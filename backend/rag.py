"""Indexing and the LangGraph retrieve -> generate pipeline.

The active retriever is module-private state. Use the functions below rather than
importing it directly, because `/api/process` replaces it at runtime.

On disk, each collection is a version directory INDEX_PATH/v<time>/ (index.faiss,
index.pkl, meta.json), and INDEX_PATH/CURRENT names the active one. A new collection is
written to a fresh directory and CURRENT is swapped atomically, so with several uvicorn
workers nobody reads a half-written index, and every worker notices the new CURRENT on its
next request and reloads. An older flat layout (files directly in INDEX_PATH) still loads.
"""

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Iterator, List, Optional, Tuple, TypedDict

from langchain_community.document_loaders import WebBaseLoader
from langchain_community.vectorstores import FAISS
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.graph import END, StateGraph

from . import llm as models
from .config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    INDEX_PATH,
    SEPARATORS,
    TOP_K,
)

log = logging.getLogger("finsight.rag")


class RAGState(TypedDict):
    question: str
    documents: List[str]
    sources: List[str]
    answer: str


# Ollama or Groq + Hugging Face embeddings, depending on LLM_PROVIDER (see llm.py).
llm = models.make_llm()
embeddings = models.make_embeddings()
_retriever: Optional[VectorStoreRetriever] = None
_store: Optional[FAISS] = None  # same index as _retriever; used for scored search
index_problem: Optional[str] = None  # why a saved index was not loaded (shown in /health)
_loaded_version: Optional[str] = None  # which version directory is in memory
_lock = threading.Lock()

# Records which embedding model built the index. Indexes from before this file existed
# were built with Ollama nomic-embed-text.
_META = "meta.json"
_LEGACY_META = {"provider": "ollama", "model": "nomic-embed-text", "dims": 768}
_CURRENT = "CURRENT"
_KEEP_VERSIONS = 3

prompt = ChatPromptTemplate.from_template(
    """You are a precise financial research assistant. Answer only from the supplied context. If the context does not support an answer, say so clearly. Keep the answer concise and do not invent market data.

Context:
{context}

Question: {question}

Answer:"""
)
chain = prompt | llm | StrOutputParser()


def _context(documents: List[str]) -> str:
    return "\n\n".join(documents)


def _active_version() -> Tuple[Optional[str], Optional[Path]]:
    """(version name, directory) of the collection on disk, or (None, None)."""
    pointer = INDEX_PATH / _CURRENT
    try:
        name = pointer.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        name = ""
    if name and (INDEX_PATH / name / "index.faiss").exists():
        return name, INDEX_PATH / name
    if (INDEX_PATH / "index.faiss").exists():  # flat layout from before versioning
        return "legacy", INDEX_PATH
    return None, None


def _load(version: str, directory: Path) -> None:
    global _retriever, _store, index_problem, _loaded_version
    meta_path = directory / _META
    saved = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else _LEGACY_META
    current = models.embedding_id()
    if saved != current:
        # Vectors from a different embedding model can't be searched with this one.
        index_problem = (
            f"The saved collection was built with {saved['provider']}:{saved['model']}, but this "
            f"server embeds with {current['provider']}:{current['model']}. Create the collection again."
        )
        log.warning(index_problem)
        _loaded_version = version  # don't retry on every request
        return
    store = FAISS.load_local(str(directory), embeddings, allow_dangerous_deserialization=True)
    _store = store
    _retriever = store.as_retriever(search_kwargs={"k": TOP_K})
    index_problem = None
    _loaded_version = version


def _refresh() -> None:
    """Reload if another worker (or process) published a newer collection."""
    version, directory = _active_version()
    if version is None or version == _loaded_version:
        return
    with _lock:
        if version != _loaded_version:
            log.info("Loading collection %s", version)
            _load(version, directory)


def is_ready() -> bool:
    _refresh()
    return _retriever is not None


def load_saved_index() -> None:
    """Make a previously-created local collection available after a server restart."""
    _refresh()


