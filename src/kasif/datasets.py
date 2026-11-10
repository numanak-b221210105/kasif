"""Veri setleri: demo veritabanı ve kullanıcının yüklediği CSV / SQLite dosyaları.

CSV dosyaları otomatik olarak SQLite tablosuna çevrilir:
* ayraç (`,` `;` sekme) ve kodlama (UTF-8 ya da Excel'in Türkçe Windows-1254'ü) tahmin edilir,
* "1.234,56" gibi Türkçe sayı ve "31.12.2025" gibi tarih biçimleri tanınır,
* sütun adları SQL'e uygun hale getirilir ("Sipariş Tarihi" → siparis_tarihi).
"""

from __future__ import annotations

import csv
import io
import json
import re
import shutil
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from . import demo
from .text import ascii_fold, local_today

MAX_UPLOAD = 50 * 1024 * 1024
DEMO_ID = "demo"


class DatasetError(ValueError):
    pass


@dataclass
class Dataset:
    id: str
    name: str
    file: str
    kind: str  # demo | csv | sqlite
    created: float
    note: str = ""
    labels: dict | None = None  # sütun adı → orijinal başlık ("sube" → "Şube")

    def public(self, data_dir: Path) -> dict:
        d = asdict(self)
        d.pop("file")
        d.pop("labels", None)
        return d


class Registry:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "datasets").mkdir(exist_ok=True)
        self.meta_path = self.dir / "datasets.json"
        self.meta = self._load()
        self.ensure_demo()

    # -------------------------------------------------------------- meta
    def _load(self) -> dict:
        if self.meta_path.exists():
            try:
                return json.loads(self.meta_path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        return {"active": DEMO_ID, "datasets": [], "demo_built": None}

    def _save(self) -> None:
        self.meta_path.write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), "utf-8")

    def ensure_demo(self, today: date | None = None) -> None:
        """Demo veritabanı her gün bugüne kadar yeniden üretilir, böylece "bu ay" gibi sorular anlamlı kalır."""
        today = today or local_today()
        path = self.dir / "datasets" / "demo.sqlite"
        if not path.exists() or self.meta.get("demo_built") != today.isoformat():
            demo.build(path, end=today)
            self.meta["demo_built"] = today.isoformat()
        if not any(d["id"] == DEMO_ID for d in self.meta["datasets"]):
            self.meta["datasets"].insert(
                0,
                asdict(
                    Dataset(
                        DEMO_ID, "Demlik Kahve A.Ş. (demo)", "demo.sqlite", "demo", time.time(), "Sentetik satış verisi"
                    )
                ),
            )
        self._save()

    def all(self) -> list[Dataset]:
        return [Dataset(**d) for d in self.meta["datasets"]]

    def get(self, ds_id: str) -> Dataset:
        for d in self.all():
            if d.id == ds_id:
                return d
        raise DatasetError("Veri seti bulunamadı.")

    @property
    def active(self) -> Dataset:
        try:
            return self.get(self.meta.get("active", DEMO_ID))
        except DatasetError:
            return self.get(DEMO_ID)

    def activate(self, ds_id: str) -> Dataset:
        ds = self.get(ds_id)
        self.meta["active"] = ds.id
        self._save()
        return ds

    def path(self, ds: Dataset) -> Path:
        return self.dir / "datasets" / ds.file

    def delete(self, ds_id: str) -> None:
        if ds_id == DEMO_ID:
            raise DatasetError("Demo veri seti silinemez.")
        ds = self.get(ds_id)
        self.path(ds).unlink(missing_ok=True)
        self.meta["datasets"] = [d for d in self.meta["datasets"] if d["id"] != ds_id]
        if self.meta.get("active") == ds_id:
            self.meta["active"] = DEMO_ID
        self._save()

    # -------------------------------------------------------------- upload
    def add(self, filename: str, data: bytes) -> Dataset:
        if len(data) > MAX_UPLOAD:
            raise DatasetError("Dosya 50 MB sınırını aşıyor.")
        stem = Path(filename).stem or "veri"
        ext = Path(filename).suffix.lower()
        ds_id = uuid.uuid4().hex[:8]
        target = self.dir / "datasets" / f"{ds_id}.sqlite"
        if data[:16] == b"SQLite format 3\x00" or ext in (".sqlite", ".sqlite3", ".db"):
            if data[:16] != b"SQLite format 3\x00":
                raise DatasetError("Geçerli bir SQLite dosyası değil.")
            target.write_bytes(data)
            try:
                con = sqlite3.connect(target)
                n = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','view')").fetchone()[0]
                con.close()
            except sqlite3.DatabaseError as e:
                target.unlink(missing_ok=True)
                raise DatasetError(f"SQLite dosyası açılamadı: {e}") from None
            if not n:
                target.unlink(missing_ok=True)
                raise DatasetError("SQLite dosyasında tablo yok.")
            kind, note, labels = "sqlite", f"{n} tablo", None
        elif ext in (".csv", ".tsv", ".txt"):
            rows, labels = csv_to_sqlite(data, target, ascii_fold(stem))
            kind, note = "csv", f"{rows:,} satır".replace(",", ".")
        else:
            raise DatasetError("Desteklenen biçimler: CSV, SQLite (.sqlite, .db).")
        ds = Dataset(ds_id, stem, target.name, kind, time.time(), note, labels)
        self.meta["datasets"].append(asdict(ds))
        self.meta["active"] = ds_id
        self._save()
        return ds

    def copy_to(self, ds: Dataset, dest: Path) -> None:
        shutil.copy(self.path(ds), dest)


# ------------------------------------------------------------------ CSV dönüştürme

TR_NUM = re.compile(r"^-?\d{1,3}(\.\d{3})+(,\d+)?$|^-?\d+,\d+$")
EN_NUM = re.compile(r"^-?\d+(\.\d+)?$|^-?\d{1,3}(,\d{3})+(\.\d+)?$")
TR_DATE = re.compile(r"^(\d{1,2})[./](\d{1,2})[./](\d{4})$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1254", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _to_number(v: str, style: str) -> float | int | None:
    v = v.strip().replace(" ", "").replace("₺", "").replace("TL", "")
    if not v:
        return None
    if style == "tr":
        v = v.replace(".", "").replace(",", ".")
    else:
        v = v.replace(",", "")
    f = float(v)
    return int(f) if f.is_integer() and "." not in v else f


def _col_type(values: list[str]) -> str:
    vals = [v.strip() for v in values if v and v.strip()]
    if not vals:
        return "text"
    if all(re.fullmatch(r"-?\d+", v) for v in vals) and not all(len(v) > 1 and v.startswith("0") for v in vals):
        return "int"
    if all(TR_NUM.match(v.replace("₺", "").replace("TL", "").strip()) for v in vals):
        return "num_tr"
    if all(EN_NUM.match(v) for v in vals):
        return "num_en"
    if all(ISO_DATE.match(v) for v in vals):
        return "date_iso"
    if all(TR_DATE.match(v) for v in vals):
        return "date_tr"
    return "text"


def csv_to_sqlite(data: bytes, target: Path, table: str) -> tuple[int, dict[str, str]]:
    text = _decode(data)
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except csv.Error:
        delim = ";" if sample.count(";") > sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise DatasetError("CSV dosyasında başlık satırı ve en az bir veri satırı olmalı.")
    header = rows[0]
    width = len(header)
    body = [(r + [""] * width)[:width] for r in rows[1:]]
    names: list[str] = []
    for i, h in enumerate(header):
        n = ascii_fold(h) if h.strip() else f"kolon_{i + 1}"
        if n[0].isdigit():
            n = f"k_{n}"
        base, k = n, 2
        while n in names:
            n, k = f"{base}_{k}", k + 1
        names.append(n)
    types = [_col_type([r[i] for r in body[:5000]]) for i in range(width)]
    sql_types = {
        "int": "INTEGER",
        "num_tr": "REAL",
        "num_en": "REAL",
        "date_iso": "TEXT",
        "date_tr": "TEXT",
        "text": "TEXT",
    }

    def conv(v: str, t: str):
        v = v.strip()
        if not v:
            return None
        if t == "int":
            return int(v)
        if t in ("num_tr", "num_en"):
            return _to_number(v, "tr" if t == "num_tr" else "en")
        if t == "date_tr":
            d, m, y = TR_DATE.match(v).groups()
            return f"{y}-{int(m):02d}-{int(d):02d}"
        if t == "date_iso":
            return v[:10] if len(v) >= 10 else v
        return v

    target.unlink(missing_ok=True)
    con = sqlite3.connect(target)
    cols = ", ".join(f'"{n}" {sql_types[t]}' for n, t in zip(names, types, strict=True))
    con.execute(f'CREATE TABLE "{table}" ({cols})')
    con.executemany(
        f'INSERT INTO "{table}" VALUES ({",".join("?" * width)})',
        ([conv(v, t) for v, t in zip(r, types, strict=True)] for r in body),
    )
    con.commit()
    con.close()
    return len(body), {n: h.strip() for n, h in zip(names, header, strict=True) if h.strip()}
