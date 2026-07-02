"""Embedding 客户端 - 调用 OpenAI 兼容的 embedding API

参考 tomatocat 的 memory2/embedder.py，改用 httpx 替代 core.net.http。
支持批量 embed，自动分批。
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


class Embedder:
    MAX_BATCH = 10
    MAX_TEXT_LEN = 2000

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str = "text-embedding-v3",
        output_dimensionality: int | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/embeddings"
        self._key = api_key
        self._model = model
        self._output_dimensionality = output_dimensionality
        self._client = httpx.AsyncClient(timeout=30.0)

    async def embed(self, text: str) -> list[float]:
        """单条 embed"""
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """分批 embed，每批 ≤ MAX_BATCH，批间 sleep 0.3s"""
        results: list[list[float]] = []
        truncated = [t[: self.MAX_TEXT_LEN] for t in texts]

        for i in range(0, len(truncated), self.MAX_BATCH):
            batch = truncated[i : i + self.MAX_BATCH]
            payload: dict[str, object] = {"model": self._model, "input": batch}
            if self._output_dimensionality is not None:
                payload["dimensions"] = self._output_dimensionality

            try:
                resp = await self._client.post(
                    self._url,
                    headers={
                        "Authorization": f"Bearer {self._key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                data.sort(key=lambda x: x["index"])
                results.extend(d["embedding"] for d in data)
            except Exception as e:
                logger.error("[embedder] 批量 embed 失败 (batch %d): %s", i, e)
                raise

            if i + self.MAX_BATCH < len(truncated):
                await asyncio.sleep(0.3)

        return results

    async def close(self) -> None:
        await self._client.aclose()
