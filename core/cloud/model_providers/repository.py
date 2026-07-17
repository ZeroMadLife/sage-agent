"""Encrypted, owner-scoped persistence for cloud LLM Providers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.cloud.model_providers.models import (
    ApiMode,
    CloudModel,
    CloudModelDefault,
    CloudModelProvider,
    ModelInput,
    RuntimeProviderCredential,
)
from core.cloud.security import SecretCipher
from db.models import (
    CloudModelPreferenceRecord,
    CloudModelProviderRecord,
    CloudModelRecord,
)

_API_MODES = {
    "openai_chat_completions",
    "openai_responses",
    "anthropic_messages",
}


class ModelProviderRepository:
    """Persist LLM credentials separately from identity OAuth credentials."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        encryption_secret: str,
    ) -> None:
        self._session_factory = session_factory
        self._cipher = SecretCipher(encryption_secret)

    async def create_provider(
        self,
        *,
        owner_user_id: str,
        name: str,
        api_mode: ApiMode,
        base_url: str,
        api_key: str,
        models: Sequence[ModelInput],
        default_model_id: str | None = None,
    ) -> CloudModelProvider:
        provider_id = str(uuid4())
        normalized_key = api_key.strip()
        if not normalized_key:
            raise ValueError("API key is required")
        _validate_api_mode(api_mode)
        async with self._session_factory() as session:
            normalized_name = _required(name, "provider name")
            await _ensure_provider_name_available(
                session, owner_user_id=owner_user_id, name=normalized_name
            )
            record = CloudModelProviderRecord(
                id=provider_id,
                owner_user_id=owner_user_id,
                name=normalized_name,
                api_mode=api_mode,
                base_url=base_url,
                encrypted_api_key=self._cipher.encrypt(
                    normalized_key, purpose=_credential_purpose(provider_id)
                ),
                key_hint=_key_hint(normalized_key),
                status="untested",
            )
            session.add(record)
            model_records = _new_model_records(provider_id, models)
            session.add_all(model_records)
            if default_model_id:
                selected = _model_record_by_model_id(model_records, default_model_id)
                if selected is None:
                    raise ValueError("default model is not configured under this Provider")
                preference = await session.get(
                    CloudModelPreferenceRecord, owner_user_id
                )
                if preference is None:
                    session.add(
                        CloudModelPreferenceRecord(
                            user_id=owner_user_id,
                            provider_id=provider_id,
                            model_record_id=selected.id,
                        )
                    )
                else:
                    preference.provider_id = provider_id
                    preference.model_record_id = selected.id
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise _bounded_integrity_error(exc) from exc
            return _to_provider(record, model_records)

    async def list_providers(self, owner_user_id: str) -> list[CloudModelProvider]:
        async with self._session_factory() as session:
            records = (
                await session.scalars(
                    select(CloudModelProviderRecord)
                    .where(CloudModelProviderRecord.owner_user_id == owner_user_id)
                    .order_by(CloudModelProviderRecord.created_at, CloudModelProviderRecord.id)
                )
            ).all()
            return [await _provider_with_models(session, record) for record in records]

    async def get_provider(
        self, owner_user_id: str, provider_id: str
    ) -> CloudModelProvider | None:
        async with self._session_factory() as session:
            record = await _owned_provider_record(session, owner_user_id, provider_id)
            return await _provider_with_models(session, record) if record is not None else None

    async def update_provider(
        self,
        *,
        owner_user_id: str,
        provider_id: str,
        name: str | None = None,
        api_mode: ApiMode | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        models: Sequence[ModelInput] | None = None,
    ) -> CloudModelProvider | None:
        async with self._session_factory() as session:
            record = await _owned_provider_record(session, owner_user_id, provider_id)
            if record is None:
                return None
            connection_changed = False
            if name is not None:
                normalized_name = _required(name, "provider name")
                await _ensure_provider_name_available(
                    session,
                    owner_user_id=owner_user_id,
                    name=normalized_name,
                    exclude_provider_id=provider_id,
                )
                record.name = normalized_name
            if api_mode is not None:
                _validate_api_mode(api_mode)
                connection_changed = connection_changed or api_mode != record.api_mode
                record.api_mode = api_mode
            if base_url is not None:
                connection_changed = connection_changed or base_url != record.base_url
                record.base_url = base_url
            if api_key is not None:
                normalized_key = _required(api_key, "API key")
                record.encrypted_api_key = self._cipher.encrypt(
                    normalized_key, purpose=_credential_purpose(provider_id)
                )
                record.key_hint = _key_hint(normalized_key)
                connection_changed = True
            if models is not None:
                requested = _validated_model_inputs(models)
                preference = await session.scalar(
                    select(CloudModelPreferenceRecord).where(
                        CloudModelPreferenceRecord.user_id == owner_user_id,
                        CloudModelPreferenceRecord.provider_id == provider_id,
                    )
                )
                existing_models = (
                    await session.scalars(
                        select(CloudModelRecord).where(
                            CloudModelRecord.provider_id == provider_id
                        )
                    )
                ).all()
                existing_by_model_id = {item.model_id: item for item in existing_models}
                if preference is not None:
                    selected = next(
                        (item for item in existing_models if item.id == preference.model_record_id),
                        None,
                    )
                    if selected is not None and selected.model_id not in requested:
                        raise ValueError("default model must be changed before it is removed")
                for model_id, value in requested.items():
                    existing = existing_by_model_id.get(model_id)
                    if existing is None:
                        session.add(
                            _new_model_record(provider_id, value)
                        )
                        continue
                    existing.display_name = value.display_name.strip() or model_id
                    existing.context_window_tokens = value.context_window_tokens
                    existing.output_reserve_tokens = value.output_reserve_tokens
                    existing.reasoning_supported = value.reasoning_supported
                removed_ids = [
                    item.id
                    for item in existing_models
                    if item.model_id not in requested
                ]
                if removed_ids:
                    await session.execute(
                        delete(CloudModelRecord).where(
                            CloudModelRecord.id.in_(removed_ids)
                        )
                    )
            if connection_changed:
                record.status = "untested"
                record.last_tested_at = None
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise _bounded_integrity_error(exc) from exc
            model_records = list(
                (
                    await session.scalars(
                        select(CloudModelRecord).where(
                            CloudModelRecord.provider_id == provider_id
                        )
                    )
                ).all()
            )
            return _to_provider(record, model_records)

    async def delete_provider(self, owner_user_id: str, provider_id: str) -> bool:
        async with self._session_factory() as session:
            record = await _owned_provider_record(session, owner_user_id, provider_id)
            if record is None:
                return False
            preference = await session.scalar(
                select(CloudModelPreferenceRecord).where(
                    CloudModelPreferenceRecord.user_id == owner_user_id,
                    CloudModelPreferenceRecord.provider_id == provider_id,
                )
            )
            if preference is not None:
                raise ValueError("default Provider must be changed before deletion")
            await session.execute(
                delete(CloudModelRecord).where(CloudModelRecord.provider_id == provider_id)
            )
            await session.delete(record)
            await session.commit()
            return True

    async def set_default(
        self, *, owner_user_id: str, provider_id: str, model_id: str
    ) -> CloudModelDefault | None:
        async with self._session_factory() as session:
            provider = await _owned_provider_record(session, owner_user_id, provider_id)
            if provider is None:
                return None
            model = await session.scalar(
                select(CloudModelRecord).where(
                    CloudModelRecord.provider_id == provider_id,
                    CloudModelRecord.model_id == model_id,
                )
            )
            if model is None:
                return None
            preference = await session.get(CloudModelPreferenceRecord, owner_user_id)
            if preference is None:
                preference = CloudModelPreferenceRecord(
                    user_id=owner_user_id,
                    provider_id=provider_id,
                    model_record_id=model.id,
                )
                session.add(preference)
            else:
                preference.provider_id = provider_id
                preference.model_record_id = model.id
            await session.commit()
            return CloudModelDefault(provider_id, model.id, _to_model(model).runtime_id)

    async def get_default(self, owner_user_id: str) -> CloudModelDefault | None:
        async with self._session_factory() as session:
            preference = await session.get(CloudModelPreferenceRecord, owner_user_id)
            if preference is None:
                return None
            provider = await _owned_provider_record(
                session, owner_user_id, preference.provider_id
            )
            model = await session.get(CloudModelRecord, preference.model_record_id)
            if provider is None or model is None or model.provider_id != provider.id:
                return None
            return CloudModelDefault(provider.id, model.id, _to_model(model).runtime_id)

    async def runtime_credentials(
        self, owner_user_id: str
    ) -> tuple[RuntimeProviderCredential, ...]:
        providers = await self.list_providers(owner_user_id)
        credentials: list[RuntimeProviderCredential] = []
        async with self._session_factory() as session:
            for provider in providers:
                record = await _owned_provider_record(session, owner_user_id, provider.id)
                if record is None:
                    continue
                credentials.append(
                    RuntimeProviderCredential(
                        provider_id=provider.id,
                        api_mode=provider.api_mode,
                        base_url=provider.base_url,
                        api_key=self._cipher.decrypt(
                            record.encrypted_api_key,
                            purpose=_credential_purpose(provider.id),
                        ),
                        models=provider.models,
                    )
                )
        return tuple(credentials)

    async def record_probe(
        self, *, owner_user_id: str, provider_id: str, ok: bool
    ) -> CloudModelProvider | None:
        async with self._session_factory() as session:
            record = await _owned_provider_record(session, owner_user_id, provider_id)
            if record is None:
                return None
            record.status = "connected" if ok else "error"
            record.last_tested_at = datetime.now(UTC)
            await session.commit()
            return await _provider_with_models(session, record)

    async def raw_api_key_is_persisted(self, api_key: str) -> bool:
        """Test-only invariant probe for plaintext API keys."""
        async with self._session_factory() as session:
            values = (
                await session.scalars(select(CloudModelProviderRecord.encrypted_api_key))
            ).all()
            return api_key in values


