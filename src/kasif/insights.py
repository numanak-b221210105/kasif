"""Sonuç tablosundan grafik önerisi ve kısa yazılı özet üretir.

Özetteki her sayı doğrudan sorgu sonucundan hesaplanır; bir dil modeli
tarafından yazılmaz. Böylece "cevap" ile "veri" arasında fark oluşamaz.
"""

from __future__ import annotations

import re
from datetime import date

from .text import local_today

PERIOD = re.compile(r"^\d{4}(-(\d{2}|Q[1-4]|W\d{2})(-\d{2})?)?$")
MONEY_HINT = (
    "ciro",
    "tutar",
    "gelir",
    "kâr",
    "kar",
    "fiyat",
    "maliyet",
    "indirim",
    "sepet",
    "revenue",
    "amount",
    "price",
    "cost",
    "profit",
    "sales",
    "discount",
    "tl",
)
PCT_HINT = ("%", "oran", "marj", "pay", "yüzde", "percent", "share", "margin", "rate")


def infer_columns(names: list[str], rows: list[list]) -> list[dict]:
    """Sütun rollerini ve biçimlerini değerlere bakarak tahmin eder (LLM ve elle yazılan SQL için)."""
    out = []
    for i, name in enumerate(names):
        vals = [r[i] for r in rows if r[i] is not None]
        low = name.lower()
        if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            if (
                low in ("id",)
                or low.endswith("_id")
                or (low.startswith(("yil", "year")) and all(isinstance(v, int) and 1900 < v < 2200 for v in vals))
            ):
                out.append(
                    {"name": name, "role": "dim" if not low.startswith(("yil", "year")) else "time", "fmt": "text"}
                )
                continue
            fmt = (
                "pct"
                if any(h in low for h in PCT_HINT)
                else "money"
                if any(h in low for h in MONEY_HINT)
                else ("int" if all(isinstance(v, int) for v in vals) else "num")
            )
            out.append({"name": name, "role": "metric", "fmt": fmt})
        elif vals and all(isinstance(v, str) and PERIOD.match(v) for v in vals):
            out.append({"name": name, "role": "time", "fmt": "period"})
        else:
            out.append({"name": name, "role": "dim", "fmt": "text"})
    return out


def choose_chart(cols: list[dict], rows: list[list]) -> dict:
    metrics = [i for i, c in enumerate(cols) if c["role"] == "metric"]
    times = [i for i, c in enumerate(cols) if c["role"] == "time"]
    dims = [i for i, c in enumerate(cols) if c["role"] == "dim"]
    share = next((i for i, c in enumerate(cols) if c["role"] == "share"), None)
    n = len(rows)
    if not rows or not metrics:
        return {"type": "table"}
    if n == 1 and not times and not dims:
        return {"type": "kpi", "y": metrics}
    if times and len(dims) == 1 and len(metrics) >= 1:
        series = {r[dims[0]] for r in rows}
        if len(series) <= 8:
            return {"type": "multiline", "x": times[0], "series": dims[0], "y": [metrics[0]]}
        return {"type": "table"}
    if times and not dims:
        n_periods = len({r[times[0]] for r in rows})
        return {"type": "line" if n_periods >= 3 else "bar", "x": times[0], "y": metrics[:2]}
    if dims and not times and len(dims) == 1:
        if share is not None and 2 <= n <= 8:
            return {"type": "donut", "x": dims[0], "y": [metrics[0]], "share": share}
        if n <= 30:
            return {"type": "bar", "x": dims[0], "y": metrics[:2]}
    return {"type": "table"}


# ---------------------------------------------------------------- biçimlendirme


def fmt_num(v: float, lang: str, decimals: int = 0) -> str:
    s = f"{v:,.{decimals}f}"
    if lang == "tr":
        s = s.replace(",", "§").replace(".", ",").replace("§", ".")
    return s


def fmt_value(v, fmt: str, lang: str = "tr", compact: bool = True) -> str:
    if v is None:
        return "—"
    if not isinstance(v, (int, float)):
        return str(v)
    if fmt == "pct":
        s = fmt_num(v, lang, 1)
        return f"%{s}" if lang == "tr" else f"{s}%"
    if fmt == "money":
        a = abs(v)
        if compact and a >= 1e6:
            return f"{fmt_num(v / 1e6, lang, 2)} {'milyon TL' if lang == 'tr' else 'M TL'}"
        return f"{fmt_num(v, lang, 0 if a >= 1000 else 2)} TL"
    if isinstance(v, int) or fmt == "int" or float(v).is_integer():
        return fmt_num(v, lang, 0)
    return fmt_num(v, lang, 2)


