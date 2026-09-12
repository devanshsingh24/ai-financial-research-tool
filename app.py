"""Local API for the FinSight Next.js dashboard.

Run with: uvicorn app:app --reload --port 8000
"""

from pathlib import Path
from typing import List, TypedDict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl
from langchain_community.document_loaders import WebBaseLoader
from langchain_community.vectorstores import FAISS
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import OllamaEmbeddings, OllamaLLM
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langgraph.graph import END, StateGraph

INDEX_PATH = Path("faiss_index")


class RAGState(TypedDict):
    question: str
    documents: List[str]
    sources: List[str]
    answer: str


class ProcessRequest(BaseModel):
    urls: List[HttpUrl] = Field(min_length=1, max_length=10)


class QueryRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1500)


class ProcessResponse(BaseModel):
    message: str
    indexed_documents: int
    sources: List[str]


class QueryResponse(BaseModel):
    answer: str
    sources: List[str]


app = FastAPI(title="FinSight Research API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

llm = OllamaLLM(model="llama3", temperature=0.2)
embeddings = OllamaEmbeddings(model="nomic-embed-text")
retriever = None

prompt = ChatPromptTemplate.from_template(
    """You are a precise financial research assistant. Answer only from the supplied
context. If the context does not support an answer, say so clearly. Keep the answer
concise and do not invent market data.

Context:
{context}

Question: {question}

Answer:"""
)


def retrieve(state: RAGState) -> RAGState:
    if retriever is None:
        raise ValueError("No research collection has been indexed yet.")
    docs = retriever.invoke(state["question"])
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


@app.on_event("startup")
def load_saved_index() -> None:
    """Make a previously-created local collection available after a server restart."""
    global retriever
    if INDEX_PATH.exists():
        store = FAISS.load_local(
            str(INDEX_PATH), embeddings, allow_dangerous_deserialization=True
        )
        retriever = store.as_retriever(search_kwargs={"k": 4})


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "collection_ready": retriever is not None}


@app.post("/api/process", response_model=ProcessResponse)
def process_urls(payload: ProcessRequest) -> ProcessResponse:
    global retriever
    urls = [str(url) for url in payload.urls]
    try:
        documents = WebBaseLoader(web_paths=tuple(urls)).load()
        if not documents:
            raise ValueError("No readable content was found at the supplied URLs.")
        chunks = RecursiveCharacterTextSplitter(
            separators=["\n\n", "\n", ".", ","], chunk_size=1000, chunk_overlap=200
        ).split_documents(documents)
        store = FAISS.from_documents(chunks, embeddings)
        store.save_local(str(INDEX_PATH))
        retriever = store.as_retriever(search_kwargs={"k": 4})
        return ProcessResponse(
            message="Research collection is ready.", indexed_documents=len(chunks), sources=urls
        )
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not process URLs: {exc}") from exc


@app.post("/api/query", response_model=QueryResponse)
def query(payload: QueryRequest) -> QueryResponse:
    if retriever is None:
        raise HTTPException(status_code=409, detail="Add and process at least one source first.")
    try:
        result = rag_app.invoke(
            {"question": payload.question, "documents": [], "sources": [], "answer": ""}
        )
        return QueryResponse(answer=result["answer"], sources=result["sources"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not generate research: {exc}") from exc
