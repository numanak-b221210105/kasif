"""Sorguları salt-okunur bağlantıda, süre ve satır sınırıyla çalıştırır."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class QueryTimeout(RuntimeError):
    pass


def connect_ro(path: Path) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, check_same_thread=False)
    con.execute("PRAGMA query_only = ON")
    return con


def run(path: Path, sql: str, max_rows: int = 1000, timeout_s: float = 5.0) -> tuple[list[str], list[list], bool, int]:
    """(sütunlar, satırlar, kırpıldı mı, süre ms)"""
    t0 = time.perf_counter()
    deadline = t0 + timeout_s
    con = connect_ro(path)
    con.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 10_000)
    try:
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description or []]
        rows = [list(r) for r in cur.fetchmany(max_rows + 1)]
    except sqlite3.OperationalError as e:
        if "interrupt" in str(e).lower():
            raise QueryTimeout(f"Sorgu {timeout_s:g} saniyede tamamlanmadı ve durduruldu.") from None
        raise
    finally:
        con.close()
    truncated = len(rows) > max_rows
    return cols, rows[:max_rows], truncated, round((time.perf_counter() - t0) * 1000)
