"""Short-term (per-thread) and long-term (per-user, cross-thread) memory.

Short-term: the thread's `messages` live in checkpointed graph state. Once a thread has
more than MAX_MESSAGES, older turns are folded into a running `summary` and removed.

Long-term: a LangGraph Store namespaced (user_id, "memories") with an embedding index
on "text". Relevant memories are read when a thread starts; preferences, tracked
companies and a report summary are written when a run completes.
"""

import hashlib
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Sequence, Tuple

from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.store.base import BaseStore

from . import config as cfg
from . import rag

log = logging.getLogger("finsight.memory")

NONE = "(none)"


def user_id_of(config: RunnableConfig) -> str:
    return str((config.get("configurable") or {}).get("user_id") or "local")


def namespace(user_id: str) -> Tuple[str, str]:
    return (user_id, "memories")


# ---------------------------------------------------------------- short-term


summary_prompt = ChatPromptTemplate.from_template(
    """Summarize this research conversation so far in at most 6 short bullet points.
Keep company names, tickers, figures and the user's stated preferences. Do not add facts.

Existing summary:
{summary}

New messages:
{messages}

Summary:"""
)


def _line(message: AnyMessage, limit: int = 600) -> str:
    who = "User" if isinstance(message, HumanMessage) else "Assistant"
    text = str(message.content).strip().replace("\n", " ")
    return f"{who}: {text[:limit]}"


def summarize_old_messages(messages: Sequence[AnyMessage], summary: str) -> Tuple[str, List[str]]:
    """If the thread is too long, return (new summary, ids of messages to remove).

    Returns (summary, []) when no trimming is needed.
    """
    if len(messages) <= cfg.MAX_MESSAGES:
        return summary, []
    old = list(messages[: len(messages) - cfg.KEEP_MESSAGES])
    new_summary = (summary_prompt | rag.llm | StrOutputParser()).invoke(
        {"summary": summary or NONE, "messages": "\n".join(_line(m) for m in old)}
    ).strip()
    return new_summary, [m.id for m in old if m.id]


def format_history(state: Dict[str, Any]) -> str:
    """Conversation context for prompts: running summary + earlier turns (not the current question)."""
    messages = list(state.get("messages") or [])
    if messages and isinstance(messages[-1], HumanMessage):
        messages = messages[:-1]  # the current question is passed separately
    parts = []
    if state.get("summary"):
        parts.append(f"Summary of earlier turns:\n{state['summary']}")
    if messages:
        parts.append("\n".join(_line(m) for m in messages[-cfg.KEEP_MESSAGES:]))
    return "\n\n".join(parts) or NONE


def format_memories(state: Dict[str, Any]) -> str:
    return "\n".join(f"- {m}" for m in state.get("memories") or []) or NONE


# ---------------------------------------------------------------- long-term


async def load_memories(state: Dict[str, Any], config: RunnableConfig, *, store: BaseStore) -> Dict[str, Any]:
    """At thread start, read the user's memories most relevant to the first question."""
    if state.get("turn", 1) > 1:
        return {}
    try:
        items = await store.asearch(namespace(user_id_of(config)), query=state["question"],
                                    limit=cfg.MEMORY_SEARCH_LIMIT)
    except Exception as exc:  # memory is best effort; never fail the question
        log.warning("Memory search failed: %s", exc)
        return {"memories": []}
    return {"memories": [f"[{i.value.get('kind', 'note')}] {i.value.get('text', '')}" for i in items]}


extract_prompt = ChatPromptTemplate.from_template(
    """Extract durable facts about the USER from this research exchange, to remember in future conversations.

Reply with a JSON object with exactly these keys:
  "preferences": list of short statements of preferences the user stated about answers
                 (length, style, focus, risk appetite, horizon). [] if none.
  "companies": list of companies or tickers the user is asking about or tracking. [] if none.
  "summary": one sentence summarizing the question and the answer.

Question: {question}

Answer: {answer}"""
)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _hash(text: str) -> str:
    return hashlib.sha1(text.strip().lower().encode("utf-8")).hexdigest()[:12]


def memory_items(extracted: Dict[str, Any], thread_id: str, turn: int) -> List[Tuple[str, Dict[str, Any]]]:
    """(key, value) pairs to upsert. Keys make repeats overwrite instead of duplicating."""
    now = datetime.now(timezone.utc).isoformat()
    out: List[Tuple[str, Dict[str, Any]]] = []
    for pref in extracted.get("preferences") or []:
        if isinstance(pref, str) and pref.strip():
            out.append((f"pref:{_hash(pref)}", {"kind": "preference", "text": pref.strip()}))
    for company in extracted.get("companies") or []:
        if isinstance(company, str) and _slug(company):
            out.append((f"company:{_slug(company)}",
                        {"kind": "company", "text": f"User tracks {company.strip()}"}))
    summary = extracted.get("summary")
    if isinstance(summary, str) and summary.strip():
        out.append((f"report:{thread_id}:{turn}", {"kind": "report", "text": summary.strip()}))
    return [(k, {**v, "thread_id": thread_id, "updated_at": now}) for k, v in out]


async def save_memory(state: Dict[str, Any], config: RunnableConfig, *, store: BaseStore) -> Dict[str, Any]:
    """When a run completes, store preferences, tracked companies and a report summary."""
    answer = (state.get("answer") or "").strip()
    if not answer:
        return {}
    from . import web_research  # late: web_research imports this module

    try:
        raw = await (extract_prompt | web_research.json_llm | StrOutputParser()).ainvoke(
            {"question": state["question"], "answer": answer[:3000]}
        )
        extracted = web_research._parse_json(raw) or {}
        thread_id = str(config["configurable"]["thread_id"])
        ns = namespace(user_id_of(config))
        for key, value in memory_items(extracted, thread_id, state.get("turn", 1)):
            await store.aput(ns, key, value)
    except Exception as exc:  # best effort: never fail a finished answer
        log.warning("Saving memories failed: %s", exc)
    return {}
