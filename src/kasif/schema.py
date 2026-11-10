"""API modelleri."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    mode: Literal["auto", "rules", "llm"] = "auto"
    lang: Literal["tr", "en"] = "tr"


class SqlIn(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)
    lang: Literal["tr", "en"] = "tr"
    question: str | None = None


class Column(BaseModel):
    name: str
    role: str = Field(description="time | dim | metric | share")
    fmt: str = Field(description="text | period | int | num | money | pct")


class PlanStep(BaseModel):
    kind: str
    label: str
    text: str
    detail: str | None = None


class Attempt(BaseModel):
    sql: str
    error: str


class Answer(BaseModel):
    question: str
    ok: bool
    mode: str = Field(description="rules | llm | sql")
    sql: str | None = None
    plan: list[PlanStep] = []
    explanation: str | None = None
    columns: list[Column] = []
    rows: list[list[Any]] = []
    row_count: int = 0
    truncated: bool = False
    chart: dict = {}
    summary: list[str] = []
    warnings: list[str] = []
    error: str | None = None
    error_code: str | None = None
    unknown: list[str] = []
    can_force: bool = Field(False, description="Kural motoru kısmen anladı; yine de çalıştırılabilir")
    suggestions: list[str] = []
    followups: list[str] = []
    attempts: list[Attempt] = []
    tables: list[str] = []
    elapsed_ms: int = 0
    query_ms: int = 0
    query_id: int | None = None
    dataset: str = ""


class HistoryItem(BaseModel):
    id: int
    ts: float
    dataset: str
    question: str
    mode: str
    sql: str | None
    ok: bool
    rows: int
    ms: int
    error: str | None


class DatasetOut(BaseModel):
    id: str
    name: str
    kind: str
    created: float
    note: str = ""
    active: bool = False
    tables: int = 0
    rows: int = 0
