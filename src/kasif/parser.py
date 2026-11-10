"""Kural motoru: Türkçe (ve basit İngilizce) bir soruyu anlam katmanı üzerinden SQL'e çevirir.

Akış:
    1. Sözcüklere ayır.
    2. Zaman ifadelerini bul ("geçen çeyrek", "son 3 ay", "Ekim 2025", "2024 ve 2025").
    3. Ölçü, boyut ve değer eşanlamlılarını en uzun eşleşme önce olacak şekilde yerleştir.
    4. Zaman kırılımını ("aylık"), sıralamayı ("en çok 5") ve olumsuzlamayı ("hariç") çöz.
    5. Kalan, tanınmayan sözcükleri raporla: bunlar varsa kural motoru emin değildir.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta

from .semantic import Dimension, Layer, Metric
from .text import _WORD as _WORD_RE
from .text import STOPWORDS, local_today, tok_match, tokens, tr_lower

NUM_WORDS = {
    "bir": 1,
    "iki": 2,
    "üç": 3,
    "dört": 4,
    "beş": 5,
    "altı": 6,
    "yedi": 7,
    "sekiz": 8,
    "dokuz": 9,
    "on": 10,
    "onbeş": 15,
    "yirmi": 20,
    "otuz": 30,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "twelve": 12,
    "twenty": 20,
}
MONTHS = ["ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz", "ağustos", "eylül", "ekim", "kasım", "aralık"]
MONTHS_EN = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
MONTH_LABEL = {"tr": [m.capitalize() for m in MONTHS], "en": [m.capitalize() for m in MONTHS_EN]}
ORDINALS = {
    "ilk": 1,
    "birinci": 1,
    "ikinci": 2,
    "üçüncü": 3,
    "dördüncü": 4,
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
}
UNITS = {
    "gün": "day",
    "günde": "day",
    "günlük": "day",
    "day": "day",
    "days": "day",
    "hafta": "week",
    "haftada": "week",
    "haftalık": "week",
    "week": "week",
    "weeks": "week",
    "ay": "month",
    "ayda": "month",
    "aylık": "month",
    "month": "month",
    "months": "month",
    "yıl": "year",
    "yılda": "year",
    "yıllık": "year",
    "sene": "year",
    "senede": "year",
    "year": "year",
    "years": "year",
}
GRAINS = {
    "yil": {"yıllık", "yıllar", "yıllara", "yılların", "yıllarda", "yearly", "annual", "annually", "years"},
    "ceyrek": {"çeyreklik", "çeyrekler", "çeyreklere", "çeyreklerin", "quarterly", "quarters"},
    "ay": {"aylık", "aylar", "aylara", "ayların", "aylarda", "monthly", "months"},
    "hafta": {"haftalık", "haftalar", "haftalara", "weekly", "weeks"},
    "gun": {"günlük", "günler", "günlere", "daily", "days"},
}
GRAIN_LABEL = {
    "yil": ("Yıl", "Year"),
    "ceyrek": ("Çeyrek", "Quarter"),
    "ay": ("Ay", "Month"),
    "hafta": ("Hafta", "Week"),
    "gun": ("Gün", "Day"),
}
TREND = {
    "trend",
    "trendi",
    "seyri",
    "seyir",
    "gidişat",
    "gidişatı",
    "değişim",
    "değişimi",
    "büyüme",
    "büyümesi",
    "artış",
    "artışı",
    "zamanla",
    "over",
    "growth",
    "evolution",
}
DESC = {"çok", "fazla", "fazlası", "yüksek", "iyi", "büyük", "top", "most", "highest", "best", "largest", "biggest"}
ASC = {"az", "düşük", "kötü", "küçük", "least", "lowest", "worst", "bottom", "smallest", "fewest"}
NEGATE = {"hariç", "dışında", "except", "excluding", "olmayan"}
SHARE = {"pay", "payı", "payları", "yüzde", "yüzdesi", "oran", "oranı", "dağılım", "dağılımı", "share", "percentage"}
WEAK_METRIC_SYNONYMS = {"satış", "satışlar", "alışveriş", "sales"}
GENERIC_VALUE_TOKENS = {"kg", "g", "l", "lu", "li", "lı", "lü", "mm", "pro", "set", "seti", "paketi", "the"}


@dataclass
class Filter:
    dim: Dimension
    values: list[str]
    negate: bool = False


@dataclass
class TimeRange:
    start: date
    end: date
    label: str
    same_period: bool = False  # yıllar arası "aynı dönem" karşılaştırması


@dataclass
class Plan:
    metrics: list[Metric] = field(default_factory=list)
    explicit_metric: bool = False
    groups: list[Dimension] = field(default_factory=list)
    grain: str | None = None
    filters: list[Filter] = field(default_factory=list)
    time: TimeRange | None = None
    order: str | None = None  # desc | asc
    limit: int | None = None
    share: bool = False
    highlight: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    kinds: dict[int, str] = field(default_factory=dict)  # sözcük sırası → metric | dim | filter | time | ...
    plural: bool = False

    @property
    def understood(self) -> bool:
        return bool(self.explicit_metric or self.groups or self.filters or self.grain or self.time)


# ---------------------------------------------------------------- zaman


def _qstart(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _month_range(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def _fmt(d: date, lang: str) -> str:
    return f"{d.day} {MONTH_LABEL[lang][d.month - 1]} {d.year}"


def _num(tok: str) -> int | None:
    if tok.isdigit():
        return int(tok)
    return NUM_WORDS.get(tok)


def _is(tok: str, *words: str) -> bool:
    return any(tok_match(tok, w) for w in words)


def parse_time(
    toks: list[str], used: set[int], today: date, lang: str = "tr", reserved: frozenset[str] = frozenset()
) -> TimeRange | None:
    """Zaman ifadelerini bulur, kullanılan sözcükleri `used` kümesine ekler."""
    ranges: list[tuple[date, date, str]] = []
    n = len(toks)

    def take(*idx: int) -> None:
        used.update(i for i in idx if 0 <= i < n)

    def year_near(i: int, span: int = 3) -> int | None:
        for j in list(range(i - span, i)) + list(range(i + 1, i + 1 + span)):
            if 0 <= j < n and j not in used and toks[j].isdigit() and len(toks[j]) == 4 and toks[j].startswith("20"):
                take(j)
                if j + 1 < n and _is(toks[j + 1], "yıl", "yılı", "yılın", "yılının", "sene"):
                    take(j + 1)
                return int(toks[j])
        return None

    i = 0
    while i < n:
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < n else ""
        nxt2 = toks[i + 2] if i + 2 < n else ""
        # son N gün/hafta/ay/yıl — last N days
        if t in ("son", "last", "past") and i + 1 < n:
            k, j = _num(nxt), i + 2
            if k is None:
                k, j = 1, i + 1
            unit = UNITS.get(toks[j]) if j < n else None
            if unit is None and j < n:
                unit = next((u for w, u in UNITS.items() if tok_match(toks[j], w)), None)
            if unit and k:
                start = {
                    "day": today - timedelta(days=k - 1),
                    "week": today - timedelta(weeks=k) + timedelta(days=1),
                    "month": _add_months(today, -k) + timedelta(days=1),
                    "year": _add_months(today, -12 * k) + timedelta(days=1),
                }[unit]
                if t == "last" and k == 1 and j == i + 1:  # "last month" = önceki takvim ayı
                    break_out = _relative("prev", unit, today)
                    if break_out:
                        ranges.append(break_out)
                        take(i, j)
                        i = j + 1
                        continue
                label = {
                    "tr": f"Son {k} " + {"day": "gün", "week": "hafta", "month": "ay", "year": "yıl"}[unit],
                    "en": f"Last {k} {unit}s",
                }[lang]
                ranges.append((start, today, label))
                take(i, i + 1, j)
                i = j + 1
                continue
        # bu / geçen / önceki + yıl/ay/çeyrek/hafta
        rel = {"bu": "this", "this": "this", "geçen": "prev", "önceki": "prev", "previous": "prev"}.get(t)
        if rel and nxt:
            unit = next(
                (
                    u
                    for w, u in {
                        "yıl": "year",
                        "sene": "year",
                        "year": "year",
                        "ay": "month",
                        "month": "month",
                        "çeyrek": "quarter",
                        "quarter": "quarter",
                        "hafta": "week",
                        "week": "week",
                    }.items()
                    if tok_match(nxt, w)
                ),
                None,
            )
            if unit:
                r = _relative(rel, unit, today, lang)
                if r:
                    ranges.append(r)
                    take(i, i + 1)
                    i += 2
                    continue
        # yılbaşından beri / year to date
        if t in ("yılbaşından", "ytd") or (t == "yıl" and _is(nxt, "başından")) or (t == "year" and nxt == "to"):
            ranges.append((date(today.year, 1, 1), today, {"tr": "Yılbaşından bugüne", "en": "Year to date"}[lang]))
            take(i, i + 1, i + 2 if nxt2 in ("beri", "date", "bugüne") else -1)
            i += 2
            continue
        if t in ("bugün", "today"):
            ranges.append((today, today, {"tr": "Bugün", "en": "Today"}[lang]))
            take(i)
        elif t in ("dün", "yesterday"):
            y = today - timedelta(days=1)
            ranges.append((y, y, {"tr": "Dün", "en": "Yesterday"}[lang]))
            take(i)
        # çeyrek: "3. çeyrek", "üçüncü çeyrek", "ilk çeyrek", "q3"
        elif (_is(nxt, "çeyrek", "çeyreği", "çeyreğinde", "quarter") and (_num(t) or t in ORDINALS)) or (
            t == "q" and nxt.isdigit()
        ):
            q = int(nxt) if t == "q" else (_num(t) if _num(t) else ORDINALS[t])
            if 1 <= q <= 4:
                take(i, i + 1)
                y = year_near(i) or (today.year if date(today.year, 3 * q - 2, 1) <= today else today.year - 1)
                s = date(y, 3 * q - 2, 1)
                e = _add_months(s, 3) - timedelta(days=1)
                ranges.append((s, min(e, today), {"tr": f"{y} {q}. çeyrek", "en": f"Q{q} {y}"}[lang]))
                i += 2
                continue
        # ay adı (+ isteğe bağlı yıl)
        else:
            mi = next((k for k, m in enumerate(MONTHS) if tok_match(t, m)), None)
            if mi is None and t in MONTHS_EN:
                mi = MONTHS_EN.index(t)
            if mi is not None and t not in reserved:
                take(i)
                if nxt in ("ayı", "ayında", "ayının", "month"):
                    take(i + 1)
                y = year_near(i) or (today.year if mi + 1 <= today.month else today.year - 1)
                s, e = _month_range(y, mi + 1)
                ranges.append((s, min(e, today), f"{MONTH_LABEL[lang][mi]} {y}"))
        i += 1

    # açık yıllar: "2025", "2024 ve 2025"
    years = sorted(
        {
            int(toks[i])
            for i in range(n)
            if i not in used
            and toks[i].isdigit()
            and len(toks[i]) == 4
            and toks[i].startswith("20")
            and 2000 <= int(toks[i]) <= today.year
        }
    )
    for i in range(n):
        if i not in used and toks[i].isdigit() and len(toks[i]) == 4 and toks[i].startswith("20"):
            take(i)
            if i + 1 < n and _is(toks[i + 1], "yıl", "yılı", "yılında", "yılının", "sene", "senesi", "senesinde"):
                take(i + 1)
    for y in years:
        ranges.append((date(y, 1, 1), min(date(y, 12, 31), today), str(y)))

    if not ranges:
        return None
    start = min(r[0] for r in ranges)
    end = max(r[1] for r in ranges)
    label = " + ".join(r[2] for r in ranges) if len(ranges) > 1 else ranges[0][2]
    if len(ranges) > 1:
        label = {
            "tr": f"{label} ({_fmt(start, lang)} – {_fmt(end, lang)})",
            "en": f"{label} ({_fmt(start, lang)} – {_fmt(end, lang)})",
        }[lang]
    tr = TimeRange(start, end, label)
    # birden çok yıl ve biri yarım kalmışsa: aynı dönemle karşılaştır
    if (
        len({r[0].year for r in ranges}) > 1
        and any(r[1] == today for r in ranges)
        and all(r[0].month == 1 and r[0].day == 1 for r in ranges)
    ):
        tr.same_period = True
    return tr


def _relative(rel: str, unit: str, today: date, lang: str = "tr") -> tuple[date, date, str] | None:
    L = {
        "tr": {
            ("this", "year"): "Bu yıl",
            ("prev", "year"): "Geçen yıl",
            ("this", "month"): "Bu ay",
            ("prev", "month"): "Geçen ay",
            ("this", "quarter"): "Bu çeyrek",
            ("prev", "quarter"): "Geçen çeyrek",
            ("this", "week"): "Bu hafta",
            ("prev", "week"): "Geçen hafta",
        },
        "en": {
            ("this", "year"): "This year",
            ("prev", "year"): "Last year",
            ("this", "month"): "This month",
            ("prev", "month"): "Last month",
            ("this", "quarter"): "This quarter",
            ("prev", "quarter"): "Last quarter",
            ("this", "week"): "This week",
            ("prev", "week"): "Last week",
        },
    }
    label = L[lang][(rel, unit)]
    if unit == "year":
        y = today.year if rel == "this" else today.year - 1
        return date(y, 1, 1), (today if rel == "this" else date(y, 12, 31)), f"{label} ({y})"
    if unit == "month":
        s = date(today.year, today.month, 1) if rel == "this" else _add_months(date(today.year, today.month, 1), -1)
        e = today if rel == "this" else _month_range(s.year, s.month)[1]
        return s, e, f"{label} ({MONTH_LABEL[lang][s.month - 1]} {s.year})"
    if unit == "quarter":
        s = _qstart(today) if rel == "this" else _add_months(_qstart(today), -3)
        e = today if rel == "this" else _add_months(s, 3) - timedelta(days=1)
        q = (s.month - 1) // 3 + 1
        return s, e, f"{label} ({s.year} Q{q})"
    if unit == "week":
        s = today - timedelta(days=today.weekday())
        if rel == "prev":
            s -= timedelta(days=7)
        e = today if rel == "this" else s + timedelta(days=6)
        return s, e, label
    return None


# ---------------------------------------------------------------- ana ayrıştırıcı


@dataclass
class _Cand:
    start: int
    length: int
    kind: str  # metric | dim | value
    obj: object
    chars: int


def _span_match(toks: list[str], i: int, syn: list[str]) -> bool:
    if i + len(syn) > len(toks):
        return False
    return all(tok_match(toks[i + j], s) for j, s in enumerate(syn))


def _value_tokens(v: str) -> list[str]:
    return [t for t in tokens(v) if not t.isdigit() and t not in GENERIC_VALUE_TOKENS]


def parse(question: str, layer: Layer, today: date | None = None, lang: str = "tr") -> Plan:
    today = today or local_today()
    toks = tokens(question)
    used: set[int] = set()
    plan = Plan()

    reserved = frozenset(t for d in layer.dimensions if len(d.values) <= 400 for v in d.values for t in tokens(v))
    time = parse_time(toks, used, today, lang, reserved) if layer.date_expr else None
    plan.time = time
    plan.kinds.update(dict.fromkeys(used, "time"))

    # aday eşleşmeler: ölçüler, boyutlar ve boyut değerleri
    cands: list[_Cand] = []
    for m in layer.metrics:
        for syn in m.synonyms:
            st = tokens(syn)
            for i in range(len(toks)):
                if st and _span_match(toks, i, st):
                    cands.append(_Cand(i, len(st), "metric", m, len(syn)))
    for d in layer.dimensions:
        for syn in d.synonyms + d.plural:
            st = tokens(syn)
            for i in range(len(toks)):
                if st and _span_match(toks, i, st):
                    cands.append(_Cand(i, len(st), "dim", d, len(syn)))
    # değerler: tüm sözcükleriyle ya da ayırt edici tek sözcükle
    tok_owner: dict[str, set[tuple[str, str]]] = {}
    for d in layer.dimensions:
        if len(d.values) > 400:
            continue
        for v in d.values:
            for t in _value_tokens(v):
                tok_owner.setdefault(t, set()).add((d.key, v))
    syn_tokens = {t for m in layer.metrics for s in m.synonyms for t in tokens(s)} | {
        t for d in layer.dimensions for s in d.synonyms + d.plural for t in tokens(s)
    }
    for d in layer.dimensions:
        if len(d.values) > 400:
            continue
        for v in d.values:
            vt = _value_tokens(v)
            if not vt:
                continue
            for i in range(len(toks)):
                if _span_match(toks, i, vt):
                    cands.append(_Cand(i, len(vt), "value", (d, v), len(v) + 1))
                elif len(vt) > 1:
                    for t in vt:
                        if (
                            len(t) >= 5
                            and len(tok_owner.get(t, ())) == 1
                            and t not in syn_tokens
                            and t not in STOPWORDS
                            and tok_match(toks[i], t)
                        ):
                            cands.append(_Cand(i, 1, "value", (d, v), len(t)))
    # en uzun eşleşme önce; aynı uzunlukta değer > ölçü > boyut
    rank = {"value": 2, "metric": 1, "dim": 0}
    cands.sort(key=lambda c: (-c.length, -rank[c.kind], -c.chars, c.start))
    chosen: list[_Cand] = []
    taken: set[int] = set(used)
    value_spans: dict[tuple[str, str], int] = {}
    for c in cands:
        span = set(range(c.start, c.start + c.length))
        if c.kind == "value" and (c.obj[0].key, c.obj[1]) in value_spans:
            continue
        if span & taken:
            # aynı sözcük birden çok değere ait olabilir: "Türk kahvesi" → 250 g ve 1 kg
            if c.kind == "value" and any(
                x.kind == "value" and x.start == c.start and x.length == c.length and x.obj[0] is c.obj[0]
                for x in chosen
            ):
                chosen.append(c)
                value_spans[(c.obj[0].key, c.obj[1])] = c.start
            continue
        chosen.append(c)
        taken |= span
        if c.kind == "value":
            value_spans[(c.obj[0].key, c.obj[1])] = c.start
    chosen.sort(key=lambda c: c.start)

    weak: list[Metric] = []
    for c in chosen:
        kind = {"metric": "metric", "dim": "dim", "value": "filter"}[c.kind]
        plan.kinds.update(dict.fromkeys(range(c.start, c.start + c.length), kind))
        if c.kind == "metric" and c.obj not in plan.metrics:
            plan.metrics.append(c.obj)
            plan.explicit_metric = True
            if c.length == 1 and any(tok_match(toks[c.start], w) for w in WEAK_METRIC_SYNONYMS):
                weak.append(c.obj)
        elif c.kind == "dim" and c.obj not in plan.groups:
            plan.groups.append(c.obj)
            q = toks[c.start + c.length - 1]
            syn_last = next(
                (tokens(s)[-1] for s in c.obj.synonyms + c.obj.plural if _span_match(toks, c.start, tokens(s))), q
            )
            rest = q[len(syn_last) :] if q.startswith(syn_last) else ""
            if (
                rest.startswith(("ler", "lar"))
                or (q.endswith("s") and q != syn_last)
                or any(tok_match(q, p) for p in c.obj.plural if tokens(p) and tokens(p)[-1] == q)
            ):
                plan.plural = True
        elif c.kind == "value":
            d, v = c.obj
            after = [toks[j] for j in range(c.start + c.length, min(len(toks), c.start + c.length + 3))]
            neg = any(a in NEGATE for a in after[:2])
            if neg:
                neg_idx = {c.start + c.length + k for k, a in enumerate(after[:2]) if a in NEGATE}
                taken |= neg_idx
                plan.kinds.update(dict.fromkeys(neg_idx, "filter"))
            f = next((f for f in plan.filters if f.dim is d and f.negate == neg), None)
            if f:
                if v not in f.values:
                    f.values.append(v)
            else:
                plan.filters.append(Filter(d, [v], neg))
    used |= taken

    # "Ekipman satışlarından elde edilen kâr": genel "satış" sözcüğü asıl ölçüyü nitelemek için kullanılmış
    if len(plan.metrics) > 1:
        strong = [m for m in plan.metrics if m not in weak]
        if strong:
            plan.metrics = strong
    # bir değerin hemen ardından gelen boyut sözcüğü onu niteler: "Ofis müşterileri", "Kenya AA ürünü"
    value_ends = {c.start + c.length for c in chosen if c.kind == "value"}
    qualifier = {c.obj.key for c in chosen if c.kind == "dim" and c.start in value_ends}
    if qualifier and not plan.order and not plan.share:
        plan.groups = [g for g in plan.groups if g.key not in qualifier]

    # zaman kırılımı
    for g, words in GRAINS.items():
        for i, t in enumerate(toks):
            if i in used:
                continue
            if (
                t in words
                or any(tok_match(t, w) for w in words if len(w) >= 5)
                or (
                    t == "her"
                    and i + 1 < len(toks)
                    and toks[i + 1]
                    in {"yil": ["yıl"], "ceyrek": ["çeyrek"], "ay": ["ay"], "hafta": ["hafta"], "gun": ["gün"]}[g]
                )
            ):
                plan.grain = plan.grain or g
                used.add(i)
                plan.kinds[i] = "grain"
                if t == "her":
                    used.add(i + 1)
                    plan.kinds[i + 1] = "grain"
    if plan.grain is None and any(t in TREND for t in toks) and layer.date_expr:
        span = (plan.time.end - plan.time.start).days if plan.time else 9999
        plan.grain = "gun" if span <= 31 else "ay" if span <= 800 else "ceyrek"
    if plan.grain is None and plan.time and " + " in plan.time.label and not plan.groups:
        plan.grain = (
            "yil"
            if all(
                tr_lower(x).strip()[:4].isdigit() or "yıl" in tr_lower(x) or "year" in tr_lower(x)
                for x in plan.time.label.split(" (")[0].split(" + ")
            )
            else "ay"
        )

    # sıralama ve limit
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if t in ("en", "daha") and (nxt in DESC or nxt in ASC or any(tok_match(nxt, w) for w in ("kârlı", "karlı"))):
            plan.order = "asc" if nxt in ASC else "desc"
            used |= {i, i + 1}
            plan.kinds.setdefault(i, "order")
            plan.kinds.setdefault(i + 1, "order")
        elif t in ("top", "ilk") and (nxt.isdigit() or nxt in NUM_WORDS or t == "top"):
            plan.order = plan.order or "desc"
            used.add(i)
            plan.kinds[i] = "order"
        elif t in ("bottom", "least", "lowest", "worst"):
            plan.order = "asc"
            used.add(i)
        elif t in ("highest", "most", "best", "largest", "biggest"):
            plan.order = plan.order or "desc"
            used.add(i)
    if plan.order:
        for i, t in enumerate(toks):
            if i in used:
                continue
            k = _num(t) if (t.isdigit() and len(t) <= 3) or t in NUM_WORDS else None
            if k and 1 <= k <= 100:
                plan.limit = k
                used.add(i)
                plan.kinds[i] = "limit"
                break

    plan.share = any(t in SHARE for t in toks)
    plan.kinds.update({i: "share" for i, t in enumerate(toks) if t in SHARE})

    # "hangi şehirlerde müşterimiz var" → şehir bazında müşteri sayısı
    cat_groups = [g for g in plan.groups]
    if not plan.explicit_metric and len(cat_groups) >= 2 and any(g.key == "musteri" for g in cat_groups):
        try:
            plan.metrics = [layer.metric("musteri_sayisi")]
            plan.explicit_metric = True
            plan.groups = [g for g in plan.groups if g.key != "musteri"]
        except StopIteration:
            pass
    plan.groups = plan.groups[:2]

    # filtre değeri verilmiş bir boyut için pay sorulursa: o boyutta grupla, değeri vurgula
    if plan.share:
        for f in list(plan.filters):
            if f.negate:
                continue
            if not plan.groups or f.dim in plan.groups:
                plan.groups = plan.groups or [f.dim]
                if f.dim in plan.groups:
                    plan.filters.remove(f)
                    plan.highlight = f.values
                    break
    # "Web kanalında", "Ege bölgesinde": boyut sözcüğü filtreyi niteliyor, gruplama değil
    if not plan.share:
        single = {f.dim.key for f in plan.filters if len(f.values) == 1 and not f.negate}
        plan.groups = [g for g in plan.groups if g.key not in single]
    # aynı boyuttan birden çok değer: karşılaştırma için o boyutta grupla
    for f in plan.filters:
        if len(f.values) > 1 and not f.negate and not plan.groups and f.dim.key not in ("urun",):
            plan.groups = [f.dim]

    if not plan.metrics:
        plan.metrics = [layer.metric(layer.default_metric)]

    # tanınmayan sözcükler
    for i, t in enumerate(toks):
        if i in used or t in STOPWORDS or t.isdigit() or len(t) < 3 or t in NUM_WORDS:
            continue
        if any(tok_match(t, s) for s in STOPWORDS if len(s) >= 3):
            continue
        plan.unknown.append(t)
        plan.kinds[i] = "unknown"
    return plan


def spans(question: str, plan: Plan) -> list[dict]:
    """Sorudaki her tanınan sözcüğün karakter aralığı ve türü (arayüzde renklendirme için)."""
    out = []
    for i, m in enumerate(_WORD_RE.finditer(tr_lower(question))):
        k = plan.kinds.get(i)
        if k:
            out.append({"s": m.start(), "e": m.end(), "k": k})
    return out


# ---------------------------------------------------------------- SQL üretimi


def _q(label: str) -> str:
    return '"' + label.replace('"', '""') + '"'


def _lit(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def grain_expr(g: str, d: str) -> str:
    return {
        "yil": f"strftime('%Y', {d})",
        "ceyrek": f"strftime('%Y', {d}) || '-Q' || ((CAST(strftime('%m', {d}) AS INTEGER) + 2) / 3)",
        "ay": f"strftime('%Y-%m', {d})",
        "hafta": f"strftime('%Y-W%W', {d})",
        "gun": f"date({d})",
    }[g]


def label_of(obj: Metric | Dimension, lang: str) -> str:
    return obj.label if lang == "tr" else (obj.label_en or obj.label)


def build_sql(plan: Plan, layer: Layer, lang: str = "tr", today: date | None = None) -> tuple[str, list[dict]]:
    """Plan → (SQL, sütun açıklamaları)."""
    today = today or local_today()
    needs: set[str] = set()
    for m in plan.metrics:
        needs |= set(m.needs)
    for g in plan.groups:
        needs |= set(g.needs)
    for f in plan.filters:
        needs |= set(f.dim.needs)

    select: list[str] = []
    cols: list[dict] = []
    group_n = 0
    if plan.grain and layer.date_expr:
        lbl = GRAIN_LABEL[plan.grain][0 if lang == "tr" else 1]
        select.append(f"{grain_expr(plan.grain, layer.date_expr)} AS {_q(lbl)}")
        cols.append({"name": lbl, "role": "time", "fmt": "period"})
        group_n += 1
    for g in plan.groups:
        lbl = label_of(g, lang)
        select.append(f"{g.expr} AS {_q(lbl)}")
        cols.append({"name": lbl, "role": "dim", "fmt": "text"})
        group_n += 1
    for m in plan.metrics:
        lbl = label_of(m, lang)
        select.append(f"{m.expr} AS {_q(lbl)}")
        cols.append({"name": lbl, "role": "metric", "fmt": m.fmt})
    if plan.share and group_n and plan.metrics[0].fmt != "pct":
        m = plan.metrics[0]
        lbl = "Pay (%)" if lang == "tr" else "Share (%)"
        raw = m.expr[6:-4] if m.expr.startswith("ROUND(") and m.expr.endswith(", 2)") else m.expr
        select.append(f"ROUND(100.0 * {raw} / SUM({raw}) OVER (), 1) AS {_q(lbl)}")
        cols.append({"name": lbl, "role": "share", "fmt": "pct"})

    where: list[str] = []
    if layer.base_where:
        where.append(layer.base_where)
    if plan.time and layer.date_expr:
        d = layer.date_expr
        where.append(f"{d} BETWEEN {_lit(plan.time.start.isoformat())} AND {_lit(plan.time.end.isoformat())}")
        if plan.time.same_period:
            where.append(f"strftime('%m-%d', {d}) <= {_lit(today.strftime('%m-%d'))}")
    for f in plan.filters:
        vals = ", ".join(_lit(v) for v in f.values)
        if len(f.values) == 1:
            where.append(f"{f.dim.expr} {'<>' if f.negate else '='} {vals}")
        else:
            where.append(f"{f.dim.expr} {'NOT IN' if f.negate else 'IN'} ({vals})")

    joins = [layer.joins[a] for a in layer.order if a in needs]
    sql = "SELECT\n  " + ",\n  ".join(select) + f"\nFROM {layer.base}"
    if joins:
        sql += "\n" + "\n".join(joins)
    if where:
        sql += "\nWHERE " + "\n  AND ".join(where)
    if group_n:
        sql += "\nGROUP BY " + ", ".join(str(i + 1) for i in range(group_n))
        metric_col = group_n + 1
        if plan.grain and not plan.order:
            sql += "\nORDER BY 1" + (f", {metric_col} DESC" if len(plan.groups) else "")
        elif plan.grain:
            sql += f"\nORDER BY {metric_col} {'ASC' if plan.order == 'asc' else 'DESC'}"
        else:
            sql += f"\nORDER BY {metric_col} {'ASC' if plan.order == 'asc' else 'DESC'}"
        limit = plan.limit
        if limit is None and plan.order and not plan.grain:
            limit = 10 if plan.plural else 1
        if limit is None and not plan.grain:
            limit = 50
        if limit:
            sql += f"\nLIMIT {limit}"
    return sql, cols


# ---------------------------------------------------------------- açıklama


def explain(plan: Plan, layer: Layer, lang: str = "tr") -> list[dict]:
    """Planı arayüzde gösterilecek adımlara çevirir."""
    tr = lang == "tr"
    out: list[dict] = []
    for m in plan.metrics:
        out.append(
            {"kind": "metric", "label": "Ölçü" if tr else "Measure", "text": label_of(m, lang), "detail": m.description}
        )
    if plan.grain:
        out.append(
            {
                "kind": "grain",
                "label": "Kırılım" if tr else "Breakdown",
                "text": GRAIN_LABEL[plan.grain][0 if tr else 1],
            }
        )
    for g in plan.groups:
        out.append({"kind": "dim", "label": "Gruplama" if tr else "Group by", "text": label_of(g, lang)})
    if plan.time:
        out.append(
            {
                "kind": "time",
                "label": "Dönem" if tr else "Period",
                "text": plan.time.label,
                "detail": (
                    f"{plan.time.start.isoformat()} → {plan.time.end.isoformat()}"
                    + (
                        (" · her yılın aynı dönemi" if tr else " · same period each year")
                        if plan.time.same_period
                        else ""
                    )
                ),
            }
        )
    for f in plan.filters:
        op = ("≠" if f.negate else "=") if len(f.values) == 1 else ("hariç" if tr else "not in") if f.negate else ("∈")
        out.append(
            {
                "kind": "filter",
                "label": "Filtre" if tr else "Filter",
                "text": f"{label_of(f.dim, lang)} {op} {', '.join(f.values)}",
            }
        )
    if plan.groups or plan.grain:
        if plan.order:
            out.append(
                {
                    "kind": "order",
                    "label": "Sıralama" if tr else "Order",
                    "text": ("en yüksekten" if tr else "highest first")
                    if plan.order == "desc"
                    else ("en düşükten" if tr else "lowest first"),
                }
            )
        lim = plan.limit or ((10 if plan.plural else 1) if plan.order and not plan.grain else None)
        if lim:
            out.append({"kind": "limit", "label": "Sınır" if tr else "Limit", "text": f"{lim}"})
    if plan.share and (plan.groups or plan.grain):
        out.append(
            {
                "kind": "share",
                "label": "Pay" if tr else "Share",
                "text": "toplamdaki yüzde" if tr else "percent of total",
            }
        )
    if layer.base_where_label:
        out.append(
            {
                "kind": "rule",
                "label": "Kural" if tr else "Rule",
                "text": layer.base_where_label if tr else "Cancelled orders excluded",
            }
        )
    return out
