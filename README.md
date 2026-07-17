
# AI-Financial-Research-Tool
Local RAG-based AI Financial Analyst using LangGraph, Ollama & Streamlit. It is a user-friendly news research tool designed for effortless information retrieval. Users can input article URLs and ask questions to receive relevant insights from the stock market and financial domain.






![App Screenshot](https://cdn.corenexis.com/f/OEE6KQbCV3b.png)

## Demo 
Watch the 1 minute demonstration on **X**
[![X](https://img.shields.io/badge/X-black.svg?logo=X&logoColor=white)](https://x.com/DevanshSin52865/status/2056979625105137869)

## Features

- Load URLs or upload text files containing URLs to fetch article content.
- Process article content through LangChain's UnstructuredURL Loader
- Construct an embedding vector using  OllamaEmbeddings and leverage FAISS, a powerful similarity search library, to enable swift and effective retrieval of relevant information
- Interact with the LLM's (Chatgpt) by inputting queries and receiving answers along with source URLs.


## Usage/Examples

1. Run the Streamlit app by executing:

streamlit run main.py

2. The web app will open in your browser.

- On the sidebar, you can input URLs directly.

- Initiate the data loading and processing by clicking "Process URLs."

- Observe the system as it performs text splitting, generates embedding vectors, and efficiently indexes them using FAISS.

- The embeddings will be stored and indexed using FAISS, enhancing retrieval speed.

- The FAISS index will be saved in a local file path in pickle format for future use.

- One can now ask a question and get the answer based on those news articles

In rag.ipynb file, we used following news articles

    "https://www.moneycontrol.com/news/business/markets/wall-street-rises-as-tesla-soars-on-ai-optimism-11351111.html", 
    "https://www.moneycontrol.com/news/business/tata-motors-launches-punch-icng-price-starts-at-rs-7-1-lakh-11098751.html"


## Roadmap

┌─────────────────────┐
│   User Interface    │
│   (Streamlit)       │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   URL Processing    │
│  (Load & Split)     │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│   Embedding Gen     │
│   (Ollama Embed)    │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  FAISS Vector Store │
│   (Similarity)      │
└──────────┬──────────┘
           │
        ┌──┴──┐
        │     │
    ┌───▼─┐ ┌┴────────┐
    │Query│ │LLM Prompt
    └───┬─┘ └┬────────┘
        │    │
        └─┬──┘
          │
          ▼
    ┌──────────────┐
    │Answer + Src  │
    └──────────────┘


## Documentation

- LangChain Docs

- LangGraph Docs

- FAISS Documentation

- Streamlit Docs

- Ollama Documentation


## Notes

All models run locally via Ollama - no API keys needed

FAISS index is persisted to disk for reuse

Streamlit session state manages retriever across interactions

LangGraph provides deterministic workflow execution

RAG ensures answers are grounded in provided documents

## Dependencies


langchain -	LLM framework

langchain-ollama -	Ollama integration

langgraph -	Workflow orchestration

faiss-cpu -	Vector similarity search

streamlit -	Web UI framework

sentence-transformers -	Embedding models

pandas - Data manipulation

unstructured -	Document loading