def build_index(urls: List[str]) -> int:
    """Fetch, chunk, embed and save the URLs, replacing the current index.

    Returns the number of chunks indexed.
    """
    documents = WebBaseLoader(web_paths=tuple(urls)).load()
    if not documents:
        raise ValueError("No readable content was found at the supplied URLs.")
    chunks = RecursiveCharacterTextSplitter(
        separators=SEPARATORS, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    ).split_documents(documents)
    publish(FAISS.from_documents(chunks, embeddings))
    return len(chunks)


def publish(store: FAISS) -> str:
    """Save `store` as a new version, make it CURRENT and use it. Returns the version."""
    global _retriever, _store, index_problem, _loaded_version
    INDEX_PATH.mkdir(parents=True, exist_ok=True)
    version = f"v{time.time_ns()}-{os.getpid()}"
    directory = INDEX_PATH / version
    store.save_local(str(directory))
    (directory / _META).write_text(json.dumps(models.embedding_id()), encoding="utf-8")
    tmp = INDEX_PATH / f".{_CURRENT}.{os.getpid()}"
    tmp.write_text(version, encoding="utf-8")
    os.replace(tmp, INDEX_PATH / _CURRENT)  # atomic: readers see the old or the new version
    with _lock:
        index_problem = None
        _store = store
        _retriever = store.as_retriever(search_kwargs={"k": TOP_K})
        _loaded_version = version
    _prune(keep=version)
    return version


def _prune(keep: str) -> None:
    """Delete old version directories (and an old flat index), keeping the newest few."""
    versions = sorted((p for p in INDEX_PATH.glob("v*") if p.is_dir()), key=lambda p: p.name, reverse=True)
    for old in versions[_KEEP_VERSIONS:]:
        if old.name != keep:
            shutil.rmtree(old, ignore_errors=True)
    for name in ("index.faiss", "index.pkl", _META):  # flat layout, superseded
        try:
            (INDEX_PATH / name).unlink()
        except FileNotFoundError:
            pass


def search_with_scores(question: str) -> Tuple[List[str], List[str], float]:
    """Top-k chunks with L2 distances (lower = closer).

    Returns (chunk texts, source URLs, best distance). Best distance is inf if empty.
    """
    _refresh()
    if _store is None:
        raise ValueError("No research collection has been indexed yet.")
    hits = _store.similarity_search_with_score(question, k=TOP_K)
    texts = [doc.page_content for doc, _ in hits]
    sources = [doc.metadata.get("source", "Unknown source") for doc, _ in hits]
    best = min((float(score) for _, score in hits), default=float("inf"))
    return texts, sources, best


def retrieve(state: RAGState) -> RAGState:
    _refresh()
    if _retriever is None:
        raise ValueError("No research collection has been indexed yet.")
    docs = _retriever.invoke(state["question"])
    return {
        **state,
        "documents": [doc.page_content for doc in docs],
        "sources": [doc.metadata.get("source", "Unknown source") for doc in docs],
    }


def generate(state: RAGState) -> RAGState:
    answer = chain.invoke(
        {"context": _context(state["documents"]), "question": state["question"]}
    )
    return {**state, "answer": answer, "sources": list(dict.fromkeys(state["sources"]))}


graph = StateGraph(RAGState)
graph.add_node("retrieve", retrieve)
graph.add_node("generate", generate)
graph.set_entry_point("retrieve")
graph.add_edge("retrieve", "generate")
graph.add_edge("generate", END)
rag_app = graph.compile()


def answer(question: str) -> Tuple[str, List[str]]:
    """Run the graph and return (answer, deduplicated source URLs)."""
    result = rag_app.invoke({"question": question, "documents": [], "sources": [], "answer": ""})
    return result["answer"], result["sources"]


def stream_answer(question: str) -> Iterator[Tuple[str, object]]:
    """Stream the answer as ("sources", [urls]) once, then ("token", text) pieces.

    Uses the same retrieve node and chain as the graph, but streams the LLM output.
    """
    state = retrieve({"question": question, "documents": [], "sources": [], "answer": ""})
    yield "sources", list(dict.fromkeys(state["sources"]))
    for piece in chain.stream({"context": _context(state["documents"]), "question": question}):
        if piece:
            yield "token", piece
