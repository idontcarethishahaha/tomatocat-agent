from __future__ import annotations

import logging
from typing import List

import numpy as np
from openai import AsyncOpenAI

log = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, api_key: str, base_url: str, model: str):
        self.model = model
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._cache: dict[str, np.ndarray] = {}

    async def embed(self, text: str) -> np.ndarray:
        if not text or not text.strip():
            return np.zeros(1024, dtype=np.float32)
        if text in self._cache:
            return self._cache[text]

        try:
            response = await self._client.embeddings.create(
                model=self.model,
                input=text,
            )
            vec = np.array(response.data[0].embedding, dtype=np.float32)
            self._cache[text] = vec
            return vec
        except Exception as e:
            log.warning(f"[embedding] 嵌入失败: {e}")
            return np.zeros(1024, dtype=np.float32)

    async def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        results = []
        for t in texts:
            results.append(await self.embed(t))
        return results

    def cosine_similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
