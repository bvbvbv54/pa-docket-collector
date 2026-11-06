from __future__ import annotations

from datetime import date
from typing import Protocol

from pydantic import BaseModel, Field


class AccessStatus(BaseModel):
    status: str
    checked_at: str
    url: str
    http_status: int | None = None
    detail: str | None = None


class SearchQuery(BaseModel):
    date_from: date | None = None
    date_to: date | None = None
    county: str | None = None
    court_type: str | None = None
    docket_number: str | None = None


class DocketRef(BaseModel):
    docket_number: str
    court_type: str | None = None
    county: str | None = None
    filing_date: date | None = None
    detail_url: str | None = None


class SearchResult(BaseModel):
    dockets: list[DocketRef] = Field(default_factory=list)
    pages_visited: int = 0
    pagination_complete: bool = False
    coverage_status: str = "UNVALIDATED"
    errors: list[str] = Field(default_factory=list)


class DocketDetail(BaseModel):
    docket: DocketRef
    document_url: str | None = None
    metadata: dict = Field(default_factory=dict)


class DocumentResult(BaseModel):
    docket_number: str
    source_url: str
    storage_path: str | None = None
    document_hash: str | None = None
    byte_count: int | None = None
    retrieved_at: str | None = None


class DocketSource(Protocol):
    async def check_access(self) -> AccessStatus: ...

    async def search(self, query: SearchQuery) -> SearchResult: ...

    async def get_docket(self, ref: DocketRef) -> DocketDetail: ...

    async def get_document(self, ref: DocketRef) -> DocumentResult: ...
