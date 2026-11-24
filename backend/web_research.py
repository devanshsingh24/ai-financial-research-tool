"""Answer from FAISS first; fall back to ONE confirmed Tavily web search.

Graph:
    retrieve_scored -> draft --can_answer--> END                       (0 credits)
                         \--else--> ask_permission (interrupt)
                                      |--no--> unverified -> END
                                      \--yes-> plan_search -> web_search -> final_answer -> END

There is no edge back into plan_search/web_search, and web_search also refuses to run
if a search was already used, so each question costs at most 1 Tavily credit.
State is checkpointed in SQLite so the interrupt survives between /ask and /confirm.
"""

import json
import logging
import re
import sqlite3
import uuid
from datetime import date
from typing import Any, Dict, Iterator, List, Optional, Tuple, TypedDict

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_ollama import OllamaLLM
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from . import rag, web_cache
from .config import (
    DATA_DIR,
    LLM_MODEL,
    LLM_TEMPERATURE,
    MAX_DISTANCE,
    REPO_ROOT,
    WEB_MAX_RESULTS,
    WEB_SEARCH_DEPTH,
)

log = logging.getLogger("finsight.web")

TOPICS = {"general", "news", "finance"}
TIME_RANGES = {"day", "week", "month", "year"}
CONFIRM_PROMPT = "Search the web? (1 credit) y/n"
UNVERIFIED_NOTE = "⚠ Not verified with a web search — this may be outdated or incomplete."


class ResearchState(TypedDict, total=False):
    question: str
    documents: List[str]
    sources: List[str]
    best_distance: float
    draft: str
    can_answer: bool
    approved: bool
    search: Dict[str, Any]  # {query, topic, time_range}
    web_results: List[Dict[str, str]]
    from_cache: bool
    search_error: Optional[str]
    searches_used: int
    answer: str
    citations: List[str]


# Same model as the existing pipeline, but constrained to emit JSON.
json_llm = OllamaLLM(model=LLM_MODEL, temperature=LLM_TEMPERATURE, format="json")

draft_prompt = ChatPromptTemplate.from_template(
    """You are a precise financial research assistant. Today is {today}.
Answer the question using ONLY the context below.

Reply with a JSON object with exactly these keys:
  "can_answer": true or false
  "answer": your best answer as a string (a short draft even if can_answer is false)

Set "can_answer" to false if ANY of these is true:
- the question needs real-time or recent information (current/today's prices, "latest", "now", recent news, this week/month/year)
- the context does not clearly contain the answer
- you are not confident

Context:
{context}

Question: {question}"""
)

plan_prompt = ChatPromptTemplate.from_template(
    """Today is {today}. Plan exactly ONE web search to answer the question below.

Reply with a JSON object with exactly these keys:
  "query": a concise search-engine query (max 12 words)
  "topic": one of "general", "news", "finance"
  "time_range": one of "day", "week", "month", "year", or null if recency does not matter

Question: {question}"""
)

final_prompt = ChatPromptTemplate.from_template(
    """You are a precise financial research assistant. Today is {today}.
Answer the question using the web results and local context below. Prefer the web results
for anything recent. Cite web results inline as [1], [2], ... matching their numbers.
Do not invent numbers that are not in the sources. Keep the answer concise.

Web results:
{web}

Local context:
{context}

Question: {question}

Answer:"""
)


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse an LLM JSON reply; tolerate surrounding prose. Returns None on failure."""
    for candidate in (text, *re.findall(r"\{.*\}", text or "", flags=re.DOTALL)):
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except (TypeError, ValueError):
            continue
    return None


# Deterministic guards: a small local model often says can_answer=true while its own
# answer admits it can't. These can only force can_answer=false, never true.
_RECENCY = re.compile(
    r"\b(today|tonight|yesterday|now|right now|currently|current|latest|recent(ly)?|live|"
    r"this (week|month|quarter|year)|last (week|month)|so far|real[- ]?time|breaking|"
    r"as of|this morning|upcoming)\b",
    re.IGNORECASE,
)
_HEDGING = re.compile(
    r"(not (possible|able) to (determine|say|answer)|cannot (determine|be determined|answer|say)|"
    r"can't (determine|answer|say)|(is|are) not (specified|mentioned|provided|available|included)|"
    r"(does|do) not (contain|mention|provide|include|specify)|no (information|data) (about|on)|"
    r"not (in|within) the (provided )?context|insufficient (information|context))",
    re.IGNORECASE,
)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"y", "yes", "true", "1"}


def _today() -> str:
    return date.today().isoformat()


def _writer():
    """LangGraph's custom stream writer; a no-op outside a graph run (e.g. unit tests)."""
    try:
        return get_stream_writer()
    except RuntimeError:
        return lambda _chunk: None


