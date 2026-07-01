"""LLM Provider - OpenAI 兼容格式"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_THINK_RE = re.compile(r"<think>(.*?)</think>", re.DOTALL)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    thinking: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMProvider:
    """OpenAI 兼容 LLM 提供者"""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "",
        enable_thinking: bool = False,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.enable_thinking = enable_thinking

        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**client_kwargs)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.7,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error("LLM 调用失败: %s", e)
            raise

        choice = response.choices[0]
        message = choice.message

        content = message.content
        thinking = None

        if content and "<think>" in content:
            match = _THINK_RE.search(content)
            if match:
                thinking = match.group(1).strip()
                content = content[match.end():].strip()

        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}
                    logger.warning("工具调用参数解析失败: %s", tc.function.arguments)
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        usage = response.usage
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            thinking=thinking,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )

    async def simple_chat(
        self,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        """简单对话，不支持工具调用，直接返回文本"""
        messages = [{"role": "user", "content": prompt}]
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            content = response.choices[0].message.content or ""
            if content and "<think>" in content:
                match = _THINK_RE.search(content)
                if match:
                    content = content[match.end():].strip()
            return content
        except Exception as e:
            logger.error("LLM simple_chat 失败: %s", e)
            return f"调用失败: {e}"
