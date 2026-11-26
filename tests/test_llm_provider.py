"""The provider switch: LLM_PROVIDER picks Ollama or Groq (+ local Hugging Face embeddings),
and a FAISS index built with one embedding model is refused by a server using another.
No network calls and no model downloads."""

import json
import os
import subprocess
import sys
from pathlib import Path

import langchain_huggingface
import pytest
from langchain_groq import ChatGroq
from langchain_ollama import OllamaEmbeddings, OllamaLLM

from backend import config, llm, rag


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(config, "GROQ_API_KEY", "test-key-not-real")
    monkeypatch.setattr(config, "GROQ_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setattr(config, "GROQ_JSON_MODE", "on")


def test_ollama_is_the_default():
    assert config.LLM_PROVIDER == "ollama"
    assert isinstance(llm.make_llm(), OllamaLLM)
    assert llm.make_llm(json_mode=True).format == "json"
    assert isinstance(llm.make_embeddings(), OllamaEmbeddings)
    assert llm.describe() == "ollama:llama3"
    assert llm.embedding_id() == {"provider": "ollama", "model": "nomic-embed-text", "dims": 768}


def test_groq_builds_chatgroq(groq):
    text, as_json = llm.make_llm(), llm.make_llm(json_mode=True)
    assert isinstance(text, ChatGroq) and isinstance(as_json, ChatGroq)
    assert as_json.model_name == "openai/gpt-oss-120b"
    assert as_json.model_kwargs == {"response_format": {"type": "json_object"}}
    assert "response_format" not in text.model_kwargs
    assert text.reasoning_effort == "low"   # gpt-oss: quick, reasoning kept out of the answer
    assert llm.describe() == "groq:openai/gpt-oss-120b"


def test_groq_json_mode_can_be_turned_off(groq, monkeypatch):
    monkeypatch.setattr(config, "GROQ_JSON_MODE", "off")
    assert "response_format" not in llm.make_llm(json_mode=True).model_kwargs


def test_qwen_hides_its_thinking(groq, monkeypatch):
    monkeypatch.setattr(config, "GROQ_MODEL", "qwen/qwen3.8-27b")
    assert llm.make_llm().reasoning_format == "hidden"


def test_groq_needs_a_key(groq, monkeypatch):
    monkeypatch.setattr(config, "GROQ_API_KEY", "")
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        llm.make_llm()


def test_groq_uses_normalized_bge_embeddings(groq, monkeypatch):
    seen = {}

    class Stub:  # stands in for HuggingFaceEmbeddings, so no model is downloaded
        def __init__(self, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(langchain_huggingface, "HuggingFaceEmbeddings", Stub)
    monkeypatch.setattr(config, "EMBED_DIMS", 384)
    assert isinstance(llm.make_embeddings(), Stub)
    assert seen == {"model_name": "BAAI/bge-small-en-v1.5", "encode_kwargs": {"normalize_embeddings": True}}
    assert llm.embedding_id() == {"provider": "huggingface", "model": "BAAI/bge-small-en-v1.5", "dims": 384}


def test_index_from_another_embedding_model_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "INDEX_PATH", tmp_path)
    monkeypatch.setattr(rag, "_store", None)
    monkeypatch.setattr(rag, "_retriever", None)
    monkeypatch.setattr(rag, "index_problem", None)
    monkeypatch.setattr(rag, "_loaded_version", None)
    (tmp_path / "index.faiss").write_bytes(b"")  # flat (pre-versioning) layout
    (tmp_path / "meta.json").write_text(
        json.dumps({"provider": "huggingface", "model": "BAAI/bge-small-en-v1.5", "dims": 384}), encoding="utf-8")

    def must_not_load(*args, **kwargs):
        raise AssertionError("a mismatched index must not be loaded")

    monkeypatch.setattr(rag.FAISS, "load_local", must_not_load)
    rag.load_saved_index()
    assert not rag.is_ready()
    assert "Create the collection again" in rag.index_problem


@pytest.mark.parametrize("provider, data, index, dims", [
    ("ollama", ("data",), ("faiss_index",), 768),
    ("groq", ("data", "groq"), ("data", "groq", "faiss_index"), 384),
])
def test_each_provider_keeps_its_own_data(provider, data, index, dims):
    # Fresh interpreter: these are fixed when config is imported.
    env = {**os.environ, "LLM_PROVIDER": provider, "EMBED_DIMS": ""}
    out = subprocess.run(
        [sys.executable, "-c",
         "from backend import config; print(config.DATA_DIR); print(config.INDEX_PATH); print(config.EMBED_DIMS)"],
        env=env, capture_output=True, text=True, check=True, cwd=config.REPO_ROOT,
    ).stdout.split()
    assert Path(out[0]) == config.BACKEND_DIR.joinpath(*data)
    assert Path(out[1]) == config.BACKEND_DIR.joinpath(*index)
    assert int(out[2]) == dims
