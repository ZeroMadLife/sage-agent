"""In-memory account Provider catalog and direct credential model factory."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from core.cloud.model_providers.models import RuntimeProviderCredential
from core.cloud.model_providers.network import create_provider_http_client
from core.llm import create_llm

_ANTHROPIC_BUDGETS = {"low": 1024, "medium": 4096, "high": 8192}


class AccountModelFactory:
    """Build clients from an authenticated user's decrypted in-memory credentials."""

    def __init__(self, credentials: Sequence[RuntimeProviderCredential]) -> None:
        self._credentials = {item.provider_id: item for item in credentials}

    def __call__(self, model_spec: str, *, reasoning_mode: str = "off") -> Any:
        provider_id, model_id = parse_account_model_id(model_spec)
        credential = self._credentials.get(provider_id)
        if credential is None:
            raise ValueError("account model Provider is unavailable")
        model = next((item for item in credential.models if item.model_id == model_id), None)
        if model is None:
            raise ValueError("account model is unavailable")
        if reasoning_mode != "off" and not model.reasoning_supported:
            raise ValueError("unsupported reasoning mode")
        if credential.destination is None:
            raise ValueError("account model Provider destination is not pinned")
        kwargs: dict[str, Any] = {}
        if credential.api_mode == "anthropic_messages" and reasoning_mode != "off":
            budget = _ANTHROPIC_BUDGETS.get(reasoning_mode)
            if budget is None:
                raise ValueError("unsupported reasoning mode")
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
        kwargs["http_async_client"] = create_provider_http_client(credential.destination)
        return create_llm(
            f"account:{model_id}",
            api_key=credential.api_key,
            base_url=credential.base_url,
            api_mode=credential.api_mode,
            reasoning_mode=reasoning_mode,
            **kwargs,
        )


class CompositeModelFactory:
    """Route account model IDs to account credentials and local IDs to the local factory."""

    def __init__(self, local_factory: Any, account_factory: AccountModelFactory) -> None:
        self._local_factory = local_factory
        self._account_factory = account_factory

    def __call__(self, model_spec: str, *, reasoning_mode: str = "off") -> Any:
        if model_spec.startswith("account:"):
            return self._account_factory(model_spec, reasoning_mode=reasoning_mode)
        try:
            return self._local_factory(model_spec, reasoning_mode=reasoning_mode)
        except TypeError:
            return self._local_factory(model_spec)


def parse_account_model_id(value: str) -> tuple[str, str]:
    prefix, separator, remainder = value.partition(":")
    provider_id, nested_separator, model_id = remainder.partition(":")
    if (
        prefix != "account"
        or not separator
        or not nested_separator
        or not provider_id
        or not model_id
    ):
        raise ValueError("invalid account model id")
    return provider_id, model_id
