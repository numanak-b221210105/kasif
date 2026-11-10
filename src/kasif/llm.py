"""OpenAI uyumlu LLM istemcisi (Ollama, OpenAI, Gemini, Groq...).

LLM yoksa uygulama kural motoruyla çalışmaya devam eder; bu modül bağlantı
olmadığında istisna yerine `reachable() == False` döndürür.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from .config import Settings

_cache: dict[str, tuple[float, bool]] = {}


def _client(s: Settings) -> Any:
    from openai import OpenAI

    return OpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key, timeout=httpx.Timeout(120, connect=3), max_retries=1)


def reachable(s: Settings, ttl: float = 20) -> bool:
    """LLM sunucusu ayakta mı? Sonuç kısa süre önbelleğe alınır."""
    now = time.monotonic()
    hit = _cache.get(s.llm_base_url)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        httpx.get(f"{s.llm_base_url}/models", timeout=1.5)
        ok = True
    except httpx.HTTPError:
        ok = False
    _cache[s.llm_base_url] = (now, ok)
    return ok


def chat(s: Settings, messages: list[dict], client: Any | None = None) -> str:
    res = (client or _client(s)).chat.completions.create(model=s.llm_model, temperature=0, messages=messages)
    return res.choices[0].message.content or ""
