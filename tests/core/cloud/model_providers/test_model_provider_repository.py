"""Encrypted account Provider repository tests."""

from collections.abc import AsyncIterator

import pytest

from core.cloud.model_providers import ModelInput, ModelProviderRepository
from db.database import create_engine, create_session_factory
from db.migrations import init_db


@pytest.fixture
async def repository() -> AsyncIterator[ModelProviderRepository]:
    engine = create_engine("sqlite+aiosqlite:///:memory:")
    factory = create_session_factory(engine)
    await init_db(engine)
    try:
        yield ModelProviderRepository(factory, encryption_secret="provider-secret-" * 4)
    finally:
        await engine.dispose()


async def test_provider_key_is_encrypted_and_never_returned(
    repository: ModelProviderRepository,
) -> None:
    provider = await repository.create_provider(
        owner_user_id="user-a",
        name="OpenAI",
        api_mode="openai_responses",
        base_url="https://api.openai.com/v1",
        api_key="sk-plaintext-secret",
        models=[ModelInput("gpt-5", "GPT-5", 200_000, 20_000, True)],
        default_model_id="gpt-5",
    )

    listed = await repository.list_providers("user-a")
    credentials = await repository.runtime_credentials("user-a")

    assert listed == [provider]
    assert provider.key_hint == "••••cret"
    assert "plaintext" not in repr(provider)
    assert await repository.raw_api_key_is_persisted("sk-plaintext-secret") is False
    assert credentials[0].api_key == "sk-plaintext-secret"
    assert credentials[0].api_mode == "openai_responses"
    assert (await repository.get_default("user-a")).runtime_model_id == (
        f"account:{provider.id}:gpt-5"
    )


async def test_provider_ids_are_owner_scoped_and_key_update_is_optional(
    repository: ModelProviderRepository,
) -> None:
    provider = await repository.create_provider(
        owner_user_id="user-a",
        name="Anthropic",
        api_mode="anthropic_messages",
        base_url="https://api.anthropic.com",
        api_key="anthropic-original-key",
        models=[ModelInput("claude-sonnet", "Claude Sonnet")],
    )

    assert await repository.get_provider("user-b", provider.id) is None
    assert await repository.update_provider(
        owner_user_id="user-b", provider_id=provider.id, name="Stolen"
    ) is None

    updated = await repository.update_provider(
        owner_user_id="user-a",
        provider_id=provider.id,
        name="Anthropic Production",
        base_url="https://api.anthropic.com/v1",
    )

    assert updated is not None
    assert updated.name == "Anthropic Production"
    assert (await repository.runtime_credentials("user-a"))[0].api_key == (
        "anthropic-original-key"
    )


async def test_default_provider_cannot_be_deleted_or_drop_its_default_model(
    repository: ModelProviderRepository,
) -> None:
    provider = await repository.create_provider(
        owner_user_id="user-a",
        name="Compatible",
        api_mode="openai_chat_completions",
        base_url="https://provider.example/v1",
        api_key="secret-key-value",
        models=[ModelInput("model-a", "Model A"), ModelInput("model-b", "Model B")],
        default_model_id="model-a",
    )

    with pytest.raises(ValueError, match="default Provider"):
        await repository.delete_provider("user-a", provider.id)
    with pytest.raises(ValueError, match="default model"):
        await repository.update_provider(
            owner_user_id="user-a",
            provider_id=provider.id,
            models=[ModelInput("model-b", "Model B")],
        )

    default = await repository.set_default(
        owner_user_id="user-a", provider_id=provider.id, model_id="model-b"
    )

    assert default is not None
    assert default.runtime_model_id.endswith(":model-b")


async def test_model_metadata_updates_in_place_and_preserves_default(
    repository: ModelProviderRepository,
) -> None:
    provider = await repository.create_provider(
        owner_user_id="user-a",
        name="Editable",
        api_mode="openai_responses",
        base_url="https://provider.example/v1",
        api_key="secret-key-value",
        models=[ModelInput("model-a", "Model A", 128_000, 16_000, False)],
        default_model_id="model-a",
    )

    updated = await repository.update_provider(
        owner_user_id="user-a",
        provider_id=provider.id,
        models=[
            ModelInput("model-a", "Model A New", 256_000, 32_000, True),
            ModelInput("model-b", "Model B"),
        ],
    )

    assert updated is not None
    assert [(model.model_id, model.display_name) for model in updated.models] == [
        ("model-a", "Model A New"),
        ("model-b", "Model B"),
    ]
    assert updated.models[0].reasoning_supported is True
    assert (await repository.get_default("user-a")).runtime_model_id == (
        f"account:{provider.id}:model-a"
    )


async def test_creating_a_new_default_provider_replaces_account_preference(
    repository: ModelProviderRepository,
) -> None:
    first = await repository.create_provider(
        owner_user_id="user-a",
        name="First",
        api_mode="openai_chat_completions",
        base_url="https://first.example/v1",
        api_key="first-secret",
        models=[ModelInput("model-a", "Model A")],
        default_model_id="model-a",
    )

    second = await repository.create_provider(
        owner_user_id="user-a",
        name="Second",
        api_mode="anthropic_messages",
        base_url="https://second.example/v1",
        api_key="second-secret",
        models=[ModelInput("model-b", "Model B")],
        default_model_id="model-b",
    )

    default = await repository.get_default("user-a")
    assert default is not None
    assert default.runtime_model_id == f"account:{second.id}:model-b"
    assert default.provider_id != first.id
