"""Direct account credential injection tests for all supported API modes."""

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from core.cloud.model_providers import (
    AccountModelFactory,
    CloudModel,
    ProviderDestination,
    RuntimeProviderCredential,
)
from core.llm_responses import ResponsesAPIChatModel


def _credential(
    mode: str, provider_id: str, *, reasoning: bool = True
) -> RuntimeProviderCredential:
    model = CloudModel(
        id=f"record-{provider_id}",
        provider_id=provider_id,
        model_id="model-a",
        display_name="Model A",
        context_window_tokens=128_000,
        output_reserve_tokens=16_000,
        reasoning_supported=reasoning,
    )
    return RuntimeProviderCredential(
        provider_id=provider_id,
        api_mode=mode,  # type: ignore[arg-type]
        base_url=f"https://{provider_id}.example/v1",
        api_key=f"secret-{provider_id}",
        models=(model,),
        destination=ProviderDestination(
            base_url=f"https://{provider_id}.example/v1",
            hostname=f"{provider_id}.example",
            addresses=("93.184.216.34",),
        ),
    )


def test_account_factory_routes_three_modes_without_mutating_environment() -> None:
    factory = AccountModelFactory(
        [
            _credential("openai_chat_completions", "chat"),
            _credential("openai_responses", "responses"),
            _credential("anthropic_messages", "anthropic"),
        ]
    )

    with patch.dict("os.environ", {}, clear=True):
        chat = factory("account:chat:model-a", reasoning_mode="medium")
        responses = factory("account:responses:model-a", reasoning_mode="high")
        anthropic = factory("account:anthropic:model-a", reasoning_mode="low")
        environment_after = dict(os.environ)

    assert isinstance(chat, ChatOpenAI)
    assert chat.openai_api_key.get_secret_value() == "secret-chat"
    assert chat.reasoning_effort == "medium"
    assert isinstance(responses, ResponsesAPIChatModel)
    assert responses.reasoning_effort == "high"
    assert isinstance(anthropic, ChatAnthropic)
    assert anthropic.anthropic_api_key.get_secret_value() == "secret-anthropic"
    assert anthropic.thinking == {"type": "enabled", "budget_tokens": 1024}
    assert environment_after == {}


def test_account_factory_rejects_cross_provider_and_undeclared_reasoning() -> None:
    factory = AccountModelFactory([_credential("openai_chat_completions", "chat", reasoning=False)])

    with pytest.raises(ValueError, match="Provider is unavailable"):
        factory("account:other:model-a")
    with pytest.raises(ValueError, match="unsupported reasoning"):
        factory("account:chat:model-a", reasoning_mode="high")


async def test_responses_adapter_streams_public_text_and_usage() -> None:
    class Stream:
        def __init__(self) -> None:
            self.events = iter(
                [
                    SimpleNamespace(type="response.output_text.delta", delta="<final>ok"),
                    SimpleNamespace(
                        type="response.completed",
                        response=SimpleNamespace(
                            usage=SimpleNamespace(input_tokens=10, output_tokens=4, total_tokens=14)
                        ),
                    ),
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self) -> object:
            try:
                return next(self.events)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class Responses:
        async def create(self, **kwargs: object) -> object:
            if kwargs.get("stream") is True:
                return Stream()
            return SimpleNamespace(
                output_text="<final>ok</final>",
                usage=SimpleNamespace(input_tokens=10, output_tokens=4, total_tokens=14),
            )

    fake_client = SimpleNamespace(responses=Responses())
    model = ResponsesAPIChatModel(
        api_key="secret",
        base_url="https://api.example/v1",
        model="model-a",
        client=fake_client,  # type: ignore[arg-type]
    )

    response = await model.ainvoke([{"role": "user", "content": "hello"}])
    chunks = [chunk async for chunk in model.astream([{"role": "user", "content": "hello"}])]

    assert response.content == "<final>ok</final>"
    assert response.usage_metadata == {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
    }
    assert chunks[0].content == "<final>ok"
    assert chunks[1].usage_metadata == response.usage_metadata
