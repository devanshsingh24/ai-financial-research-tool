# FinSight — Architecture

How FinSight works internally, from the browser click down to the vectors on disk. The diagrams are [Mermaid](https://mermaid.js.org/) and render on GitHub and in VS Code (with a Mermaid preview extension).

---

## 1. System overview

FinSight is three local processes talking over HTTP on `localhost`. Nothing leaves your machine except the one-time fetch of the article URLs you supply.

```mermaid
flowchart LR
    User([User in browser])

    subgraph FE["Next.js dev server :3000"]
        Page["frontend/app/page.tsx<br/>Dashboard (client component)"]
        Layout["frontend/app/layout.tsx + globals.css"]
    end

    subgraph BE["FastAPI / Uvicorn :8000  (backend/)"]
        API["main.py endpoints<br/>/health · /api/process · /api/query"]
        Graph["rag.py LangGraph<br/>retrieve → generate"]
        Ret[("_retriever<br/>(rag.py module state)")]
    end

    subgraph OL["Ollama :11434"]
        Emb["nomic-embed-text<br/>(embeddings)"]
        LLM["llama3<br/>(generation)"]
    end

    Disk[("backend/faiss_index/<br/>index.faiss + index.pkl")]
    Web[(Public news sites)]

    User --> Page
    Layout --- Page
    Page -- "fetch() JSON" --> API
    API --> Graph
    Graph --> Ret
    API -- "WebBaseLoader (HTTP GET)" --> Web
    API -- embed chunks --> Emb
    Ret -- embed question --> Emb
    Graph -- prompt --> LLM
    API <-- "save_local / load_local" --> Disk
```

| Layer | Technology | Lives in | Role |
|---|---|---|---|
| UI | Next.js 15 (App Router), React 19, TypeScript | `frontend/app/` | Collects URLs and questions, shows answers and sources |
| API | FastAPI + Uvicorn, Pydantic | `backend/main.py`, `backend/schemas.py` | Validates requests, calls ingestion and the RAG graph |
| Orchestration | LangChain, LangGraph | `backend/rag.py` | Loader, splitter, prompt, `retrieve → generate` state machine |
| Settings | Python constants | `backend/config.py` | Model names, chunking, top-k, index path, CORS |
| Vector store | FAISS (`faiss-cpu`) | memory + `backend/faiss_index/` | Similarity search over chunk embeddings |
| Models | Ollama (`nomic-embed-text`, `llama3`) | separate Ollama service | Embeddings and answer generation |

---

## 2. Startup sequence

```mermaid
sequenceDiagram
    autonumber
    participant U as uvicorn
    participant A as backend (package import)
    participant D as backend/faiss_index/
    participant O as Ollama

    U->>A: import backend.main (pulls in config, schemas, rag)
    A->>A: rag.py: OllamaLLM("llama3", temp 0.2), OllamaEmbeddings("nomic-embed-text")
    Note over A,O: Only client objects are created. Ollama is not contacted yet.
    A->>A: rag.py: build prompt template, compile LangGraph: retrieve → generate → END
    A->>A: main.py: create FastAPI app (with lifespan) + CORS (allow http://localhost:3000)
    U->>A: lifespan startup → rag.load_saved_index()
    alt backend/faiss_index/ exists
        A->>D: FAISS.load_local(..., allow_dangerous_deserialization=True)
        D-->>A: vectors + docstore
        A->>A: _retriever = store.as_retriever(k=4)
    else no saved index
        A->>A: _retriever stays None
    end
    U-->>U: listening on :8000
```

Key points:

- **Everything is module-level in `rag.py`.** `llm`, `embeddings`, `prompt`, `rag_app` (the compiled graph) and `_retriever` are created once per process and shared by all requests.
- **`_retriever` is the only mutable state.** It is `None` until an index is loaded or built. The endpoints reach it only through `rag.is_ready()`, `rag.build_index()` and `rag.answer()`, never by importing it, because `/api/process` replaces it.
- **`INDEX_PATH` is anchored to `backend/config.py`** (`Path(__file__).resolve().parent / "faiss_index"`), so the saved index is found whatever directory Uvicorn is started from.
- **`index.pkl` is a pickle**, which is why loading needs `allow_dangerous_deserialization=True`. Only load indexes you created yourself.

---

## 3. Flow A — building a research collection (`POST /api/process`)

### 3.1 Request path

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant P as page.tsx
    participant F as FastAPI
    participant W as News sites
    participant S as Text splitter
    participant O as Ollama (nomic-embed-text)
    participant X as FAISS
    participant D as backend/faiss_index/

    User->>P: paste 1–3 URLs, click "Create research collection"
    P->>P: trim, drop empty fields; if none → notice, stop
    P->>F: POST /api/process {"urls": [...]}
    Note over P,F: Browser sends a CORS preflight (OPTIONS) first
    F->>F: Pydantic: 1–10 items, each a valid HttpUrl (else 422)
    F->>W: WebBaseLoader GET each URL
    W-->>F: HTML → BeautifulSoup → plain text Documents (metadata.source = URL)
    F->>S: split_documents (1000 chars, 200 overlap)
    S-->>F: chunks
    F->>O: embed every chunk
    O-->>F: one 768-dim vector per chunk
    F->>X: FAISS.from_documents(chunks, embeddings)
    X->>D: save_local → index.faiss + index.pkl (overwrites)
    F->>F: rag._retriever = store.as_retriever(k=4)  (replaces old one)
    F-->>P: 200 {message, indexed_documents, sources}
    P-->>User: "N research passages indexed from M sources."
```

### 3.2 Inside each step

| Step | What happens | Settings |
|---|---|---|
| Validation | `ProcessRequest.urls: List[HttpUrl]`, min 1, max 10. Bad input never reaches the handler; FastAPI returns 422 with field errors. | URLs are converted back to `str` before loading |
| Loading | `WebBaseLoader(web_paths=tuple(urls)).load()` downloads each page with `requests` and strips HTML with BeautifulSoup. Each page becomes one `Document` whose `metadata["source"]` is its URL. | No JavaScript rendering, so JS-only or paywalled pages may yield little text. Logs warn that `USER_AGENT` is unset. |
| Splitting | `RecursiveCharacterTextSplitter` tries separators in order `"\n\n"`, `"\n"`, `"."`, `","` and keeps chunks ≤ 1000 characters. | 200-character overlap keeps sentences that cross a boundary retrievable. Each chunk inherits its page's `source`. |
| Embedding | `OllamaEmbeddings` sends chunk text to Ollama's embedding API; `nomic-embed-text` returns a vector per chunk. | Runs on your CPU/GPU via Ollama; this is usually the slowest step. |
| Indexing | `FAISS.from_documents` stores vectors in a flat (exact, brute-force) L2 index, and chunk text + metadata in an in-memory docstore keyed by ID. | Flat search is exact and fine for the few hundred chunks a handful of articles produce. |
| Persisting | `save_local(INDEX_PATH)` (`backend/faiss_index/`) writes `index.faiss` (the vectors) and `index.pkl` (docstore + ID mapping). | **Overwrites** the previous collection. There is no append. |
| Activation | `rag._retriever` is swapped to the new store with `k=4`. | Takes effect for the very next query. |

All of the above runs inside `rag.build_index(urls)`; the handler in `main.py` only converts the URLs to strings and wraps errors. Any exception (network failure, empty page, Ollama down) is returned as **422** `"Could not process URLs: …"`, and the old index stays in place on disk unless saving had already happened.

---

## 4. Flow B — asking a question (`POST /api/query`)

### 4.1 Request path

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant P as page.tsx
    participant F as FastAPI
    participant G as LangGraph (rag_app)
    participant R as retriever (FAISS)
    participant O1 as Ollama nomic-embed-text
    participant O2 as Ollama llama3

    User->>P: type question, submit
    P->>F: POST /api/query {"question": "..."}
    F->>F: Pydantic: 3–1500 chars (else 422)
    alt not rag.is_ready()
        F-->>P: 409 "Add and process at least one source first."
    else index ready
        F->>G: rag.answer(question) → invoke({question, documents: [], sources: [], answer: ""})
        G->>R: retrieve node: retriever.invoke(question)
        R->>O1: embed question
        O1-->>R: query vector
        R->>R: L2 nearest-neighbour search, top 4 chunks
        R-->>G: documents (chunk text) + sources (URL per chunk)
        G->>O2: generate node: prompt(context = 4 chunks joined, question)
        O2-->>G: answer text (temperature 0.2)
        G->>G: de-duplicate sources, keep order
        G-->>F: final RAGState
        F-->>P: 200 {answer, sources}
        P-->>User: SYNTHESIS + EVIDENCE links (hostnames)
    end
```

### 4.2 The LangGraph state machine

```mermaid
stateDiagram-v2
    direction LR
    [*] --> retrieve: question
    retrieve --> generate: + documents, sources
    generate --> [*]: + answer, deduped sources
```

The graph state is a `TypedDict`:

```python
class RAGState(TypedDict):
    question: str        # set by the endpoint
    documents: List[str] # filled by retrieve: page_content of top-4 chunks
    sources: List[str]   # filled by retrieve: metadata["source"] per chunk
    answer: str          # filled by generate
```

Each node receives the full state and returns a new full state (`{**state, ...}`).

- **`retrieve`** raises if `_retriever` is `None` (a second guard behind the endpoint's 409 check), then runs the similarity search.
- **`generate`** builds the chain `prompt | llm | StrOutputParser()`, fills `{context}` with the four chunks separated by blank lines, and stores the plain-text answer. `dict.fromkeys(...)` removes duplicate URLs while keeping their order, because several of the top chunks often come from the same article.

The graph is linear today, but because it's a LangGraph `StateGraph`, adding steps (query rewriting, re-ranking, a "no answer" branch, tool calls) means adding nodes and edges rather than rewriting the endpoint.

### 4.3 The prompt

```
You are a precise financial research assistant. Answer only from the supplied
context. If the context does not support an answer, say so clearly. Keep the answer
concise and do not invent market data.

Context:
{context}        ← 4 retrieved chunks (≈ up to 4,000 characters)

Question: {question}

Answer:
```

The model only sees these four chunks, never the full articles, so answer quality depends heavily on retrieval. Errors in the graph (for example Ollama not running) return **500** `"Could not generate research: …"`.

### 4.4 Streaming variant (`POST /api/query/stream`), used by the dashboard

```mermaid
sequenceDiagram
    autonumber
    participant P as page.tsx
    participant F as main.py
    participant R as rag.stream_answer
    participant O as Ollama llama3

    P->>F: POST /api/query/stream {"question": "..."}
    alt not rag.is_ready()
        F-->>P: 409 JSON (before any streaming)
    else
        F-->>P: 200 text/event-stream (headers sent)
        F->>R: iterate
        R->>R: retrieve node (top-4 chunks)
        R-->>P: event: sources  data: [urls]
        R->>O: chain.stream(prompt)
        loop each generated piece
            O-->>R: text piece
            R-->>P: event: token  data: "piece"
        end
        F-->>P: event: done  data: {}
    end
```

- Each event is `event: <name>\ndata: <json>\n\n`. JSON-encoding the data keeps newlines inside tokens from breaking the framing.
- The status is already 200 once streaming starts, so a failure mid-stream (for example Ollama stopping) is sent as `event: error` with `{"detail": "Could not generate research: …"}`.
- `stream_answer` reuses the `retrieve` node and the shared `chain` (`prompt | llm | StrOutputParser()`) but doesn't run the compiled graph, because it needs `chain.stream` rather than `invoke`. Any node added to the graph later must also be added here if it should apply to streamed answers.
- The handler is a sync generator, so Starlette runs it in a thread pool and the event loop stays free.
- `/api/query` (non-streaming JSON) is kept for scripts and other clients.

---

## 5. Frontend internals (`frontend/app/`)

```mermaid
flowchart TD
    subgraph Layout["layout.tsx (server component)"]
        Meta["metadata: title, description"]
        CSS["imports globals.css"]
    end
    subgraph Page["page.tsx — Dashboard ('use client')"]
        State["useState:<br/>urls[3] · question · isIndexing · isResearching · notice · result"]
        Ingest["ingestSources()<br/>POST /api/process"]
        Ask["askResearch()<br/>POST /api/query/stream (reads SSE)"]
        UI1["Research collection form<br/>3 URL inputs"]
        UI2["Research terminal<br/>textarea + notice + answer"]
    end
    Layout --> Page
    UI1 -- submit --> Ingest
    UI2 -- submit --> Ask
    Ingest -- "notice, clears result" --> State
    Ask -- "result {answer, sources}" --> State
    State --> UI1
    State --> UI2
```

- **One client component.** All logic is in `Dashboard`; there are no API routes, server actions or shared components. `layout.tsx` only sets metadata and global CSS.
- **API base URL** is `process.env.NEXT_PUBLIC_API_URL`, defaulting to `http://localhost:8000`. The browser calls FastAPI directly, which is why the backend needs CORS for `localhost:3000`.
- **UI state:**
  - `isIndexing` / `isResearching` disable the buttons and change their labels during requests.
  - `notice` is a single status line used for progress, success and error messages; on failure it shows the backend's `detail`.
  - `isStreaming` shows a blinking `.cursor` after the answer text while tokens are arriving.
  - `result` holds the current answer. It starts empty, sources fill in on the `sources` event, and each `token` event is appended. The answer paragraph uses `white-space: pre-wrap`, so the model's line breaks show. Sources render as links labelled by hostname (`new URL(source).hostname`, `www.` removed).
- **Static sections** (hero, "On the horizon" roadmap cards, footer) are presentational only.
- The UI shows three URL fields, while the API accepts up to ten.

---

## 6. Data on disk

```mermaid
flowchart LR
    subgraph IDX["backend/faiss_index/"]
        IF["index.faiss<br/>FAISS flat L2 index<br/>N × 768 float32 vectors"]
        IP["index.pkl (pickle)<br/>• InMemoryDocstore: id → Document(text, {source})<br/>• index_to_docstore_id: row → id"]
    end
    Q["query vector"] -- "nearest rows" --> IF
    IF -- "row numbers" --> IP
    IP -- "chunk text + source URL" --> R["retrieved documents"]
```

FAISS itself only stores numbers. LangChain keeps the mapping from each vector row to its chunk text and URL in `index.pkl`; both files are needed to answer a query.

---

## 7. Error handling and status codes

| Situation | Where caught | Status | Message shown in UI |
|---|---|---|---|
| Invalid URL / too many URLs / question too short or long | Pydantic models | 422 | FastAPI validation detail |
| Page fetch fails, page empty, embedding fails | `/api/process` `except` | 422 | `Could not process URLs: …` |
| Query before any collection exists | `/api/query` guard | 409 | `Add and process at least one source first.` |
| Ollama down or generation error | `/api/query` `except` | 500 | `Could not generate research: …` |
| API not running | browser `fetch` throws | — | `Could not reach the research API.` |

`GET /health` returns `{"status": "ok", "collection_ready": <bool>}` for a quick check.

---

## 8. Design trade-offs and limits

- **Single collection.** Each `/api/process` call replaces the index; there are no collections per user or topic.
- **Single-user assumption.** FastAPI runs these sync handlers in a thread pool, and `rag._retriever` is swapped without a lock. Fine for one local user, not for a shared server.
- **Streaming, but not cancellable.** The dashboard streams tokens as `llama3` writes them, but there is no stop button. Closing the page doesn't necessarily stop generation on the server right away.
- **Exact search.** Flat L2 search is accurate and fast at this scale; a large corpus would need an approximate FAISS index (IVF/HNSW).
- **Fixed retrieval.** `k=4`, 1000-character chunks and temperature 0.2 are constants in `backend/config.py` (no env-var or per-request overrides).
- **Prompt-only grounding.** The prompt tells the model to answer only from the context and not invent market data, but nothing enforces it; `llama3` can still add outside knowledge when retrieved chunks are off-topic.
- **Local-only index.** `backend/faiss_index/` is git-ignored, so a fresh clone starts with no collection until URLs are processed.

---

## 9. Supporting pieces (not in the runtime path)

| Path | Purpose |
|---|---|
| `notebooks/rag.ipynb` | Original prototype of the same pipeline in a notebook. Uses `UnstructuredURLLoader` and `llama3` at temperature 0.9, and writes its own `notebooks/faiss_index/`. Not imported by the app. |
| `Presentation/` | College deck (`FinSight_College_Presentation.pptx`), the script that generates it (`generate_deck.py`) and an image. |
| `docs/postcleanup.md` | Record of removed/moved files and known issues. |
| `CLAUDE.md` | Guidance for Claude Code working in this repo. |

---

## 10. Run-time lifecycle summary

```mermaid
flowchart TD
    A["ollama serve<br/>(models pulled)"] --> B["uvicorn backend.main:app --port 8000"]
    B --> C{"backend/faiss_index/ exists?"}
    C -- yes --> D["retriever loaded<br/>/health: collection_ready = true"]
    C -- no --> E["retriever = None<br/>/health: collection_ready = false"]
    F["frontend/: npm run dev → localhost:3000"] --> G["User pastes URLs"]
    E --> G
    D --> H
    G --> I["/api/process<br/>fetch → split → embed → index → save"]
    I --> H["User asks question"]
    H --> J["/api/query<br/>embed question → top-4 search → llama3 → answer + sources"]
    J --> H
    H -. "new sources" .-> G
```