class _AnswerFieldStreamer:
    """Incrementally extract the "answer" string value from streamed JSON text.

    feed(chunk) returns the newly decoded characters of the answer (possibly "").
    Handles escapes (\\n, \\", \\\\, \\uXXXX) split across chunks. Best effort only:
    the authoritative draft is always taken from the fully parsed JSON afterwards.
    """

    _START = re.compile(r'"answer"\s*:\s*"')
    _SIMPLE = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}

    def __init__(self) -> None:
        self._raw = ""
        self._pos = -1  # index in _raw where the answer string's content begins; -1 = not found yet
        self._done = False

    def feed(self, chunk: str) -> str:
        if self._done:
            return ""
        self._raw += chunk
        if self._pos < 0:
            match = self._START.search(self._raw)
            if not match:
                return ""
            self._pos = match.end()
        out = []
        i, raw = self._pos, self._raw
        while i < len(raw):
            ch = raw[i]
            if ch == '"':
                self._done = True
                i += 1
                break
            if ch == "\\":
                if i + 1 >= len(raw):
                    break  # escape split across chunks: wait for more
                esc = raw[i + 1]
                if esc == "u":
                    if i + 6 > len(raw):
                        break
                    try:
                        out.append(chr(int(raw[i + 2:i + 6], 16)))
                    except ValueError:
                        pass
                    i += 6
                    continue
                out.append(self._SIMPLE.get(esc, esc))
                i += 2
                continue
            out.append(ch)
            i += 1
        self._pos = i
        return "".join(out)


# ---------------------------------------------------------------- nodes


def retrieve_scored(state: ResearchState) -> ResearchState:
    documents, sources, best = rag.search_with_scores(state["question"])
    _writer()({"type": "sources", "sources": list(dict.fromkeys(sources))})
    return {"documents": documents, "sources": sources, "best_distance": best, "searches_used": 0}


def draft(state: ResearchState) -> ResearchState:
    # Stream the "answer" field to the client as it is generated; decide afterwards.
    writer = _writer()
    extractor = _AnswerFieldStreamer()
    pieces: List[str] = []
    for chunk in (draft_prompt | json_llm | StrOutputParser()).stream(
        {"today": _today(), "context": "\n\n".join(state["documents"]), "question": state["question"]}
    ):
        pieces.append(chunk)
        text = extractor.feed(chunk)
        if text:
            writer({"type": "token", "text": text})
    raw = "".join(pieces)
    parsed = _parse_json(raw)
    if parsed is None:
        log.warning("Draft was not valid JSON; treating as can_answer=false")
        answer, can_answer = (raw or "").strip(), False
    else:
        answer = str(parsed.get("answer") or "").strip()
        can_answer = _as_bool(parsed.get("can_answer", False)) and bool(answer)
    if can_answer and state["best_distance"] > MAX_DISTANCE:
        log.info("Weak FAISS match (distance %.3f > %.2f); forcing can_answer=false",
                 state["best_distance"], MAX_DISTANCE)
        can_answer = False
    if can_answer and _RECENCY.search(state["question"]):
        log.info("Question asks for recent/real-time info; forcing can_answer=false")
        can_answer = False
    if can_answer and _HEDGING.search(answer):
        log.info("Draft admits it can't answer; forcing can_answer=false")
        can_answer = False
    return {"draft": answer or "No answer could be drafted from the indexed sources.",
            "can_answer": can_answer}


def ask_permission(state: ResearchState) -> ResearchState:
    decision = interrupt({"prompt": CONFIRM_PROMPT, "draft": state["draft"]})
    return {"approved": _as_bool(decision)}


def unverified(state: ResearchState) -> ResearchState:
    return {"answer": f"{state['draft']}\n\n{UNVERIFIED_NOTE}",
            "citations": list(dict.fromkeys(state["sources"]))}