async def _owned_provider_record(
    session: AsyncSession, owner_user_id: str, provider_id: str
) -> CloudModelProviderRecord | None:
    return cast(
        CloudModelProviderRecord | None,
        await session.scalar(
            select(CloudModelProviderRecord).where(
                CloudModelProviderRecord.id == provider_id,
                CloudModelProviderRecord.owner_user_id == owner_user_id,
            )
        )
    )


async def _ensure_provider_name_available(
    session: AsyncSession,
    *,
    owner_user_id: str,
    name: str,
    exclude_provider_id: str | None = None,
) -> None:
    statement = select(CloudModelProviderRecord.id).where(
        CloudModelProviderRecord.owner_user_id == owner_user_id,
        CloudModelProviderRecord.name == name,
    )
    if exclude_provider_id is not None:
        statement = statement.where(CloudModelProviderRecord.id != exclude_provider_id)
    if await session.scalar(statement) is not None:
        raise ValueError("Provider name already exists")


def _bounded_integrity_error(exc: IntegrityError) -> ValueError:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", "")
    if constraint_name == "cloud_model_provider_owner_name_key" or (
        "cloud_model_providers.owner_user_id, cloud_model_providers.name"
        in str(exc.orig)
    ):
        return ValueError("Provider name already exists")
    return ValueError("Provider configuration conflicts with an existing record")


