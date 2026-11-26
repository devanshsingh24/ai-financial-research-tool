r"""Answer from FAISS first; fall back to ONE confirmed Tavily web search.

Graph (one run = one question/turn of a thread):
    start_turn -> load_memories -> retrieve_scored -> draft --can_answer--> finish   (0 credits)
                                                        \--else--> plan_search -> ask_permission (interrupt: search gate)
        ask_permission --reject--> unverified -> finish
                       \--approve/edit query--> web_search -> final_answer -> review_report (interrupt: report gate) -> finish
    finish -> save_memory -> END

There is no edge back into plan_search/web_search, and web_search also refuses to run
if a search was already used, so each question costs at most 1 Tavily credit.

The graph is compiled in persistence.py with a checkpointer (state saved every superstep,
durability="sync") and a long-term memory store. State holds only plain data and messages.
"""

import json
import logging
import re
import uuid
from datetime import date
from typing import Annotated, Any, AsyncIterator, Dict, List, Optional, Tuple, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, RemoveMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command, interrupt

from . import llm as models, memory, rag, web_cache
from .config import (
    MAX_DISTANCE,
    REPO_ROOT,
    WEB_MAX_RESULTS,
    WEB_SEARCH_DEPTH,
)

log = logging.getLogger("finsight.web")

TOPICS = {"general", "news", "finance"}
TIME_RANGES = {"day", "week", "month", "year"}
CONFIRM_PROMPT = "Search the web? (1 credit) y/n"
REPORT_PROMPT = "Approve this report? (approve / reject / edit)"
UNVERIFIED_NOTE = "⚠ Not verified with a web search — this may be outdated or incomplete."
GATES = ("ask_permission", "review_report")
ACTIONS = {"approve", "reject", "edit"}


class ResearchState(TypedDict, total=False):
    # thread-level (kept across turns)
    messages: Annotated[List[AnyMessage], add_messages]
    summary: str          # summary of turns trimmed out of `messages`
    memories: List[str]   # long-term memories read at thread start
    turn: int
    # per-turn (reset by start_turn)
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
    review: str  # report gate outcome: approved | edited | rejected


# Fields cleared at the start of every turn, so a follow-up question starts clean.
_TURN_RESET: Dict[str, Any] = {
    "documents": [], "sources": [], "draft": "", "can_answer": False,
    "approved": False, "search": None, "web_results": [], "from_cache": False, "search_error": None,
    "searches_used": 0, "answer": "", "citations": [], "review": "",
}

# Same model as the existing pipeline (Ollama or Groq), but constrained to emit JSON.
json_llm = models.make_llm(json_mode=True)

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

What you know about this user from earlier conversations (use it only to shape focus and
style, never as a source of facts):
{memories}

Conversation so far (use it to understand follow-up questions):
{history}

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

What you know about this user (style and focus only, not facts):
{memories}

Conversation so far:
{history}

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


def _decision(value: Any) -> Dict[str, Any]:
    """Normalize a gate's resume value: a legacy bool (/confirm) or
    {"action": "approve"|"reject"|"edit", "query"?: str, "draft"?: str}."""
    if isinstance(value, dict):
        action = str(value.get("action", "")).strip().lower()
        if action not in ACTIONS:
            action = "approve" if _as_bool(value.get("approve", False)) else "reject"
        return {**value, "action": action}
    return {"action": "approve" if _as_bool(value) else "reject"}


def _prompt_memory(state: "ResearchState") -> Dict[str, str]:
    return {"history": memory.format_history(state), "memories": memory.format_memories(state)}


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


def start_turn(state: ResearchState) -> ResearchState:
    """Reset per-turn fields and fold old turns into the summary once the thread is long."""
    update: Dict[str, Any] = {**_TURN_RESET, "turn": state.get("turn", 0) + 1}
    summary, remove_ids = memory.summarize_old_messages(state.get("messages") or [],
                                                        state.get("summary", ""))
    if remove_ids:
        update["summary"] = summary
        update["messages"] = [RemoveMessage(id=i) for i in remove_ids]
    return update