def plan_search(state: ResearchState) -> ResearchState:
    parsed = None
    try:
        parsed = _parse_json(
            (plan_prompt | json_llm | StrOutputParser()).invoke(
                {"today": _today(), "question": state["question"]}
            )
        )
    except Exception as exc:  # LLM failure: fall back to defaults, still one search
        log.warning("Search planning failed: %s", exc)
    parsed = parsed or {}
    query = str(parsed.get("query") or "").strip() or state["question"]
    topic = parsed.get("topic") if parsed.get("topic") in TOPICS else "general"
    time_range = parsed.get("time_range") if parsed.get("time_range") in TIME_RANGES else None
    when = f" · past {time_range}" if time_range else ""
    _writer()({"type": "status", "text": f"Searching the web: “{query}” · {topic}{when}"})
    return {"search": {"query": query, "topic": topic, "time_range": time_range}}


def _tavily(query: str, topic: str, time_range: Optional[str]) -> List[Dict[str, str]]:
    from dotenv import load_dotenv
    from langchain_tavily import TavilySearch  # lazy: server starts without a key

    load_dotenv(REPO_ROOT / ".env")  # pick up a key added while the server is running
    tool = TavilySearch(max_results=WEB_MAX_RESULTS, search_depth=WEB_SEARCH_DEPTH)
    args: Dict[str, Any] = {"query": query, "topic": topic}
    if time_range:
        args["time_range"] = time_range
    raw = tool.invoke(args)
    if isinstance(raw, str):  # tool errors can come back as a string
        raise RuntimeError(raw)
    if not isinstance(raw, dict) or raw.get("error"):
        raise RuntimeError(str(raw.get("error") if isinstance(raw, dict) else raw))
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "content": (r.get("content") or "")[:1200]}
        for r in raw.get("results", [])
        if r.get("url")
    ]


def web_search(state: ResearchState) -> ResearchState:
    if state.get("searches_used", 0) >= 1:  # hard limit, belt and braces
        return {"search_error": "Search limit reached (1 per question)."}
    s = state["search"]
    cached = web_cache.get(s["query"], s["topic"], s["time_range"])
    if cached is not None:
        log.info("Tavily cache HIT (0 credits): %r topic=%s time_range=%s",
                 s["query"], s["topic"], s["time_range"])
        _writer()({"type": "status", "text": f"Using cached web results ({len(cached)}) · 0 credits"})
        return {"web_results": cached, "from_cache": True, "search_error": None, "searches_used": 1}
    try:
        results = _tavily(s["query"], s["topic"], s["time_range"])
        log.info("Tavily credit USED (1 credit): %r topic=%s time_range=%s -> %d results",
                 s["query"], s["topic"], s["time_range"], len(results))
    except Exception as exc:
        log.warning("Tavily search failed (not retried): %s", exc)
        return {"web_results": [], "from_cache": False, "search_error": str(exc)[:300], "searches_used": 1}
    web_cache.put(s["query"], s["topic"], s["time_range"], results)
    if results:
        _writer()({"type": "status", "text": f"Reading {len(results)} web results…"})
    return {"web_results": results, "from_cache": False,
            "search_error": None if results else "No web results found.", "searches_used": 1}


def final_answer(state: ResearchState) -> ResearchState:
    results = state.get("web_results") or []
    local_sources = list(dict.fromkeys(state["sources"]))
    if state.get("search_error") or not results:
        reason = state.get("search_error") or "no results"
        return {"answer": f"{state['draft']}\n\n⚠ Web search failed ({reason}); answer not verified.",
                "citations": local_sources}
    web = "\n\n".join(
        f"[{i}] {r['title']}\nURL: {r['url']}\n{r['content']}" for i, r in enumerate(results, 1)
    )
    writer = _writer()
    pieces: List[str] = []
    for piece in (final_prompt | rag.llm | StrOutputParser()).stream(
        {"today": _today(), "web": web, "context": "\n\n".join(state["documents"]),
         "question": state["question"]}
    ):
        if piece:
            pieces.append(piece)
            writer({"type": "token", "text": piece})
    answer = "".join(pieces)
    sources_list = "\n".join(f"[{i}] {r['url']}" for i, r in enumerate(results, 1))
    return {"answer": f"{answer.strip()}\n\nSources:\n{sources_list}",
            "citations": list(dict.fromkeys([r["url"] for r in results] + local_sources))}


# ---------------------------------------------------------------- graph


