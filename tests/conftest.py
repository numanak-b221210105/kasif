from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from kasif.config import Settings
from kasif.engine import Engine

TODAY = date(2026, 11, 10)


@dataclass
class _Msg:
    content: str


@dataclass
class _Choice:
    message: _Msg


@dataclass
class _Res:
    choices: list


class FakeLLM:
    """OpenAI istemcisi taklidi: sırayla önceden verilen yanıtları döndürür."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list[dict]] = []
        self.chat = self
        self.completions = self

    def create(self, model: str, temperature: float, messages: list[dict]):
        self.calls.append(messages)
        return _Res([_Choice(_Msg(self.replies.pop(0)))])


def cfg(tmp: Path) -> Settings:
    # 9 numaralı port kapalı: gerçek bir LLM'e asla bağlanılmaz
    return Settings(tmp, "http://127.0.0.1:9/v1", "x", "test-model", max_rows=1000, timeout_s=5)


@pytest.fixture(scope="session")
def shared_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("kasif")


@pytest.fixture
def engine(shared_dir) -> Engine:
    e = Engine(cfg(shared_dir), today=TODAY)
    e.registry.ensure_demo(TODAY)
    e.registry.activate("demo")
    return e