async def _provider_with_models(
    session: AsyncSession, record: CloudModelProviderRecord
) -> CloudModelProvider:
    models = (
        await session.scalars(
            select(CloudModelRecord)
            .where(CloudModelRecord.provider_id == record.id)
            .order_by(CloudModelRecord.created_at, CloudModelRecord.id)
        )
    ).all()
    return _to_provider(record, models)


def _new_model_records(
    provider_id: str, models: Sequence[ModelInput]
) -> list[CloudModelRecord]:
    return [
        _new_model_record(provider_id, value)
        for value in _validated_model_inputs(models).values()
    ]


def _validated_model_inputs(
    models: Sequence[ModelInput],
) -> dict[str, ModelInput]:
    if not models:
        raise ValueError("at least one model is required")
    seen: set[str] = set()
    values: dict[str, ModelInput] = {}
    for value in models:
        model_id = _required(value.model_id, "model id")
        if model_id in seen:
            raise ValueError("model ids must be unique within a Provider")
        seen.add(model_id)
        if (
            value.context_window_tokens is None
            and value.output_reserve_tokens is not None
        ):
            raise ValueError("output reserve requires a context window")
        if (
            value.context_window_tokens is not None
            and value.output_reserve_tokens is not None
            and value.output_reserve_tokens >= value.context_window_tokens
        ):
            raise ValueError("output reserve must be less than context window")
        values[model_id] = value
    return values


def _new_model_record(provider_id: str, value: ModelInput) -> CloudModelRecord:
    model_id = _required(value.model_id, "model id")
    return CloudModelRecord(
        id=str(uuid4()),
        provider_id=provider_id,
        model_id=model_id,
        display_name=value.display_name.strip() or model_id,
        context_window_tokens=value.context_window_tokens,
        output_reserve_tokens=value.output_reserve_tokens,
        reasoning_supported=value.reasoning_supported,
    )


def _to_provider(
    record: CloudModelProviderRecord, models: Sequence[CloudModelRecord]
) -> CloudModelProvider:
    return CloudModelProvider(
        id=record.id,
        owner_user_id=record.owner_user_id,
        name=record.name,
        api_mode=cast(ApiMode, record.api_mode),
        base_url=record.base_url,
        key_hint=record.key_hint,
        status=record.status,
        last_tested_at=record.last_tested_at,
        models=tuple(_to_model(model) for model in models),
    )


def _to_model(record: CloudModelRecord) -> CloudModel:
    return CloudModel(
        id=record.id,
        provider_id=record.provider_id,
        model_id=record.model_id,
        display_name=record.display_name,
        context_window_tokens=record.context_window_tokens,
        output_reserve_tokens=record.output_reserve_tokens,
        reasoning_supported=record.reasoning_supported,
    )


def _model_record_by_model_id(
    records: Sequence[CloudModelRecord], model_id: str
) -> CloudModelRecord | None:
    return next((record for record in records if record.model_id == model_id), None)


def _credential_purpose(provider_id: str) -> str:
    return f"cloud-model-provider:{provider_id}:api-key"


def _key_hint(api_key: str) -> str:
    return f"••••{api_key[-4:]}" if len(api_key) >= 4 else "••••"


def _required(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} is required")
    return normalized


def _validate_api_mode(value: str) -> None:
    if value not in _API_MODES:
        raise ValueError("unsupported Provider API mode")