def _after_draft(state: ResearchState) -> str:
    return END if state["can_answer"] else "ask_permission"


def _after_permission(state: ResearchState) -> str:
    return "plan_search" if state.get("approved") else "unverified"


graph = StateGraph(ResearchState)
for name, fn in [("retrieve_scored", retrieve_scored), ("draft", draft),
                 ("ask_permission", ask_permission), ("unverified", unverified),
                 ("plan_search", plan_search), ("web_search", web_search),
                 ("final_answer", final_answer)]:
    graph.add_node(name, fn)
graph.set_entry_point("retrieve_scored")
graph.add_edge("retrieve_scored", "draft")
graph.add_conditional_edges("draft", _after_draft, ["ask_permission", END])
graph.add_conditional_edges("ask_permission", _after_permission, ["plan_search", "unverified"])
graph.add_edge("unverified", END)
graph.add_edge("plan_search", "web_search")
graph.add_edge("web_search", "final_answer")
graph.add_edge("final_answer", END)

DATA_DIR.mkdir(parents=True, exist_ok=True)
checkpointer = SqliteSaver(sqlite3.connect(DATA_DIR / "checkpoints.sqlite", check_same_thread=False))
research_app = graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------- API helpers


class ThreadNotFound(Exception):
    pass


class NotAwaitingConfirmation(Exception):
    pass


def _config(thread_id: str) -> Dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


def _response(thread_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value
        return {"status": "needs_confirmation", "thread_id": thread_id,
                "prompt": payload.get("prompt", CONFIRM_PROMPT), "draft": payload.get("draft", "")}
    if result.get("can_answer"):
        return {"status": "answered", "thread_id": thread_id, "answer": result["draft"],
                "sources": list(dict.fromkeys(result.get("sources", []))), "web_search": None}
    search = result.get("search")
    web_search_info = None
    if result.get("approved") and search:
        web_search_info = {**search, "used": True, "from_cache": bool(result.get("from_cache")),
                           "error": result.get("search_error")}
    return {"status": "answered", "thread_id": thread_id, "answer": result.get("answer", ""),
            "sources": result.get("citations", []), "web_search": web_search_info}


def ask(question: str) -> Dict[str, Any]:
    thread_id = uuid.uuid4().hex
    result = research_app.invoke({"question": question}, _config(thread_id))
    return _response(thread_id, result)


def _check_awaiting(thread_id: str) -> None:
    snapshot = research_app.get_state(_config(thread_id))
    if not snapshot.values:
        raise ThreadNotFound(thread_id)
    if "ask_permission" not in (snapshot.next or ()):
        raise NotAwaitingConfirmation(thread_id)


def confirm(thread_id: str, approve: bool) -> Dict[str, Any]:
    _check_awaiting(thread_id)
    result = research_app.invoke(Command(resume=approve), _config(thread_id))
    return _response(thread_id, result)


# ---------------------------------------------------------------- streaming API helpers
# Events: ("sources", [urls]), ("token", text), ("status", text), then exactly one of
# ("needs_confirmation", {...}) or ("answered", {...}) — same payloads as /ask and /confirm.


def _stream(graph_input: Any, thread_id: str) -> Iterator[Tuple[str, Any]]:
    config = _config(thread_id)
    for mode, chunk in research_app.stream(graph_input, config, stream_mode=["custom", "updates"]):
        if mode == "custom":
            kind = chunk.get("type")
            if kind == "token":
                yield "token", chunk["text"]
            elif kind == "sources":
                yield "sources", chunk["sources"]
            elif kind == "status":
                yield "status", chunk["text"]
        elif mode == "updates" and "__interrupt__" in chunk:
            payload = _response(thread_id, {"__interrupt__": chunk["__interrupt__"]})
            yield "needs_confirmation", payload
            return
    yield "answered", _response(thread_id, research_app.get_state(config).values)


def stream_ask(question: str) -> Iterator[Tuple[str, Any]]:
    yield from _stream({"question": question}, uuid.uuid4().hex)


def check_confirmable(thread_id: str) -> None:
    """Raise ThreadNotFound / NotAwaitingConfirmation before a stream starts."""
    _check_awaiting(thread_id)


def stream_confirm(thread_id: str, approve: bool) -> Iterator[Tuple[str, Any]]:
    yield from _stream(Command(resume=approve), thread_id)
