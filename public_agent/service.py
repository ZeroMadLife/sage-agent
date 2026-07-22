"""Bounded public-corpus retrieval, synthesis, and disclosure controls."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from public_agent.corpus import PublicDocument, PublicPackage
from public_agent.model import PublicAnswerModel, PublicModelAnswer

_PRIVATE_REQUESTS = (
    re.compile(
        r"(?:读取|显示|告诉|列出|泄露|导出|忽略).{0,28}"
        r"(?:私有|内部|session|memory|workspace|api.?key|token|系统提示|system prompt)",
        re.IGNORECASE,
    ),
    re.compile(r"ignore (?:all |the )?(?:previous|prior|system) instructions", re.IGNORECASE),
)
_LEAK_PATTERNS = (
    re.compile(r"/(?:Users|home|etc|opt/sage)/", re.IGNORECASE),
    re.compile(
        r"(?:api[_ -]?key|token|secret|password)[\"']?\s*[:=]\s*[\"']?\S+",
        re.IGNORECASE,
    ),
    re.compile(r"(?:sk-|ghp_|github_pat_)[a-z0-9_-]{12,}", re.IGNORECASE),
)
_REFUSAL = (
    "这个公开入口只回答已发布的 Sage 项目资料。私人 Session、Memory、工作区、"
    "系统提示、凭据和未发布内容不在公开资料包中。"
)
_NO_MATCH = "当前公开资料包没有足够证据回答这个问题。你可以询问 Sage、Harness、Knowledge 或 Mastery Evidence。"


@dataclass(frozen=True, slots=True)
class PublicCitation:
    citation_id: str
    document_id: str
    title: str
    url: str
    revision: str
    excerpt: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class PublicAnswerReceipt:
    request_id: str
    package_id: str
    package_revision: str
    package_digest: str
    evidence_ids: tuple[str, ...]
    retrieval_limit: int


@dataclass(frozen=True, slots=True)
class PublicAgentResult:
    status: str
    answer: str
    citations: tuple[PublicCitation, ...]
    receipt: PublicAnswerReceipt
    input_tokens: int = 0
    output_tokens: int = 0


class PublicAgentService:
    def __init__(
        self,
        package: PublicPackage,
        model: PublicAnswerModel | None,
        *,
        retrieval_limit: int = 3,
    ) -> None:
        self.package = package
        self.model = model
        self.retrieval_limit = retrieval_limit

    @property
    def ready(self) -> bool:
        return self.model is not None

    async def answer(self, question: str) -> PublicAgentResult:
        request_id = f"pub_{secrets.token_hex(6)}"
        if _private_request(question):
            return self._result(request_id, "refused", _REFUSAL, (), PublicModelAnswer(""))
        evidence = self.package.retrieve(question, limit=self.retrieval_limit)
        if not evidence:
            return self._result(request_id, "no_match", _NO_MATCH, (), PublicModelAnswer(""))
        if self.model is None:
            raise RuntimeError("public answer model is not configured")
        model_answer = await self.model.answer(question, evidence)
        if not model_answer.text or _contains_leak(model_answer.text):
            return self._result(request_id, "refused", _REFUSAL, (), model_answer)
        citations = tuple(_citation(index, item) for index, item in enumerate(evidence, start=1))
        return self._result(request_id, "answered", model_answer.text, citations, model_answer)

    def _result(
        self,
        request_id: str,
        status: str,
        answer: str,
        citations: tuple[PublicCitation, ...],
        model_answer: PublicModelAnswer,
    ) -> PublicAgentResult:
        receipt = PublicAnswerReceipt(
            request_id=request_id,
            package_id=self.package.package_id,
            package_revision=self.package.revision,
            package_digest=self.package.digest,
            evidence_ids=tuple(item.document_id for item in citations),
            retrieval_limit=self.retrieval_limit,
        )
        return PublicAgentResult(
            status=status,
            answer=answer,
            citations=citations,
            receipt=receipt,
            input_tokens=model_answer.input_tokens,
            output_tokens=model_answer.output_tokens,
        )


def _private_request(question: str) -> bool:
    return any(pattern.search(question) for pattern in _PRIVATE_REQUESTS)


def _contains_leak(answer: str) -> bool:
    return any(pattern.search(answer) for pattern in _LEAK_PATTERNS)


def _citation(index: int, document: PublicDocument) -> PublicCitation:
    return PublicCitation(
        citation_id=f"E{index}",
        document_id=document.document_id,
        title=document.title,
        url=document.url,
        revision=document.revision,
        excerpt=document.content,
        content_sha256=document.content_sha256,
    )
