"""Turn 级不可变上下文与执行计划契约。"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

from core.harness.task_intent import TaskIntentEnvelope

PLAN_SCHEMA_VERSION = 1
MAX_PLAN_BYTES = 512 * 1024

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_SECRET_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "authorization",
        "authorization_header",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "passwd",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
)
_SECRET_KEY_SUFFIXES = (
    "api_key",
    "_access_token",
    "_authorization",
    "_cookie",
    "_credential",
    "_credentials",
    "_passwd",
    "_password",
    "_refresh_token",
    "_secret",
    "_token",
)


class TurnContextPlanValidationError(ValueError):
    """Plan 无效、非 canonical，或包含禁止持久化的敏感字段。"""


@dataclass(frozen=True, slots=True)
class TurnContextPlan:
    """已接受 Turn 的不可变决策快照。"""

    plan_id: str
    session_id: str
    run_id: str
    owner_fingerprint: str
    workspace_id: str
    surface: str
    created_at: str
    plan_hash: str
    _payload_json: str = field(repr=False)

    @classmethod
    def create(
        cls,
        *,
        plan_id: str,
        session_id: str,
        run_id: str,
        owner_fingerprint: str,
        workspace_id: str,
        surface: str,
        created_at: str,
        admission: Mapping[str, Any],
        prompt: Mapping[str, Any],
        context_refs: Mapping[str, Any],
        retrieval: Mapping[str, Any],
        tools: Mapping[str, Any],
        execution: Mapping[str, Any],
        resume: Mapping[str, Any],
        budget: Mapping[str, Any] | None = None,
    ) -> TurnContextPlan:
        """规范化一次 Turn 的结构化选择，并计算稳定 hash。"""
        _validate_identifier("plan_id", plan_id)
        _validate_identifier("session_id", session_id)
        _validate_identifier("run_id", run_id)
        _validate_identifier("owner_fingerprint", owner_fingerprint)
        _validate_identifier("workspace_id", workspace_id)
        _validate_identifier("surface", surface)
        _validate_timestamp(created_at)
        prompt_payload = _normalize_json(prompt)
        if not isinstance(prompt_payload, dict):
            raise TurnContextPlanValidationError("prompt must be an object")
        static_policy = prompt_payload.get("static_policy")
        if isinstance(static_policy, dict):
            rendered_content = static_policy.get("rendered_content")
            if isinstance(rendered_content, str):
                content_hash = hashlib.sha256(rendered_content.encode("utf-8")).hexdigest()
                stored_hash = static_policy.get("content_hash")
                if stored_hash is not None and stored_hash != content_hash:
                    raise TurnContextPlanValidationError("static policy content hash mismatch")
                static_policy["content_hash"] = content_hash
        payload = {
            "version": PLAN_SCHEMA_VERSION,
            "identity": {
                "plan_id": plan_id,
                "session_id": session_id,
                "run_id": run_id,
                "owner_fingerprint": owner_fingerprint,
                "workspace_id": workspace_id,
                "surface": surface,
                "created_at": created_at,
            },
            "admission": _normalize_json(admission),
            "prompt": prompt_payload,
            "context_refs": _normalize_json(context_refs),
            "retrieval": _normalize_json(retrieval),
            "tools": _normalize_json(tools),
            "execution": _normalize_json(execution),
            "resume": _normalize_json(resume),
        }
        if budget is not None:
            payload["budget"] = _normalize_json(budget)
        _validate_payload(payload)
        plan_hash = _hash_payload(payload)
        payload_json = _canonical_json(payload)
        _validate_size(payload_json)
        return cls(
            plan_id=plan_id,
            session_id=session_id,
            run_id=run_id,
            owner_fingerprint=owner_fingerprint,
            workspace_id=workspace_id,
            surface=surface,
            created_at=created_at,
            plan_hash=plan_hash,
            _payload_json=payload_json,
        )

    @classmethod
    def from_stored(
        cls,
        *,
        plan_id: str,
        session_id: str,
        run_id: str,
        version: int,
        plan_hash: str,
        owner_fingerprint: str,
        workspace_id: str,
        surface: str,
        checkpoint_thread_id: str,
        checkpoint_namespace: str,
        payload_json: str,
        created_at: str,
    ) -> TurnContextPlan:
        """验证数据库中的 Plan 行，任何篡改都按损坏处理。"""
        if version != PLAN_SCHEMA_VERSION:
            raise TurnContextPlanValidationError("unsupported turn context plan version")
        try:
            payload = json.loads(payload_json, object_pairs_hook=_reject_duplicate_keys)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise TurnContextPlanValidationError(
                "stored turn context plan JSON is invalid"
            ) from exc
        if not isinstance(payload, dict):
            raise TurnContextPlanValidationError("stored turn context plan must be an object")
        _validate_payload(payload)
        identity = payload.get("identity")
        if not isinstance(identity, dict):
            raise TurnContextPlanValidationError("stored turn context plan identity is invalid")
        expected_identity = {
            "plan_id": plan_id,
            "session_id": session_id,
            "run_id": run_id,
            "owner_fingerprint": owner_fingerprint,
            "workspace_id": workspace_id,
            "surface": surface,
            "created_at": created_at,
        }
        for key, expected in expected_identity.items():
            if identity.get(key) != expected:
                raise TurnContextPlanValidationError(f"stored turn context plan {key} mismatch")
        resume = payload.get("resume")
        if not isinstance(resume, dict):
            raise TurnContextPlanValidationError("stored turn context plan resume is invalid")
        if resume.get("checkpoint_thread_id") != checkpoint_thread_id:
            raise TurnContextPlanValidationError(
                "stored turn context plan checkpoint thread mismatch"
            )
        if resume.get("checkpoint_namespace", "") != checkpoint_namespace:
            raise TurnContextPlanValidationError(
                "stored turn context plan checkpoint namespace mismatch"
            )
        actual_hash = _hash_payload(payload)
        if actual_hash != plan_hash:
            raise TurnContextPlanValidationError("stored turn context plan hash mismatch")
        canonical = _canonical_json(payload)
        if canonical != payload_json:
            raise TurnContextPlanValidationError("stored turn context plan is not canonical")
        _validate_size(payload_json)
        return cls(
            plan_id=plan_id,
            session_id=str(identity["session_id"]),
            run_id=run_id,
            owner_fingerprint=owner_fingerprint,
            workspace_id=workspace_id,
            surface=surface,
            created_at=created_at,
            plan_hash=plan_hash,
            _payload_json=payload_json,
        )

    def to_payload(self) -> dict[str, Any]:
        """返回独立副本，调用方修改不会改变不可变 Plan。"""
        return cast(dict[str, Any], json.loads(self._payload_json))

    @property
    def payload_json(self) -> str:
        """返回已规范化的 JSON，用于受控的存储适配器。"""
        return self._payload_json

    def checkpoint_binding(self) -> dict[str, object]:
        """只导出 Graph checkpoint 所需的 Plan 身份，不泄露完整 payload。"""
        return {
            "version": PLAN_SCHEMA_VERSION,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
        }

    def identity_kwargs(self) -> dict[str, str]:
        """返回用于测试或适配器重建的身份字段。"""
        return {
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "owner_fingerprint": self.owner_fingerprint,
            "workspace_id": self.workspace_id,
            "surface": self.surface,
        }

    @property
    def static_policy_hash(self) -> str:
        """返回静态策略正文的 digest，不暴露正文。"""
        static_policy = self.to_payload().get("prompt", {}).get("static_policy", {})
        if not isinstance(static_policy, dict):
            return _hash_mapping({})
        rendered = static_policy.get("rendered_content")
        if isinstance(rendered, str):
            return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        return _hash_mapping(static_policy)


def build_turn_context_plan_receipt(plan: TurnContextPlan) -> dict[str, Any]:
    """生成不含正文、参数和远端配置的 Timeline receipt。"""
    payload = plan.to_payload()
    retrieval = _mapping_value(payload.get("retrieval"))
    tools = _mapping_value(payload.get("tools"))
    refs = _mapping_value(payload.get("context_refs"))
    prompt = _mapping_value(payload.get("prompt"))
    authority = _mapping_value(prompt.get("dynamic_authority"))
    selected_sources = retrieval.get("selected_sources")
    if not isinstance(selected_sources, list):
        selected_sources = []
    receipt: dict[str, Any] = {
        "type": "turn_context_plan_prepared",
        "version": PLAN_SCHEMA_VERSION,
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "prompt": {
            "static_policy_hash": plan.static_policy_hash,
            "authority_hash": _hash_mapping(authority),
        },
        "retrieval": {
            "decision": _safe_text(retrieval.get("decision"), 40),
            "sources": [_safe_text(item, 64) for item in selected_sources[:16]],
            "scope": _safe_text(retrieval.get("tool_scope"), 64),
        },
        "tools": {
            "catalog_hash": _safe_text(tools.get("catalog_hash"), 128),
            "resident_count": _bounded_count(tools.get("resident_ids")),
            "deferred_count": _bounded_count(tools.get("deferred_ids")),
        },
        "context": {
            "transcript_range": _safe_range(refs.get("transcript_range")),
            "memory_ref_count": _bounded_count(refs.get("memory_refs")),
            "artifact_ref_count": _bounded_count(refs.get("artifact_refs")),
        },
    }
    budget = payload.get("budget")
    if isinstance(budget, dict):
        estimate = budget.get("estimated_input_tokens")
        if isinstance(estimate, int) and not isinstance(estimate, bool) and estimate >= 0:
            receipt["budget"] = {"estimated_input_tokens": min(estimate, 10_000_000)}
    return receipt


def _validate_payload(payload: Mapping[str, Any]) -> None:
    """检查严格 JSON 和禁止进入持久化 Plan 的敏感字段。"""
    if payload.get("version") != PLAN_SCHEMA_VERSION:
        raise TurnContextPlanValidationError("unsupported turn context plan version")
    if not _strict_json_value(payload):
        raise TurnContextPlanValidationError("turn context plan must contain strict JSON values")
    _reject_secret_keys(payload)
    identity = payload.get("identity")
    if not isinstance(identity, dict):
        raise TurnContextPlanValidationError("turn context plan identity is required")
    for key in ("plan_id", "session_id", "run_id", "owner_fingerprint", "workspace_id", "surface"):
        value = identity.get(key)
        if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
            raise TurnContextPlanValidationError(f"invalid plan identity: {key}")
    created_at = identity.get("created_at")
    if not isinstance(created_at, str):
        raise TurnContextPlanValidationError("plan created_at must be a string")
    _validate_timestamp(created_at)
    for section in (
        "admission",
        "prompt",
        "context_refs",
        "retrieval",
        "tools",
        "execution",
        "resume",
    ):
        if not isinstance(payload.get(section), dict):
            raise TurnContextPlanValidationError(f"plan section is not an object: {section}")
    if "budget" in payload and not isinstance(payload["budget"], dict):
        raise TurnContextPlanValidationError("plan section is not an object: budget")
    raw_task_intent = cast(Mapping[str, Any], payload["admission"]).get("task_intent")
    if raw_task_intent is not None:
        if not isinstance(raw_task_intent, Mapping):
            raise TurnContextPlanValidationError("admission task intent must be an object")
        try:
            TaskIntentEnvelope.from_mapping(raw_task_intent)
        except (TypeError, ValueError) as exc:
            raise TurnContextPlanValidationError("invalid admission task intent") from exc


def _reject_secret_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = _normalize_sensitive_key(str(key))
            if normalized in _SECRET_KEYS or any(
                normalized.endswith(suffix) for suffix in _SECRET_KEY_SUFFIXES
            ):
                raise TurnContextPlanValidationError(f"secret-bearing plan field rejected: {key}")
            _reject_secret_keys(item)
    elif isinstance(value, list):
        for item in value:
            _reject_secret_keys(item)


def _normalize_sensitive_key(key: str) -> str:
    """统一 snake/camel/kebab key，避免凭据字段通过命名变体绕过。"""
    canonical = unicodedata.normalize("NFKC", key)
    snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", canonical)
    return re.sub(r"[^A-Za-z0-9]+", "_", snake).strip("_").casefold()


def _mapping_value(value: Any) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], value) if isinstance(value, dict) else {}


def _normalize_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TurnContextPlanValidationError("plan JSON object keys must be strings")
        return {key: _normalize_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_normalize_json(item) for item in value]
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float) and value == value and abs(value) != float("inf"):
        return value
    raise TurnContextPlanValidationError("plan contains a non-JSON value")


def _strict_json_value(value: Any) -> bool:
    if value is None or isinstance(value, str | bool | int):
        return True
    if isinstance(value, float):
        return value == value and abs(value) != float("inf")
    if isinstance(value, list):
        return all(_strict_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _strict_json_value(item) for key, item in value.items())
    return False


def _canonical_json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        )
    except (TypeError, ValueError) as exc:
        raise TurnContextPlanValidationError("plan cannot be encoded as canonical JSON") from exc


def _hash_payload(payload: Mapping[str, Any]) -> str:
    material = json.loads(_canonical_json(payload))
    identity = material.get("identity", {})
    if isinstance(identity, dict):
        identity.pop("plan_id", None)
        identity.pop("created_at", None)
    return "sha256:" + hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _hash_mapping(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _validate_size(payload_json: str) -> None:
    if len(payload_json.encode("utf-8")) > MAX_PLAN_BYTES:
        raise TurnContextPlanValidationError("turn context plan exceeds size limit")


def _validate_identifier(name: str, value: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise TurnContextPlanValidationError(f"invalid {name}")


def _validate_timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise TurnContextPlanValidationError("plan timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise TurnContextPlanValidationError("plan timestamp must include timezone")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _safe_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _bounded_count(value: Any) -> int:
    if not isinstance(value, list | tuple):
        return 0
    return min(len(value), 10_000)


def _safe_range(value: Any) -> list[int] | None:
    if not isinstance(value, list) or len(value) != 2:
        return None
    if not all(
        isinstance(item, int) and not isinstance(item, bool) and item >= 0 for item in value
    ):
        return None
    return [min(value[0], 10_000_000), min(value[1], 10_000_000)]


__all__ = [
    "MAX_PLAN_BYTES",
    "PLAN_SCHEMA_VERSION",
    "TurnContextPlan",
    "TurnContextPlanValidationError",
    "build_turn_context_plan_receipt",
]
