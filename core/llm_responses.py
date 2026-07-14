"""Minimal LangChain-compatible adapter for the OpenAI Responses API."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage, AIMessageChunk, UsageMetadata
from openai import AsyncOpenAI
from openai.types.responses.easy_input_message_param import EasyInputMessageParam
from openai.types.responses.response_input_param import ResponseInputParam
from openai.types.shared.reasoning_effort import ReasoningEffort
from openai.types.shared_params.reasoning import Reasoning


class ResponsesAPIChatModel:
    """Expose ``ainvoke``/``astream`` over ``client.responses`` for CodingRuntime."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        reasoning_effort: str | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.model_name = model
        self.base_url = base_url
        self.reasoning_effort = reasoning_effort
        self._client = client or AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def ainvoke(self, messages: Sequence[object]) -> AIMessage:
        request: dict[str, Any] = {
            "model": self.model,
            "input": _response_input(messages),
        }
        reasoning = _reasoning(self.reasoning_effort)
        if reasoning is not None:
            request["reasoning"] = reasoning
        response = await self._client.responses.create(**request)
        return AIMessage(
            content=str(getattr(response, "output_text", "")),
            usage_metadata=_usage_metadata(getattr(response, "usage", None)),
        )

    async def astream(self, messages: Sequence[object]) -> AsyncIterator[AIMessageChunk]:
        request: dict[str, Any] = {
            "model": self.model,
            "input": _response_input(messages),
            "stream": True,
        }
        reasoning = _reasoning(self.reasoning_effort)
        if reasoning is not None:
            request["reasoning"] = reasoning
        stream = cast(Any, await self._client.responses.create(**request))
        async for event in stream:
            event_type = str(getattr(event, "type", ""))
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "")
                if isinstance(delta, str) and delta:
                    yield AIMessageChunk(content=delta)
            elif event_type == "response.completed":
                response = getattr(event, "response", None)
                usage = _usage_metadata(getattr(response, "usage", None))
                if usage is not None:
                    yield AIMessageChunk(content="", usage_metadata=usage)


def _response_input(messages: Sequence[object]) -> ResponseInputParam:
    values: list[EasyInputMessageParam] = []
    for message in messages:
        if isinstance(message, dict):
            role = str(message.get("role", "user"))
            content = str(message.get("content", ""))
        else:
            role = str(getattr(message, "type", getattr(message, "role", "user")))
            role = "assistant" if role in {"ai", "assistant"} else "system" if role == "system" else "user"
            content = str(getattr(message, "content", ""))
        normalized_role: Literal["user", "assistant", "system", "developer"] = (
            cast(Literal["user", "assistant", "system", "developer"], role)
            if role in {"user", "assistant", "system", "developer"}
            else "user"
        )
        values.append({"role": normalized_role, "content": content})
    return cast(ResponseInputParam, values)


def _reasoning(effort: str | None) -> Reasoning | None:
    return {"effort": cast(ReasoningEffort, effort)} if effort else None


def _usage_metadata(value: Any) -> UsageMetadata | None:
    if value is None:
        return None
    input_tokens = int(getattr(value, "input_tokens", 0) or 0)
    output_tokens = int(getattr(value, "output_tokens", 0) or 0)
    total_tokens = int(getattr(value, "total_tokens", input_tokens + output_tokens) or 0)
    if input_tokens == 0 and output_tokens == 0 and total_tokens == 0:
        return None
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }
