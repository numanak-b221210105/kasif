"""REST API.  Çalıştırma: python -m kasif.serve"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from . import __version__, llm
from .config import settings
from .datasets import DatasetError
from .engine import Engine
from .schema import Answer, AskIn, DatasetOut, HistoryItem, ParseIn, Preview, SqlIn

STATIC = Path(__file__).parent / "static"

app = FastAPI(
    title="Kâşif",
    version=__version__,
    description="Türkçe sorularla veritabanı analizi: kural motoru + isteğe bağlı LLM, salt-okunur ve denetimli SQL.",
)


@lru_cache(maxsize=1)
def engine() -> Engine:
    return Engine(settings())


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/status")
def status() -> dict:
    e = engine()
    ds = e.registry.active
    s = e.s
    return {
        "version": __version__,
        "dataset": ds.public(s.data_dir) | e.info(ds),
        "rules": e.layer(ds) is not None,
        "llm": {
            "host": s.llm_base_url,
            "model": s.llm_model,
            "reachable": e.llm_client is not None or llm.reachable(s),
        },
        "limits": {"max_rows": s.max_rows, "timeout_s": s.timeout_s},
        "history": e.history.stats(),
    }


@app.post("/ask", response_model=Answer)
def ask(body: AskIn) -> Answer:
    return engine().ask(body.question.strip(), body.mode, body.lang)


@app.post("/parse", response_model=Preview)
def preview(body: ParseIn) -> Preview:
    """Yazarken canlı önizleme: soru nasıl anlaşılıyor (SQL çalıştırmaz, geçmişe yazılmaz)."""
    return engine().preview(body.question, body.lang)


@app.post("/sql", response_model=Answer)
def run_sql(body: SqlIn) -> Answer:
    return engine().run_sql(body.sql, body.lang, body.question)


@app.get("/schema")
def schema(lang: str = Query("tr", pattern="^(tr|en)$")) -> dict:
    return engine().schema(lang)


@app.get("/examples")
def examples(lang: str = Query("tr", pattern="^(tr|en)$")) -> list[str]:
    return engine().examples(lang)


@app.get("/datasets", response_model=list[DatasetOut])
def datasets() -> list[DatasetOut]:
    e = engine()
    act = e.registry.active.id
    return [DatasetOut(**d.public(e.s.data_dir), active=d.id == act, **e.info(d)) for d in e.registry.all()]


@app.post("/datasets", response_model=DatasetOut)
async def upload(file: UploadFile = File(...)) -> DatasetOut:
    e = engine()
    try:
        ds = e.registry.add(file.filename or "veri.csv", await file.read())
    except (DatasetError, ValueError) as err:
        raise HTTPException(400, str(err)) from None
    return DatasetOut(**ds.public(e.s.data_dir), active=True, **e.info(ds))


@app.post("/datasets/{ds_id}/activate", response_model=DatasetOut)
def activate(ds_id: str) -> DatasetOut:
    e = engine()
    try:
        ds = e.registry.activate(ds_id)
    except DatasetError as err:
        raise HTTPException(404, str(err)) from None
    return DatasetOut(**ds.public(e.s.data_dir), active=True, **e.info(ds))


@app.delete("/datasets/{ds_id}", status_code=204)
def delete_dataset(ds_id: str) -> None:
    try:
        engine().registry.delete(ds_id)
    except DatasetError as err:
        raise HTTPException(400, str(err)) from None


@app.get("/history", response_model=list[HistoryItem])
def history(limit: int = Query(200, ge=1, le=1000)) -> list[dict]:
    return engine().history.list(limit)


@app.delete("/history", status_code=204)
def clear_history() -> None:
    engine().history.clear()
