"""Domain values for owner-scoped cloud model Providers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

ApiMode = Literal[
    "openai_chat_completions",
    "openai_responses",
    "anthropic_messages",
]


@dataclass(frozen=True, slots=True)
class CloudModel:
    id: str
    provider_id: str
    model_id: str
    display_name: str
    context_window_tokens: int | None
    output_reserve_tokens: int | None
    reasoning_supported: bool

    @property
    def runtime_id(self) -> str:
        return f"account:{self.provider_id}:{self.model_id}"


@dataclass(frozen=True, slots=True)
class CloudModelProvider:
    id: str
    owner_user_id: str
    name: str
    api_mode: ApiMode
    base_url: str
    key_hint: str
    status: str
    last_tested_at: datetime | None
    models: tuple[CloudModel, ...]


@dataclass(frozen=True, slots=True)
class CloudModelDefault:
    provider_id: str
    model_record_id: str
    runtime_model_id: str


@dataclass(frozen=True, slots=True)
class RuntimeProviderCredential:
    provider_id: str
    api_mode: ApiMode
    base_url: str
    api_key: str
    models: tuple[CloudModel, ...]


@dataclass(frozen=True, slots=True)
class ModelInput:
    model_id: str
    display_name: str
    context_window_tokens: int | None = None
    output_reserve_tokens: int | None = None
    reasoning_supported: bool = False
