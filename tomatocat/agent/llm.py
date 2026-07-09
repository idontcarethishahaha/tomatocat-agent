"""LLM Provider - OpenAI 兼容格式，支持流式输出"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# 国内 API 域名直连，不走代理
_DOMESTIC_API_HOSTS = {"open.bigmodel.cn", "api.minimax.chat", "dashscope.aliyuncs.com"}


def _should_bypass_proxy(base_url: str) -> bool:
    """判断 base_url 是否为国内 API，应该直连不走代理"""
    if not base_url:
        return False
    try:
        from urllib.parse import urlparse
        host = urlparse(base_url).hostname or ""
        return any(h in host for h in _DOMESTIC_API_HOSTS)
    except Exception:
        return False


def _clean_proxy_env() -> None:
    """如果环境变量中设了代理，清除掉以免影响国内 API 连接"""
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        val = os.environ.get(key, "")
        if val:
            logger.info("[llm] 检测到代理环境变量 %s=%s，将使用 httpx 直连国内 API", key, val)

_StreamDelta = dict[str, str]


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
    """OpenAI 兼容 LLM 提供者，支持流式输出"""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "",
        enable_thinking: bool = False,
    ) -> None:
        self.model = model
        self.api_key = api_key or "placeholder"
        self.base_url = base_url
        self.enable_thinking = enable_thinking
        self._valid = bool(api_key and api_key.strip() and api_key != "placeholder")

        client_kwargs: dict[str, Any] = {
            "api_key": self.api_key,
            "timeout": 120.0,        # 请求超时 120 秒
            "max_retries": 2,         # 最多重试 2 次
        }
        if base_url:
            client_kwargs["base_url"] = base_url

        # 国内 API 直连，绕过代理
        if _should_bypass_proxy(base_url):
            import httpx
            client_kwargs["http_client"] = httpx.AsyncClient(
                proxy=None,
                timeout=httpx.Timeout(120.0, connect=30.0),
            )
            logger.info("[llm] %s 为国内 API，已绕过代理直连", base_url)

        self._client = AsyncOpenAI(**client_kwargs)

    def _check_valid(self) -> None:
        if not self._valid:
            raise ValueError("LLM API key 未配置，请在 config.toml 中设置 llm.main.api_key")

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.7,
        on_delta: Callable[[_StreamDelta], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        self._check_valid()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if self.enable_thinking:
            kwargs["extra_body"] = {"enable_thinking": True}

        if on_delta is not None:
            return await self._chat_streaming(kwargs, on_delta)

        return await self._chat_non_streaming(kwargs)

    async def _chat_non_streaming(self, kwargs: dict[str, Any]) -> LLMResponse:
        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error("LLM 调用失败: %s", e)
            raise

        choice = response.choices[0]
        message = choice.message

        content = message.content
        thinking = None

        reasoning_content = getattr(message, "reasoning_content", None)
        if reasoning_content:
            thinking = str(reasoning_content)

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

    async def _chat_streaming(self, kwargs: dict[str, Any], on_delta: Callable[[_StreamDelta], Awaitable[None]]) -> LLMResponse:
        kwargs["stream"] = True
        stream_options = kwargs.get("stream_options", {})
        stream_options["include_usage"] = True
        kwargs["stream_options"] = stream_options

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except Exception as e:
            logger.error("LLM 流式调用失败: %s", e)
            raise

        content_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_call_chunks: dict[int, dict[str, str]] = {}
        tool_call_seen = False

        async for chunk in stream:
            choices = getattr(chunk, "choices", []) or []
            if not choices:
                continue

            choice = choices[0]
            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            reasoning_piece = getattr(delta, "reasoning_content", None)
            if isinstance(reasoning_piece, str) and reasoning_piece:
                thinking_parts.append(reasoning_piece)
                if not tool_call_seen:
                    await on_delta({"thinking_delta": reasoning_piece})

            content_piece = getattr(delta, "content", None)
            if isinstance(content_piece, str) and content_piece:
                content_parts.append(content_piece)
                if not tool_call_seen:
                    await on_delta({"content_delta": content_piece})

            delta_tool_calls = getattr(delta, "tool_calls", None) or []
            for tc in delta_tool_calls:
                tool_call_seen = True
                chunk_index = int(tc.index)
                slot = tool_call_chunks.setdefault(chunk_index, {})
                tc_id = getattr(tc.id, "id", "") if tc.id else ""
                tc_name = getattr(tc.function, "name", "") if tc.function else ""
                tc_args = getattr(tc.function, "arguments", "") if tc.function else ""
                if tc_id:
                    slot["id"] = slot.get("id", "") + tc_id
                if tc_name:
                    slot["name"] = slot.get("name", "") + tc_name
                if tc_args:
                    slot["arguments"] = slot.get("arguments", "") + tc_args

        tool_calls: list[ToolCall] = []
        for idx in sorted(tool_call_chunks):
            item = tool_call_chunks[idx]
            raw_args = item.get("arguments", "") or "{}"
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError:
                args = {}
                logger.warning("工具调用参数解析失败: %s", raw_args)
            tool_calls.append(ToolCall(
                id=item.get("id", ""),
                name=item.get("name", ""),
                arguments=args,
            ))

        content = "".join(content_parts).strip() or None
        thinking = "".join(thinking_parts).strip() or None

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            thinking=thinking,
        )

    async def simple_chat(
        self,
        prompt: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        self._check_valid()
        messages = [{"role": "user", "content": prompt}]
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            content = response.choices[0].message.content or ""
            return content
        except Exception as e:
            logger.error("LLM simple_chat 失败: %s", e)
            return f"调用失败: {e}"
