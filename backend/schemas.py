"""Request and response models for the API."""

from typing import List, Literal, Optional

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


class AskResponse(BaseModel):
    status: Literal["answered", "needs_confirmation"]
    thread_id: str
    # needs_confirmation
    prompt: Optional[str] = None
    draft: Optional[str] = None
    # answered
    answer: Optional[str] = None
    sources: List[str] = []
    web_search: Optional[WebSearchInfo] = None
