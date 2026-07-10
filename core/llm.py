"""LLM factory - routes to OpenAI-compatible or Anthropic-native client."""

from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from core.llm_anthropic import create_anthropic_llm
from core.llm_openai import create_openai_llm

_ANTHROPIC_PROVIDERS = {"anthropic", "deepseek_anthropic"}


def create_llm(
    model_spec: str, temperature: float = 0.0, **kwargs: Any
) -> ChatOpenAI | ChatAnthropic:
    """Create an LLM client from a provider:model spec.

    Routes Anthropic-native providers (``anthropic``, ``deepseek_anthropic``)
    to :func:`core.llm_anthropic.create_anthropic_llm`; all other providers go
    to :func:`core.llm_openai.create_openai_llm`.

    Args:
        model_spec: Provider-prefixed model spec, such as
            ``doubao:Doubao-Seed-2.0-pro`` or ``deepseek:deepseek-v4-flash``.
        temperature: Sampling temperature. Defaults to deterministic output.
        **kwargs: Extra keyword arguments passed to the underlying client.

    Returns:
        A ``ChatOpenAI`` or ``ChatAnthropic`` instance. Both expose
        ``ainvoke`` and ``astream`` for async callers.
    """
    provider = model_spec.partition(":")[0].strip()
    if provider in _ANTHROPIC_PROVIDERS:
        return create_anthropic_llm(model_spec, temperature, **kwargs)
    return create_openai_llm(model_spec, temperature, **kwargs)
