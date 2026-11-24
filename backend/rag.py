"""Indexing and the LangGraph retrieve -> generate pipeline.

The active retriever is module-private state. Use the functions below rather than
importing it directly, because `/api/process` replaces it at runtime.
"""

from typing import List, Optional, Tuple, TypedDict

from langchain_community.document_loaders import WebBaseLoader
from langchain_community.vectorstores import FAISS
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.vectorstores import VectorStoreRetriever
from langchain_ollama import OllamaEmbeddings, OllamaLLM
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.graph import END, StateGraph

from .config import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    EMBED_MODEL,
    INDEX_PATH,
    LLM_MODEL,
    LLM_TEMPERATURE,
    SEPARATORS,
    TOP_K,
)


class RAGState(TypedDict):
    question: str
    documents: List[str]
    sources: List[str]
    answer: str


llm = OllamaLLM(model=LLM_MODEL, temperature=LLM_TEMPERATURE)
embeddings = OllamaEmbeddings(model=EMBED_MODEL)
_retriever: Optional[VectorStoreRetriever] = None

prompt = ChatPromptTemplate.from_template(
    """You are a precise financial research assistant. Answer only from the supplied context. If the context does not support an answer, say so clearly. Keep the answer concise and do not invent market data.

Context:
{context}

Question: {question}

Answer:"""
)


def is_ready() -> bool:
    return _retriever is not None


def load_saved_index() -> None:
    """Make a previously-created local collection available after a server restart."""
    global _retriever
    if INDEX_PATH.exists():
        store = FAISS.load_local(
            str(INDEX_PATH), embeddings, allow_dangerous_deserialization=True
        )
        _retriever = store.as_retriever(search_kwargs={"k": TOP_K})


def build_index(urls: List[str]) -> int:
    """Fetch, chunk, embed and save the URLs, replacing the current index.

    Returns the number of chunks indexed.
    """
    global _retriever
    documents = WebBaseLoader(web_paths=tuple(urls)).load()
    if not documents:
        raise ValueError("No readable content was found at the supplied URLs.")
    chunks = RecursiveCharacterTextSplitter(
        separators=SEPARATORS, chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP
    ).split_documents(documents)
    store = FAISS.from_documents(chunks, embeddings)
    store.save_local(str(INDEX_PATH))
    _retriever = store.as_retriever(search_kwargs={"k": TOP_K})
    return len(chunks)


def retrieve(state: RAGState) -> RAGState:
    if _retriever is None:
        raise ValueError("No research collection has been indexed yet.")
    docs = _retriever.invoke(state["question"])
    return {
        **state,
        "documents": [doc.page_content for doc in docs],
        "sources": [doc.metadata.get("source", "Unknown source") for doc in docs],
    }


def generate(state: RAGState) -> RAGState:
    answer = (prompt | llm | StrOutputParser()).invoke(
        {"context": "\n\n".join(state["documents"]), "question": state["question"]}
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