def retrieve_scored(state: ResearchState) -> ResearchState:
    documents, sources, best = rag.search_with_scores(state["question"])
    # Logged for every question, to calibrate MAX_DISTANCE for a new embedding model.
    log.info("best FAISS distance %.3f (threshold %.2f)", best, MAX_DISTANCE)
    _writer()({"type": "sources", "sources": list(dict.fromkeys(sources))})
    return {"documents": documents, "sources": sources, "best_distance": best, "searches_used": 0}


def draft(state: ResearchState) -> ResearchState:
    # Stream the "answer" field to the client as it is generated; decide afterwards.
    writer = _writer()
    extractor = _AnswerFieldStreamer()
    pieces: List[str] = []
    for chunk in (draft_prompt | json_llm | StrOutputParser()).stream(
        {"today": _today(), "context": "\n\n".join(state["documents"]), "question": state["question"],
         **_prompt_memory(state)}
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
    """Search gate. Shows the draft and the planned query; approve, reject or edit the query."""
    decision = _decision(interrupt({"kind": "web_search", "prompt": CONFIRM_PROMPT,
                                    "draft": state["draft"], "search": state.get("search")}))
    if decision["action"] == "reject":
        return {"approved": False}
    query = str(decision.get("query") or "").strip()
    if decision["action"] == "edit" and query:
        return {"approved": True, "search": {**state["search"], "query": query[:400]}}
    return {"approved": True}


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
    # Runs BEFORE the search gate so the user can see and edit the query; nothing is spent yet.
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
    when = f" · past {s['time_range']}" if s.get("time_range") else ""
    _writer()({"type": "status", "text": f"Searching the web: “{s['query']}” · {s['topic']}{when}"})
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
         "question": state["question"], **_prompt_memory(state)}
    ):
        if piece:
            pieces.append(piece)
            writer({"type": "token", "text": piece})
    answer = "".join(pieces)
    sources_list = "\n".join(f"[{i}] {r['url']}" for i, r in enumerate(results, 1))
    return {"answer": f"{answer.strip()}\n\nSources:\n{sources_list}",
            "citations": list(dict.fromkeys([r["url"] for r in results] + local_sources))}


def review_report(state: ResearchState) -> ResearchState:
    """Report gate, before the final report is delivered: approve, reject or edit it."""
    decision = _decision(interrupt({"kind": "report", "prompt": REPORT_PROMPT,
                                    "answer": state.get("answer", ""),
                                    "sources": state.get("citations", [])}))
    if decision["action"] == "reject":
        return {**unverified(state), "review": "rejected"}
    edited = str(decision.get("draft") or "").strip()
    if decision["action"] == "edit" and edited:
        return {"answer": edited, "review": "edited"}
    return {"review": "approved"}


def finish(state: ResearchState) -> ResearchState:
    """Record the turn's answer in the thread's messages (short-term memory)."""
    text = state.get("answer") or state.get("draft", "")
    citations = state.get("citations") or list(dict.fromkeys(state.get("sources", [])))
    return {"answer": text, "citations": citations, "messages": [AIMessage(content=text)]}


# ---------------------------------------------------------------- graph


def _after_draft(state: ResearchState) -> str:
    return "finish" if state["can_answer"] else "plan_search"


def _after_permission(state: ResearchState) -> str:
    return "web_search" if state.get("approved") else "unverified"


def build_graph() -> StateGraph:
    """The uncompiled graph. persistence.py compiles it with a checkpointer and store."""
    graph = StateGraph(ResearchState)
    for name, fn in [("start_turn", start_turn), ("load_memories", memory.load_memories),
                     ("retrieve_scored", retrieve_scored), ("draft", draft),
                     ("plan_search", plan_search), ("ask_permission", ask_permission),
                     ("unverified", unverified), ("web_search", web_search),
                     ("final_answer", final_answer), ("review_report", review_report),
                     ("finish", finish), ("save_memory", memory.save_memory)]:
        graph.add_node(name, fn)
    graph.set_entry_point("start_turn")
    graph.add_edge("start_turn", "load_memories")
    graph.add_edge("load_memories", "retrieve_scored")
    graph.add_edge("retrieve_scored", "draft")
    graph.add_conditional_edges("draft", _after_draft, ["plan_search", "finish"])
    graph.add_edge("plan_search", "ask_permission")
    graph.add_conditional_edges("ask_permission", _after_permission, ["web_search", "unverified"])
    graph.add_edge("unverified", "finish")
    graph.add_edge("web_search", "final_answer")
    graph.add_edge("final_answer", "review_report")
    graph.add_edge("review_report", "finish")
    graph.add_edge("finish", "save_memory")
    graph.add_edge("save_memory", END)
    return graph


