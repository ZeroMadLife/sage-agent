"""Wire schemas for the isolated public Agent API."""

from typing import Literal

from pydantic import BaseModel, Field


class PublicAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=800)


class PublicCitationResponse(BaseModel):
    citation_id: str
    document_id: str
    title: str
    url: str
    revision: str
    excerpt: str
    content_sha256: str


class PublicReceiptResponse(BaseModel):
    request_id: str
    package_id: str
    package_revision: str
    package_digest: str
    evidence_ids: list[str]
    retrieval_limit: int = Field(ge=1, le=5)


class PublicUsageResponse(BaseModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class PublicAskResponse(BaseModel):
    status: Literal["answered", "no_match", "refused"]
    answer: str
    citations: list[PublicCitationResponse]
    receipt: PublicReceiptResponse
    usage: PublicUsageResponse


class PublicHealthResponse(BaseModel):
    status: Literal["ready", "not_configured"]
    package_id: str
    package_revision: str
    package_digest: str
