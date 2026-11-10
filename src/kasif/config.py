"""Ortam değişkenlerinden ayarlar. Hepsinin çalışan bir varsayılanı vardır."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    max_rows: int = 1000
    timeout_s: float = 5.0


def settings() -> Settings:
    return Settings(
        data_dir=Path(os.getenv("KASIF_DATA_DIR", "data")),
        llm_base_url=os.getenv("KASIF_LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/"),
        llm_api_key=os.getenv("KASIF_LLM_API_KEY", "ollama"),
        llm_model=os.getenv("KASIF_LLM_MODEL", "qwen2.5-coder:7b"),
        max_rows=int(os.getenv("KASIF_MAX_ROWS", "1000")),
        timeout_s=float(os.getenv("KASIF_TIMEOUT_S", "5")),
    )