def _b(s: str) -> str:
    return f"**{s}**"


def _pct(v: float, lang: str) -> str:
    return fmt_value(v, "pct", lang)


def _current_period(p: str, today: date) -> bool:
    return (
        p == str(today.year)
        or p == today.strftime("%Y-%m")
        or p == today.isoformat()
        or p == f"{today.year}-Q{(today.month - 1) // 3 + 1}"
        or p == today.strftime("%Y-W%W")
    )


def summarize(
    cols: list[dict],
    rows: list[list],
    lang: str = "tr",
    chart: dict | None = None,
    highlight: list[str] | None = None,
    order: str | None = None,
    today: date | None = None,
    same_period: bool = False,
    truncated: bool = False,
) -> list[str]:
    tr = lang == "tr"
    today = today or local_today()
    out: list[str] = []
    if not rows:
        return ["Sorgu çalıştı ama hiç satır döndürmedi." if tr else "The query ran but returned no rows."]
    chart = chart or choose_chart(cols, rows)
    metrics = [i for i, c in enumerate(cols) if c["role"] == "metric"]
    share = next((i for i, c in enumerate(cols) if c["role"] == "share"), None)
    t = chart.get("type")

    if t == "kpi":
        for i in metrics:
            out.append(f"{cols[i]['name']}: {_b(fmt_value(rows[0][i], cols[i]['fmt'], lang))}")
        return out

    if t in ("line", "bar") and cols[chart["x"]]["role"] == "time":
        x, y = chart["x"], chart["y"][0]
        pts = [(r[x], r[y]) for r in rows if r[y] is not None]
        partial = len(pts) >= 3 and _current_period(str(pts[-1][0]), today) and not same_period
        full = pts[:-1] if partial else pts
        if len(pts) >= 2:
            (p0, v0), (p1, v1) = full[0], full[-1]
            name, fmt = cols[y]["name"], cols[y]["fmt"]
            if v0 and v1 == v0:
                out.append(
                    f"{name}, {_b(str(p0))} ile {_b(str(p1))} arasında değişmedi ({fmt_value(v0, fmt, lang)})."
                    if tr
                    else f"{name} is unchanged between {_b(str(p0))} and {_b(str(p1))}."
                )
            elif v0:
                ch = (v1 - v0) / abs(v0) * 100
                word = ("arttı" if ch >= 0 else "azaldı") if tr else ("up" if ch >= 0 else "down")
                if tr:
                    out.append(
                        f"{name}, {_b(str(p0))} döneminden {_b(str(p1))} dönemine {_b(_pct(abs(ch), lang))} {word} "
                        f"({fmt_value(v0, fmt, lang)} → {fmt_value(v1, fmt, lang)})."
                    )
                else:
                    out.append(
                        f"{name} is {word} {_b(_pct(abs(ch), lang))} from {_b(str(p0))} to {_b(str(p1))} "
                        f"({fmt_value(v0, fmt, lang)} → {fmt_value(v1, fmt, lang)})."
                    )
            peak = max(full, key=lambda p: p[1])
            low = min(full, key=lambda p: p[1])
            out.append(
                (
                    f"En yüksek dönem {_b(str(peak[0]))} ({fmt_value(peak[1], fmt, lang)}), en düşük "
                    f"{_b(str(low[0]))} ({fmt_value(low[1], fmt, lang)})."
                )
                if tr
                else (
                    f"Peak: {_b(str(peak[0]))} ({fmt_value(peak[1], fmt, lang)}); low: {_b(str(low[0]))} "
                    f"({fmt_value(low[1], fmt, lang)})."
                )
            )
            if same_period:
                out.append(
                    "Yıllar, her yılın aynı tarih aralığıyla karşılaştırıldı."
                    if tr
                    else "Years are compared over the same date range."
                )
            elif partial:
                last = pts[-1][0]
                out.append(
                    f"{last} henüz tamamlanmadığı için karşılaştırmaya katılmadı."
                    if tr
                    else f"{last} is not over yet, so it is left out of the comparison."
                )
            elif _current_period(str(p1), today):
                out.append(
                    f"Son dönem ({p1}) henüz tamamlanmadı; karşılaştırırken bunu dikkate alın."
                    if tr
                    else f"The last period ({p1}) is not over yet; keep that in mind when comparing."
                )
        return out

    if t == "multiline":
        x, s, y = chart["x"], chart["series"], chart["y"][0]
        last = max(r[x] for r in rows)
        at_last = sorted((r for r in rows if r[x] == last), key=lambda r: -(r[y] or 0))
        if at_last:
            out.append(
                (
                    f"{_b(str(last))} döneminde {cols[y]['name'].lower()} bakımından önde: {_b(str(at_last[0][s]))} "
                    f"({fmt_value(at_last[0][y], cols[y]['fmt'], lang)})."
                )
                if tr
                else (
                    f"In {_b(str(last))} the leader is {_b(str(at_last[0][s]))} "
                    f"({fmt_value(at_last[0][y], cols[y]['fmt'], lang)})."
                )
            )
        totals: dict = {}
        for r in rows:
            totals[r[s]] = totals.get(r[s], 0) + (r[y] or 0)
        best = max(totals, key=totals.get)
        out.append(
            f"Tüm dönem toplamında ilk sırada {_b(str(best))}."
            if tr
            else f"Over the whole range: {_b(str(best))} leads."
        )
        return out

    if t in ("bar", "donut") or (t == "table" and metrics):
        x = chart.get("x", next((i for i, c in enumerate(cols) if c["role"] == "dim"), 0))
        y = (chart.get("y") or metrics)[0]
        name, fmt = cols[y]["name"], cols[y]["fmt"]
        if highlight and share is not None:
            for r in rows:
                if str(r[x]) in highlight:
                    out.append(
                        (
                            f"{_b(str(r[x]))}, toplam {name.lower()} içinde {_b(_pct(r[share], lang))} paya sahip "
                            f"({fmt_value(r[y], fmt, lang)})."
                        )
                        if tr
                        else (
                            f"{_b(str(r[x]))} accounts for {_b(_pct(r[share], lang))} of total {name.lower()} "
                            f"({fmt_value(r[y], fmt, lang)})."
                        )
                    )
        top = rows[0]
        lead = "en düşük" if order == "asc" else "ilk sırada"
        if len(rows) == 1:
            out.append(
                f"{_b(str(top[x]))}: {_b(fmt_value(top[y], fmt, lang))} ({name.lower()})."
                if tr
                else f"{_b(str(top[x]))}: {_b(fmt_value(top[y], fmt, lang))} ({name.lower()})."
            )
            return out
        if tr:
            out.append(f"{name} bakımından {lead}: {_b(str(top[x]))} ({fmt_value(top[y], fmt, lang)}).")
        else:
            out.append(
                f"{'Lowest' if order == 'asc' else 'Top'} by {name.lower()}: {_b(str(top[x]))} "
                f"({fmt_value(top[y], fmt, lang)})."
            )
        sec = rows[1]
        if (
            not highlight
            and isinstance(top[y], (int, float))
            and isinstance(sec[y], (int, float))
            and sec[y]
            and order != "asc"
        ):
            ratio = top[y] / sec[y]
            if ratio >= 1.05:
                out.append(
                    (
                        f"İkinci sıradaki {_b(str(sec[x]))} ile arasında {_b(fmt_num(ratio, lang, 1) + ' kat')} fark var."
                        if ratio >= 1.5
                        else f"İkinci sıradaki {_b(str(sec[x]))} değerinin {_b(_pct((ratio - 1) * 100, lang))} üzerinde."
                    )
                    if tr
                    else (
                        f"That is {_b(fmt_num(ratio, lang, 1) + '×')} the runner-up, {_b(str(sec[x]))}."
                        if ratio >= 1.5
                        else f"{_b(_pct((ratio - 1) * 100, lang))} ahead of {_b(str(sec[x]))}."
                    )
                )
        if share is not None and not highlight and len(rows) > 3:
            k = 3
            s = sum((r[share] or 0) for r in rows[:k])
            out.append(
                f"İlk {k} kalemin toplamdaki payı {_b(_pct(s, lang))}."
                if tr
                else f"The top {k} make up {_b(_pct(s, lang))} of the total."
            )
        elif share is None and fmt in ("money", "int", "num") and not truncated:
            total = sum(r[y] for r in rows if isinstance(r[y], (int, float)))
            if total and len(rows) >= 3:
                out.append(
                    (
                        f"Listelenen {len(rows)} kalemin toplamı {fmt_value(total, fmt, lang)}; ilk sıradakinin "
                        f"payı {_b(_pct(top[y] / total * 100, lang))}."
                    )
                    if tr
                    else (
                        f"The {len(rows)} rows shown total {fmt_value(total, fmt, lang)}; the first is "
                        f"{_b(_pct(top[y] / total * 100, lang))} of that."
                    )
                )
        return out

    out.append(f"{len(rows)} satır döndü." if tr else f"{len(rows)} rows returned.")
    return out