# ---------------------------------------------------------------- API helpers


class ThreadNotFound(Exception):
    pass


class NotAwaitingConfirmation(Exception):
    pass


def new_thread_id() -> str:
    return uuid.uuid4().hex


def _app():
    from . import persistence  # late: persistence imports this module

    return persistence.runtime().app


def thread_config(thread_id: str, user_id: str, checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
    configurable = {"thread_id": thread_id, "user_id": user_id}
    if checkpoint_id:
        # checkpoint_ns "" = the root graph; update_state at a past checkpoint requires it.
        configurable.update(checkpoint_ns="", checkpoint_id=checkpoint_id)
    return {"configurable": configurable}


def turn_input(question: str) -> Dict[str, Any]:
    """Graph input for a new question on a (new or existing) thread."""
    return {"question": question, "messages": [HumanMessage(content=question)]}


def pending_interrupts(snapshot: Any) -> List[Any]:
    return [i for task in (snapshot.tasks or ()) for i in (task.interrupts or ())]


def _response(thread_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
    interrupts = result.get("__interrupt__")
    if interrupts:
        payload = interrupts[0].value
        kind = payload.get("kind", "web_search")
        return {"status": "needs_confirmation", "thread_id": thread_id, "kind": kind,
                "prompt": payload.get("prompt", CONFIRM_PROMPT), "draft": payload.get("draft", ""),
                "search": payload.get("search"),
                "answer": payload.get("answer") if kind == "report" else None,
                "sources": payload.get("sources", [])}
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


async def check_confirmable(thread_id: str, user_id: str) -> None:
    """Raise ThreadNotFound / NotAwaitingConfirmation unless the thread is paused at a gate."""
    snapshot = await _app().aget_state(thread_config(thread_id, user_id))
    if not snapshot.values:
        raise ThreadNotFound(thread_id)
    if not pending_interrupts(snapshot):
        raise NotAwaitingConfirmation(thread_id)


# ---------------------------------------------------------------- running the graph
# Events: ("sources", [urls]), ("token", text), ("status", text), then exactly one of
# ("needs_confirmation", {...}) or ("answered", {...}) — the same payloads as /ask and /confirm.


async def stream_run(graph_input: Any, thread_id: str, user_id: str,
                     checkpoint_id: Optional[str] = None) -> AsyncIterator[Tuple[str, Any]]:
    """Run (or resume) the thread and yield UI events.

    graph_input: turn_input(...) for a new question, Command(resume=...) at a gate, or
    None to continue from the last checkpoint (retry after a failure, replay, fork).
    durability="sync": every superstep is written before the next one starts, so a crash
    or failed node never loses completed work.
    """
    app = _app()
    config = thread_config(thread_id, user_id, checkpoint_id)
    async for mode, chunk in app.astream(graph_input, config, stream_mode=["custom", "updates"],
                                         durability="sync"):
        if mode == "custom":
            kind = chunk.get("type")
            if kind == "token":
                yield "token", chunk["text"]
            elif kind == "sources":
                yield "sources", chunk["sources"]
            elif kind == "status":
                yield "status", chunk["text"]
    # Read the head of the thread (not checkpoint_id: a replay/fork moves the head forward).
    snapshot = await app.aget_state(thread_config(thread_id, user_id))
    interrupts = pending_interrupts(snapshot)
    if interrupts:
        yield "needs_confirmation", _response(thread_id, {"__interrupt__": interrupts})
    else:
        yield "answered", _response(thread_id, snapshot.values)


async def run(graph_input: Any, thread_id: str, user_id: str,
              checkpoint_id: Optional[str] = None) -> Dict[str, Any]:
    """Non-streaming stream_run: returns the final needs_confirmation/answered payload."""
    final: Dict[str, Any] = {}
    async for event, data in stream_run(graph_input, thread_id, user_id, checkpoint_id):
        if event in ("needs_confirmation", "answered"):
            final = data
    return final


def resume_input(decision: Any) -> Command:
    return Command(resume=decision)
