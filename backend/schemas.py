"""Request and response models for the API."""

from typing import List

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
