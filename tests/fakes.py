"""Fakes shared by the test suite and the e2e test app (tests/e2e/fake_app.py).

A prompt-routing fake LLM, deterministic bag-of-words embeddings, fake retrieval and a
fake Tavily. Nothing here touches the network or costs credits.
"""

import hashlib
import json
import math
import re
from typing import Any, Callable, Dict, Iterator, List, Optional

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.llms import LLM
from langchain_core.outputs import GenerationChunk

from backend import config, rag, web_research

KNOWN_COMPANIES = ["NVIDIA", "Tesla", "Apple"]


class Brain:
    """Scripted behaviour of the fake LLM / retrieval / Tavily, plus call logs."""

    def __init__(self) -> None:
        self.can_answer = True
        self.draft_answer = "NVIDIA revenue grew 50% on data-center demand."
        self.planned_query = "nvidia revenue latest quarter"
        self.final_text = "NVIDIA reported record revenue [1]."
        self.fail_final = 0  # raise in final_answer this many times
        self.prompts: Dict[str, List[str]] = {}
        self.retrievals = 0
        self.tavily_queries: List[str] = []

    def log(self, kind: str, prompt: str) -> None:
        self.prompts.setdefault(kind, []).append(prompt)

    def count(self, kind: str) -> int:
        return len(self.prompts.get(kind, []))

    def respond(self, prompt: str) -> str:
        if '"can_answer"' in prompt:
            self.log("draft", prompt)
            return json.dumps({"can_answer": self.can_answer, "answer": self.draft_answer})
        if "Plan exactly ONE web search" in prompt:
            self.log("plan", prompt)
            return json.dumps({"query": self.planned_query, "topic": "finance", "time_range": "month"})
        if "Extract durable facts" in prompt:
            self.log("extract", prompt)
            question = prompt.split("Question:", 1)[1].split("Answer:", 1)[0].strip()
            prefs = [s.strip() for s in re.split(r"[.;]", question) if "prefer" in s.lower()]
            companies = [c for c in KNOWN_COMPANIES if c.lower() in question.lower()]
            return json.dumps({"preferences": prefs, "companies": companies,
                               "summary": f"Report on: {question[:120]}"})
        if "Summarize this research conversation" in prompt:
            self.log("summary", prompt)
            return "- earlier turns about NVIDIA revenue"
        if "Web results:" in prompt:
            self.log("final", prompt)
            if self.fail_final > 0:
                self.fail_final -= 1
                raise RuntimeError("LLM unavailable (simulated)")
            return self.final_text
        self.log("other", prompt)
        return "ok"


BRAIN: Brain = Brain()


class FakeLLM(LLM):
    """Routes on prompt text to BRAIN; streams in small chunks like Ollama."""

    @property
    def _llm_type(self) -> str:
        return "fake-finsight"

    def _call(self, prompt: str, stop: Optional[List[str]] = None, run_manager: Any = None,
              **kwargs: Any) -> str:
        return BRAIN.respond(prompt)

    def _stream(self, prompt: str, stop: Optional[List[str]] = None, run_manager: Any = None,
                **kwargs: Any) -> Iterator[GenerationChunk]:
        text = BRAIN.respond(prompt)
        for i in range(0, len(text), 7):
            yield GenerationChunk(text=text[i:i + 7])


class HashEmbeddings(Embeddings):
    """Deterministic bag-of-words embeddings: texts sharing words are close."""

    def _vec(self, text: str) -> List[float]:
        v = [0.0] * config.EMBED_DIMS
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            v[int(hashlib.md5(word.encode()).hexdigest(), 16) % config.EMBED_DIMS] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> List[float]:
        return self._vec(text)


def _fake_search(question: str):
    BRAIN.retrievals += 1
    return (["NVIDIA revenue grew 50% year over year on data-center demand."],
            ["https://example.com/nvidia-q3"], 0.4)


def _fake_tavily(query: str, topic: str, time_range: Optional[str]):
    BRAIN.tavily_queries.append(query)
    return [{"title": "NVIDIA results", "url": "https://news.example.com/nvda", "content": "Record revenue."}]


def install(set_attr: Callable[[Any, str, Any], None], brain: Optional[Brain] = None) -> Brain:
    """Swap the models, retrieval and Tavily for fakes. `set_attr` is monkeypatch.setattr
    in tests, or plain setattr in the e2e app. Returns the Brain that scripts them."""
    global BRAIN
    BRAIN = brain or Brain()
    fake = FakeLLM()
    set_attr(web_research, "json_llm", fake)
    set_attr(rag, "llm", fake)
    set_attr(rag, "embeddings", HashEmbeddings())
    set_attr(rag, "search_with_scores", _fake_search)
    set_attr(rag, "is_ready", lambda: True)
    set_attr(rag, "load_saved_index", lambda: None)
    set_attr(web_research, "_tavily", _fake_tavily)
    return BRAIN
