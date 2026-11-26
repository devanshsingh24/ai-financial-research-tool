"""Request and response models for the API."""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


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


class ConfirmRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64)
    approve: bool


class WebSearchInfo(BaseModel):
    used: bool
    from_cache: bool
    query: str
    topic: str
    time_range: Optional[str] = None
    error: Optional[str] = None


class SearchPlan(BaseModel):
    query: str
    topic: str
    time_range: Optional[str] = None


class AskResponse(BaseModel):
    status: Literal["answered", "needs_confirmation"]
    thread_id: str
    # needs_confirmation: kind says which gate the thread is paused at
    kind: Optional[Literal["web_search", "report"]] = None
    prompt: Optional[str] = None
    draft: Optional[str] = None
    search: Optional[SearchPlan] = None  # web_search gate: the planned (editable) query
    # answered (and the report gate's report text)
    answer: Optional[str] = None
    sources: List[str] = []
    web_search: Optional[WebSearchInfo] = None


# ---------------------------------------------------------------- threads API


class ResumeRequest(BaseModel):
    """Resume a thread paused at a gate. edit + query at the search gate; edit + draft at
    the report gate."""
    action: Literal["approve", "reject", "edit"]
    query: Optional[str] = Field(default=None, max_length=400)
    draft: Optional[str] = Field(default=None, max_length=20000)


class ThreadCreate(BaseModel):
    title: str = Field(default="", max_length=200)


class ThreadOut(BaseModel):
    thread_id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    status: Literal["idle", "running", "interrupted", "completed", "failed"]
    error: Optional[str] = None


class MessageOut(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ThreadState(BaseModel):
    thread: ThreadOut
    next: List[str]
    pending: Optional[AskResponse] = None  # set when paused at a gate
    last: Optional[AskResponse] = None     # the latest completed answer (sources, web_search)
    messages: List[MessageOut] = []
    summary: str = ""
    memories: List[str] = []
    answer: Optional[str] = None
    checkpoint_id: Optional[str] = None


class CheckpointOut(BaseModel):
    checkpoint_id: str
    parent_id: Optional[str] = None
    step: Optional[int] = None
    source: Optional[str] = None
    next: List[str]
    created_at: Optional[str] = None
    values: Dict[str, Any]


class ReplayRequest(BaseModel):
    checkpoint_id: str = Field(min_length=1, max_length=64)


class ForkRequest(BaseModel):
    checkpoint_id: str = Field(min_length=1, max_length=64)
    values: Dict[str, Any] = Field(min_length=1)
    as_node: Optional[str] = None
    run: bool = True


class ForkResponse(BaseModel):
    checkpoint_id: str
    result: Optional[AskResponse] = None
