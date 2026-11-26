"""Builds the models for the configured provider: local Ollama (default) or Groq.

`LLM_PROVIDER` in .env (or the host's environment) picks one; nothing else changes.
- ollama: llama3 (text) + nomic-embed-text (embeddings), both in the local Ollama service.
- groq:   GROQ_MODEL on the Groq API (text) + HF_EMBED_MODEL run locally with
          sentence-transformers (Groq has no embedding models).
rag.llm, rag.embeddings and web_research.json_llm are built from these functions, so the
rest of the code (and the test fakes that patch those attributes) never see the provider.
"""

import sys
import time

from langchain_core.embeddings import Embeddings

from . import config


def describe() -> str:
    """e.g. "ollama:llama3" or "groq:openai/gpt-oss-120b", for /health and the dashboard."""
    model = config.GROQ_MODEL if config.LLM_PROVIDER == "groq" else config.LLM_MODEL
    return f"{config.LLM_PROVIDER}:{model}"


def embedding_id() -> dict:
    """Identifies the embedding space; saved next to the FAISS index to catch mismatches."""
    if config.LLM_PROVIDER == "groq":
        return {"provider": "huggingface", "model": config.HF_EMBED_MODEL, "dims": config.EMBED_DIMS}
    return {"provider": "ollama", "model": config.EMBED_MODEL, "dims": config.EMBED_DIMS}


def _groq_key() -> str:
    if not config.GROQ_API_KEY:
        raise RuntimeError("LLM_PROVIDER=groq needs GROQ_API_KEY (in .env or the host's environment).")
    return config.GROQ_API_KEY


def groq_json_mode() -> bool:
    """Whether to send response_format=json_object to Groq for the JSON steps."""
    return config.GROQ_JSON_MODE != "off"


def _groq_reasoning() -> dict:
    """Keep reasoning models quick and their thinking out of the answer text."""
    model = config.GROQ_MODEL.lower()
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low"}        # reasoning comes back separately, not in content
    if model.startswith("qwen/"):
        return {"reasoning_format": "hidden"}     # otherwise <think>…</think> lands in content
    return {}


def make_llm(json_mode: bool = False):
    """Text model. json_mode=True constrains the output to a JSON object."""
    if config.LLM_PROVIDER == "groq":
        from langchain_groq import ChatGroq

        kwargs = {}
        if json_mode and groq_json_mode():
            kwargs["model_kwargs"] = {"response_format": {"type": "json_object"}}
        return ChatGroq(
            model=config.GROQ_MODEL,
            temperature=config.LLM_TEMPERATURE,
            api_key=_groq_key(),
            max_retries=2,
            **_groq_reasoning(),
            **kwargs,
        )
    from langchain_ollama import OllamaLLM

    return OllamaLLM(
        model=config.LLM_MODEL,
        base_url=config.OLLAMA_BASE_URL,
        temperature=config.LLM_TEMPERATURE,
        **({"format": "json"} if json_mode else {}),
    )


def make_embeddings() -> Embeddings:
    if config.LLM_PROVIDER == "groq":
        from langchain_huggingface import HuggingFaceEmbeddings

        # Downloaded from Hugging Face on first use (~130 MB), then loaded from the local cache.
        return HuggingFaceEmbeddings(
            model_name=config.HF_EMBED_MODEL,
            encode_kwargs={"normalize_embeddings": True},   # unit vectors: FAISS L2 = cosine
        )
    from langchain_ollama import OllamaEmbeddings

    return OllamaEmbeddings(model=config.EMBED_MODEL, base_url=config.OLLAMA_BASE_URL)


def _fail(message: str):
    raise ValueError(message)


def _check() -> int:
    """`python -m backend.llm`: one tiny call of each kind, to test the key and models.

    Uses the provider from LLM_PROVIDER (.env or the shell). With groq, this spends a
    negligible amount of Groq quota; the embedding runs locally.
    """
    from langchain_core.output_parsers import StrOutputParser

    from .web_research import _parse_json  # the same extraction the graph uses

    json_prompt = 'Reply with a JSON object with exactly one key "ok" set to true.'
    print(f"provider   {describe()}")
    print(f"embeddings {embedding_id()['model']} ({config.EMBED_DIMS} dims)")
    print(f"data       {config.DATA_DIR}")
    steps = [
        ("text", lambda: (make_llm() | StrOutputParser()).invoke("Reply with the single word OK.").strip()[:60]),
        ("json", lambda: _parse_json((make_llm(json_mode=True) | StrOutputParser()).invoke(json_prompt))
            or _fail("no JSON object in reply")),
        # The draft node streams its JSON reply, so check streaming too.
        ("json-stream", lambda: _parse_json("".join((make_llm(json_mode=True) | StrOutputParser()).stream(json_prompt)))
            or _fail("no JSON object in streamed reply")),
        ("embedding", lambda: f"{len(make_embeddings().embed_query('Nvidia quarterly revenue'))} dims"),
    ]
    failed = False
    for name, step in steps:
        start = time.perf_counter()
        try:
            result = step()
            print(f"OK   {name:<11} {time.perf_counter() - start:5.1f}s  {result}")
        except Exception as exc:  # report every step, then exit non-zero
            failed = True
            print(f"FAIL {name:<11} {type(exc).__name__}: {str(exc)[:300]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_check())
