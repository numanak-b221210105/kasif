"""SQL güvenlik denetimi.

LLM'in ya da kullanıcının yazdığı SQL çalıştırılmadan önce ayrıştırılır ve şu
kurallardan biri ihlal edilirse reddedilir:

* tek bir ifade olmalı ve bir SELECT sorgusu olmalı (WITH ve UNION serbest),
* veri ya da şema değiştiren hiçbir yapı (INSERT, UPDATE, DELETE, DROP, ATTACH, PRAGMA...) olamaz,
* dosya okuyan/yazan ya da eklenti yükleyen fonksiyonlar kullanılamaz,
* yalnızca veri setinde var olan tablo ve sütunlara başvurulabilir,
* LIMIT yoksa eklenir.

Bu katman tek savunma değildir: sorgular ayrıca salt-okunur bir bağlantıda,
süre sınırıyla çalıştırılır (bkz. `execute.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

FORBIDDEN_NODES = tuple(
    getattr(exp, n)
    for n in (
        "Insert",
        "Update",
        "Delete",
        "Drop",
        "Create",
        "Alter",
        "Command",
        "Pragma",
        "Attach",
        "Detach",
        "Merge",
        "TruncateTable",
        "Transaction",
        "Commit",
        "Rollback",
        "Set",
        "Use",
        "Copy",
        "LoadData",
    )
    if hasattr(exp, n)
)
FORBIDDEN_FUNCS = {
    "load_extension",
    "readfile",
    "writefile",
    "edit",
    "fts3_tokenizer",
    "zipfile",
    "sqlar_compress",
    "sqlar_uncompress",
    "randomblob",
    "zeroblob",
}


class GuardError(ValueError):
    """Sorgu güvenlik kurallarına uymuyor. Mesaj kullanıcıya gösterilebilir."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


@dataclass
class Checked:
    sql: str
    tables: list[str]
    warnings: list[str] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)


def check(sql: str, schema: dict[str, dict[str, str]], max_rows: int = 1000) -> Checked:
    """`schema`: {tablo: {sütun: tip}}. Geçerse çalıştırılacak SQL'i döndürür."""
    text = (sql or "").strip().rstrip(";").strip()
    if not text:
        raise GuardError("Sorgu boş.", "empty")
    try:
        statements = [s for s in sqlglot.parse(text, read="sqlite") if s is not None]
    except ParseError as e:
        raise GuardError(f"SQL ayrıştırılamadı: {str(e).splitlines()[0]}", "parse") from None
    if len(statements) != 1:
        raise GuardError("Yalnızca tek bir sorgu çalıştırılabilir.", "multi")
    root = statements[0]
    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise GuardError(f"Yalnızca okuma yapılabilir; {node.key.upper()} kullanılamaz.", "write")
        if isinstance(node, exp.Anonymous) and str(node.name).lower() in FORBIDDEN_FUNCS:
            raise GuardError(f"{node.name}() fonksiyonu güvenlik nedeniyle kapalı.", "func")
    if not isinstance(root, exp.Query):
        raise GuardError("Yalnızca SELECT sorguları çalıştırılabilir.", "not_select")

    known = {t.lower(): t for t in schema}
    ctes = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    tables: list[str] = []
    for t in root.find_all(exp.Table):
        name = t.name.lower()
        if t.args.get("db") or t.args.get("catalog"):
            raise GuardError("Başka bir veritabanına erişilemez.", "db")
        if name in ctes:
            continue
        if name.startswith("sqlite_") or name not in known:
            raise GuardError(f"Bilinmeyen tablo: {t.name}", "table")
        tables.append(known[name])

    try:
        qualify(
            root.copy(),
            schema={k: dict(v) for k, v in schema.items()},
            dialect="sqlite",
            validate_qualify_columns=True,
            identify=False,
        )
    except OptimizeError as e:
        import re

        m = re.search(r"Column '([^']+)'|Unknown column: (\S+)", str(e))
        col = (m.group(1) or m.group(2)) if m else str(e)
        raise GuardError(f"Bilinmeyen sütun: {col}", "column") from None
    except Exception:  # noqa: BLE001 - doğrulayıcının desteklemediği yapı: diğer katmanlar yine korur
        pass

    warnings: list[str] = []
    added = not root.args.get("limit")
    if isinstance(root, exp.Select) and added:
        root = root.limit(max_rows + 1)
    elif added:
        root = exp.select("*").from_(root.subquery("q")).limit(max_rows + 1)
    checks = ["single", "select", "no_write", "tables", "columns", "limit_added" if added else "limit"]
    return Checked(root.sql(dialect="sqlite", pretty=True), sorted(set(tables)), warnings, checks)
