"""Authenticated account Provider and model APIs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from api.cloud_dependencies import require_authenticated_user
from api.schemas import (
    CloudModelDefaultRequest,
    CloudModelDefaultResponse,
    CloudModelDiscoveryResponse,
    CloudModelInput,
    CloudModelProviderCreateRequest,
    CloudModelProviderResponse,
    CloudModelProvidersResponse,
    CloudModelProviderTestResponse,
    CloudModelProviderUpdateRequest,
    CloudModelResponse,
)
from core.cloud.model_providers import (
    CloudModelProvider,
    ModelInput,
    ModelProviderRepository,
    ProviderProbe,
    ProviderProbeError,
    RuntimeProviderCredential,
    validate_provider_base_url,
)

router = APIRouter(prefix="/api/v1/cloud", tags=["cloud-model-providers"])


def _repository(request: Request) -> ModelProviderRepository:
    repository = getattr(request.app.state, "cloud_model_provider_repository", None)
    if not isinstance(repository, ModelProviderRepository):
        raise HTTPException(status_code=503, detail="model Provider service is unavailable")
    return repository


def _probe(request: Request) -> ProviderProbe:
    probe = getattr(request.app.state, "cloud_model_provider_probe", None)
    if not isinstance(probe, ProviderProbe):
        raise HTTPException(status_code=503, detail="model Provider probe is unavailable")
    return probe


@router.get("/model-providers", response_model=CloudModelProvidersResponse)
async def list_model_providers(request: Request, response: Response) -> CloudModelProvidersResponse:
    user = await require_authenticated_user(request)
    providers = await _repository(request).list_providers(user.user_id)
    default = await _repository(request).get_default(user.user_id)
    response.headers["Cache-Control"] = "no-store"
    return CloudModelProvidersResponse(
        providers=[_provider_response(provider) for provider in providers],
        default_model=default.runtime_model_id if default is not None else None,
    )


@router.post("/model-providers", response_model=CloudModelProviderResponse)
async def create_model_provider(
    payload: CloudModelProviderCreateRequest, request: Request, response: Response
) -> CloudModelProviderResponse:
    user = await require_authenticated_user(request)
    base_url = _validated_base_url(request, payload.base_url)
    try:
        provider = await _repository(request).create_provider(
            owner_user_id=user.user_id,
            name=payload.name,
            api_mode=payload.api_mode,
            base_url=base_url,
            api_key=_validated_api_key(payload.api_key.get_secret_value()),
            models=[_model_input(item) for item in payload.models],
            default_model_id=payload.default_model_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return _provider_response(provider)


@router.patch("/model-providers/{provider_id}", response_model=CloudModelProviderResponse)
async def update_model_provider(
    provider_id: str,
    payload: CloudModelProviderUpdateRequest,
    request: Request,
    response: Response,
) -> CloudModelProviderResponse:
    user = await require_authenticated_user(request)
    base_url = _validated_base_url(request, payload.base_url) if payload.base_url else None
    try:
        provider = await _repository(request).update_provider(
            owner_user_id=user.user_id,
            provider_id=provider_id,
            name=payload.name,
            api_mode=payload.api_mode,
            base_url=base_url,
            api_key=(
                _validated_api_key(payload.api_key.get_secret_value())
                if payload.api_key is not None
                else None
            ),
            models=(
                [_model_input(item) for item in payload.models]
                if payload.models is not None
                else None
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if provider is None:
        raise HTTPException(status_code=404, detail="model Provider not found")
    response.headers["Cache-Control"] = "no-store"
    return _provider_response(provider)


@router.delete("/model-providers/{provider_id}", status_code=204)
async def delete_model_provider(provider_id: str, request: Request) -> Response:
    user = await require_authenticated_user(request)
    try:
        deleted = await _repository(request).delete_provider(user.user_id, provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="model Provider not found")
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.put("/model-default", response_model=CloudModelDefaultResponse)
async def set_model_default(
    payload: CloudModelDefaultRequest, request: Request, response: Response
) -> CloudModelDefaultResponse:
    user = await require_authenticated_user(request)
    default = await _repository(request).set_default(
        owner_user_id=user.user_id,
        provider_id=payload.provider_id,
        model_id=payload.model_id,
    )
    if default is None:
        raise HTTPException(status_code=404, detail="model Provider or model not found")
    response.headers["Cache-Control"] = "no-store"
    return CloudModelDefaultResponse(
        provider_id=default.provider_id,
        model_id=payload.model_id,
        runtime_model_id=default.runtime_model_id,
    )


@router.post(
    "/model-providers/{provider_id}/test",
    response_model=CloudModelProviderTestResponse,
)
async def test_model_provider(
    provider_id: str, request: Request, response: Response
) -> CloudModelProviderTestResponse:
    user = await require_authenticated_user(request)
    credential = await _owned_credential(request, user.user_id, provider_id)
    try:
        await _probe(request).test(credential)
    except ProviderProbeError as exc:
        await _repository(request).record_probe(
            owner_user_id=user.user_id, provider_id=provider_id, ok=False
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    provider = await _repository(request).record_probe(
        owner_user_id=user.user_id, provider_id=provider_id, ok=True
    )
    if provider is None or provider.last_tested_at is None:
        raise HTTPException(status_code=404, detail="model Provider not found")
    response.headers["Cache-Control"] = "no-store"
    return CloudModelProviderTestResponse(
        ok=True,
        status=provider.status,
        tested_at=provider.last_tested_at.isoformat(),
    )


@router.post(
    "/model-providers/{provider_id}/discover-models",
    response_model=CloudModelDiscoveryResponse,
)
async def discover_provider_models(
    provider_id: str, request: Request, response: Response
) -> CloudModelDiscoveryResponse:
    user = await require_authenticated_user(request)
    credential = await _owned_credential(request, user.user_id, provider_id)
    try:
        models = await _probe(request).discover(credential)
    except ProviderProbeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    response.headers["Cache-Control"] = "no-store"
    return CloudModelDiscoveryResponse(models=models)


async def _owned_credential(
    request: Request, owner_user_id: str, provider_id: str
) -> RuntimeProviderCredential:
    credentials = await _repository(request).runtime_credentials(owner_user_id)
    credential = next((item for item in credentials if item.provider_id == provider_id), None)
    if credential is None:
        raise HTTPException(status_code=404, detail="model Provider not found")
    return credential


def _validated_base_url(request: Request, base_url: str) -> str:
    try:
        return validate_provider_base_url(base_url, app_env=str(request.app.state.cloud_app_env))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _validated_api_key(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 10_000:
        raise HTTPException(status_code=422, detail="API key is invalid")
    return normalized


def _model_input(value: CloudModelInput) -> ModelInput:
    return ModelInput(
        model_id=value.model_id,
        display_name=value.display_name,
        context_window_tokens=value.context_window_tokens,
        output_reserve_tokens=value.output_reserve_tokens,
        reasoning_supported=value.reasoning_supported,
    )


def _provider_response(provider: CloudModelProvider) -> CloudModelProviderResponse:
    return CloudModelProviderResponse(
        id=provider.id,
        name=provider.name,
        api_mode=provider.api_mode,
        base_url=provider.base_url,
        key_configured=True,
        key_hint=provider.key_hint,
        status=provider.status,
        last_tested_at=(
            provider.last_tested_at.isoformat() if provider.last_tested_at is not None else None
        ),
        models=[
            CloudModelResponse(
                id=model.id,
                runtime_id=model.runtime_id,
                model_id=model.model_id,
                display_name=model.display_name,
                context_window_tokens=model.context_window_tokens,
                output_reserve_tokens=model.output_reserve_tokens,
                reasoning_supported=model.reasoning_supported,
            )
            for model in provider.models
        ],
    )
