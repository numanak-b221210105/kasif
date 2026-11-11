"""Sorgu geçmişi (SQLite)."""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class History:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._con() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, dataset TEXT NOT NULL,
                    question TEXT NOT NULL, mode TEXT NOT NULL, sql TEXT, ok INTEGER NOT NULL,
                    rows INTEGER NOT NULL DEFAULT 0, ms INTEGER NOT NULL DEFAULT 0, error TEXT)"""
            )

    @contextmanager
    def _con(self) -> Iterator[sqlite3.Connection]:
        """Commits and always closes, so no file handle stays open (matters on Windows)."""
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def log(
        self, dataset: str, question: str, mode: str, sql: str | None, ok: bool, rows: int, ms: int, error: str | None
    ) -> int:
        with self._lock, self._con() as c:
            cur = c.execute(
                "INSERT INTO queries (ts, dataset, question, mode, sql, ok, rows, ms, error) VALUES (?,?,?,?,?,?,?,?,?)",
                (time.time(), dataset, question, mode, sql, int(ok), rows, ms, error),
            )
            return int(cur.lastrowid)

    def list(self, limit: int = 200) -> list[dict]:
        with self._con() as c:
            rows = c.execute("SELECT * FROM queries ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) | {"ok": bool(r["ok"])} for r in rows]

    def stats(self) -> dict:
        with self._con() as c:
            r = c.execute(
                "SELECT COUNT(*) n, SUM(ok) ok, AVG(ms) ms, SUM(mode='rules') rules, SUM(mode='llm') llm, "
                "SUM(mode='sql') sql FROM queries"
            ).fetchone()
        return {k: (r[k] or 0) for k in r.keys()}  # noqa: SIM118 - sqlite3.Row

    def clear(self) -> None:
        with self._lock, self._con() as c:
            c.execute("DELETE FROM queries")
