import os
import time
import streamlit as st
from langchain_ollama import OllamaLLM, OllamaEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import UnstructuredURLLoader
from langchain_community.vectorstores import FAISS
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langgraph.graph import StateGraph, END
from typing import TypedDict, List

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(page_title="RockyBot: News Research Tool", page_icon="📈", layout="wide")
st.title("AI Financial Analyst: News Research Tool 📈")
st.sidebar.title("News Article URLs")

# ── LangGraph State ─────────────────────────────────────────────────────────────
class RAGState(TypedDict):
    question: str
    documents: List[str]
    sources: List[str]
    answer: str

# ── Constants ───────────────────────────────────────────────────────────────────
FAISS_INDEX_PATH = "faiss_index"

# ── Sidebar: URL inputs ─────────────────────────────────────────────────────────
urls = []
for i in range(3):
    url = st.sidebar.text_input(f"URL {i+1}")
    urls.append(url)

process_url_clicked = st.sidebar.button("Process URLs")

# ── Sidebar: Model info ─────────────────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.markdown("**🦙 Running locally with Ollama**")
st.sidebar.markdown("- LLM: `llama3`")
st.sidebar.markdown("- Embeddings: `nomic-embed-text`")
st.sidebar.markdown("- No API key needed ✅")

# ── Main placeholder ────────────────────────────────────────────────────────────
main_placeholder = st.empty()

# ── LLM & Embeddings (initialised once) ────────────────────────────────────────
@st.cache_resource
def load_llm_and_embeddings():
    llm = OllamaLLM(model="llama3", temperature=0.9)
    embeddings = OllamaEmbeddings(model="nomic-embed-text")
    return llm, embeddings

llm, embeddings = load_llm_and_embeddings()

# ── Prompt ──────────────────────────────────────────────────────────────────────
prompt = ChatPromptTemplate.from_template("""
Answer the question based only on the following context.
Be concise and accurate. Mention the sources used at the end.

Context:
{context}

Question: {question}

Answer:
""")

# ── LangGraph nodes ─────────────────────────────────────────────────────────────
def retrieve(state: RAGState) -> RAGState:
    retriever = st.session_state.retriever
    retrieved_docs = retriever.invoke(state["question"])
    documents = [doc.page_content for doc in retrieved_docs]
    sources = [doc.metadata.get("source", "unknown") for doc in retrieved_docs]
    return {**state, "documents": documents, "sources": sources}

def generate(state: RAGState) -> RAGState:
    context = "\n\n".join(state["documents"])
    sources = list(set(state["sources"]))
    chain = prompt | llm | StrOutputParser()
    answer = chain.invoke({"context": context, "question": state["question"]})
    answer_with_sources = answer  # sources displayed separately in UI
    return {**state, "answer": answer_with_sources, "sources": sources}

# ── Build LangGraph ─────────────────────────────────────────────────────────────
@st.cache_resource
def build_graph():
    graph = StateGraph(RAGState)
    graph.add_node("retrieve", retrieve)
    graph.add_node("generate", generate)
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)
    return graph.compile()

rag_app = build_graph()

# ── Process URLs ────────────────────────────────────────────────────────────────
if process_url_clicked:
    valid_urls = [u for u in urls if u.strip()]
    if not valid_urls:
        st.sidebar.error("Please enter at least one URL.")
    else:
        with st.spinner("Loading data from URLs..."):
            main_placeholder.text("Data Loading... Started... ✅✅✅")
            loader = UnstructuredURLLoader(urls=valid_urls)
            data = loader.load()

        with st.spinner("Splitting text into chunks..."):
            main_placeholder.text("Text Splitter... Started... ✅✅✅")
            text_splitter = RecursiveCharacterTextSplitter(
                separators=['\n\n', '\n', '.', ','],
                chunk_size=1000,
                chunk_overlap=200
            )
            docs = text_splitter.split_documents(data)

        with st.spinner("Building embeddings (this may take a minute)..."):
            main_placeholder.text("Embedding Vector Started Building... ✅✅✅")
            vectorstore = FAISS.from_documents(docs, embeddings)
            vectorstore.save_local(FAISS_INDEX_PATH)
            st.session_state.retriever = vectorstore.as_retriever()
            time.sleep(1)

        main_placeholder.success("URLs processed successfully! You can now ask questions. ✅")

# ── Load FAISS index if not already in session ──────────────────────────────────
if "retriever" not in st.session_state:
    if os.path.exists(FAISS_INDEX_PATH):
        vectorstore = FAISS.load_local(
            FAISS_INDEX_PATH,
            embeddings,
            allow_dangerous_deserialization=True  # safe: file created by this app
        )
        st.session_state.retriever = vectorstore.as_retriever()

# ── Query input & response ──────────────────────────────────────────────────────
st.markdown("---")
query = st.text_input("🔍 Ask a question about the articles:")

if query:
    if "retriever" not in st.session_state:
        st.warning("Please process URLs first using the sidebar.")
    else:
        with st.spinner("Thinking..."):
            result = rag_app.invoke({
                "question": query,
                "documents": [],
                "sources": [],
                "answer": ""
            })

        st.header("Answer")
        st.write(result["answer"])

        sources = result.get("sources", [])
        if sources:
            st.subheader("Sources:")
            for source in set(sources):
                st.write(f"- {source}")