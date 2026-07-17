"""Optional authenticated account model projection for coding routes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Request

from api.cloud_dependencies import SESSION_COOKIE
from core.cloud.auth.repository import CloudRepository
from core.cloud.model_providers import (
    AccountModelFactory,
    CompositeModelFactory,
    ModelProviderRepository,
    ProviderProbe,
)
from core.coding.context import ModelCapabilityRegistry


@dataclass(frozen=True, slots=True)
class AccountModelContext:
    user_id: str
    catalog: tuple[dict[str, Any], ...]
    default_model: str | None
    capabilities: dict[str, object]
    reasoning_modes: dict[str, tuple[str, ...]]
    factory: AccountModelFactory | None


async def load_account_model_context(
    request: Request, *, include_credentials: bool = False
) -> AccountModelContext | None:
    cloud = getattr(request.app.state, "cloud_repository", None)
    providers = getattr(request.app.state, "cloud_model_provider_repository", None)
    if not isinstance(cloud, CloudRepository) or not isinstance(providers, ModelProviderRepository):
        return None
    user = await cloud.authenticated_user(request.cookies.get(SESSION_COOKIE, ""))
    if user is None:
        return None
    configured = await providers.list_providers(user.user_id)
    default = await providers.get_default(user.user_id)
    catalog: list[dict[str, Any]] = []
    capabilities: dict[str, object] = {}
    reasoning_modes: dict[str, tuple[str, ...]] = {}
    for provider in configured:
        for model in provider.models:
            catalog.append(
                {
                    "id": model.runtime_id,
                    "label": model.display_name,
                    "provider": provider.name,
                    "provider_id": provider.id,
                    "connection_status": provider.status,
                    "reasoning_modes": (
                        ["low", "medium", "high"] if model.reasoning_supported else []
                    ),
                }
            )
            if model.context_window_tokens is not None:
                capabilities[model.runtime_id] = {
                    "context_window_tokens": model.context_window_tokens,
                    "output_reserve_tokens": model.output_reserve_tokens
                    or min(20_000, max(1, model.context_window_tokens // 5)),
                }
            reasoning_modes[model.runtime_id] = (
                ("low", "medium", "high") if model.reasoning_supported else ()
            )
    return AccountModelContext(
        user_id=user.user_id,
        catalog=tuple(catalog),
        default_model=default.runtime_model_id if default is not None else None,
        capabilities=capabilities,
        reasoning_modes=reasoning_modes,
        factory=await _account_factory(request, providers, user.user_id)
        if include_credentials
        else None,
    )


def combined_catalog(request: Request, account: AccountModelContext | None) -> list[dict[str, Any]]:
    return [
        *request.app.state.coding_model_catalog,
        *(list(account.catalog) if account is not None else []),
    ]


def combined_capabilities(
    request: Request, account: AccountModelContext | None
) -> ModelCapabilityRegistry:
    values: dict[str, object] = {}
    local: ModelCapabilityRegistry = request.app.state.coding_model_capabilities
    for item in request.app.state.coding_model_catalog:
        model_id = str(item["id"])
        policy = local.resolve(model_id)
        if policy is not None:
            values[model_id] = {
                "context_window_tokens": policy.context_window_tokens,
                "output_reserve_tokens": policy.output_reserve_tokens,
            }
    if account is not None:
        values.update(account.capabilities)
    return ModelCapabilityRegistry(values)


def combined_reasoning_modes(
    request: Request, account: AccountModelContext | None
) -> dict[str, tuple[str, ...]]:
    values = dict(request.app.state.coding_model_reasoning_modes)
    if account is not None:
        values.update(account.reasoning_modes)
    return values


def combined_model_factory(request: Request, account: AccountModelContext | None) -> Any:
    local = request.app.state.coding_model_factory
    if account is None:
        return local
    if account.factory is None:
        raise RuntimeError("account model credentials were not loaded")
    return CompositeModelFactory(local, account.factory)


async def _account_factory(
    request: Request,
    providers: ModelProviderRepository,
    owner_user_id: str,
) -> AccountModelFactory:
    probe = getattr(request.app.state, "cloud_model_provider_probe", None)
    if not isinstance(probe, ProviderProbe):
        raise RuntimeError("model Provider probe is unavailable")
    credentials = await providers.runtime_credentials(owner_user_id)
    return AccountModelFactory([await probe.pin(item) for item in credentials])
