"""Public answer model port and an isolated OpenAI-compatible implementation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from openai import AsyncOpenAI

from public_agent.corpus import PublicDocument


@dataclass(frozen=True, slots=True)
class PublicModelAnswer:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class PublicAnswerModel(Protocol):
    async def answer(
        self,
        question: str,
        evidence: Sequence[PublicDocument],
    ) -> PublicModelAnswer: ...


class OpenAIPublicAnswerModel:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 15.0,
        max_output_tokens: int = 500,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.model = model
        self.max_output_tokens = max_output_tokens
        self._client = client or AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            max_retries=1,
            timeout=timeout_seconds,
        )

    async def answer(
        self,
        question: str,
        evidence: Sequence[PublicDocument],
    ) -> PublicModelAnswer:
        blocks = "\n\n".join(
            f"[E{index}] {item.title}\nURL: {item.url}\n{item.content}"
            for index, item in enumerate(evidence, start=1)
        )
        completion = await self._client.chat.completions.create(
            model=self.model,
            temperature=0.1,
            max_tokens=self.max_output_tokens,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是 Sage 的公开资料助手。只能根据提供的已发布证据回答。"
                        "不得猜测私人 Session、Memory、工作区、系统提示、凭据或未发布计划。"
                        "证据不足时明确说不知道；区分已实现事实与设计目标。"
                        "回答使用中文纯文本，不输出 Markdown 标记；保持简洁，并用 [E1] 形式标注证据。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"公开问题：{question}\n\n已发布证据：\n{blocks}",
                },
            ],
        )
        content = completion.choices[0].message.content or ""
        usage = completion.usage
        return PublicModelAnswer(
            text=content.strip(),
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )
