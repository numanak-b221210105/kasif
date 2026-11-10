"""Kâşif motoru: soru → (kural motoru | LLM) → güvenlik denetimi → çalıştırma → özet."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date
from typing import Any

from . import llm
from .config import Settings
from .datasets import DEMO_ID, Dataset, Registry
from .demo import SCHEMA as DEMO_DDL
from .execute import QueryTimeout, connect_ro, run
from .guard import GuardError, check
from .insights import choose_chart, infer_columns, summarize
from .parser import Plan, build_sql, explain, label_of, parse
from .schema import Answer, Attempt, Column, PlanStep
from .semantic import Layer, auto_layer, demo_layer, load_values
from .store import History
from .text import local_today

DEMO_EXAMPLES = {
    "tr": [
        "Bu yıl toplam ciro ne kadar?",
        "En çok satan 5 ürün hangisi?",
        "Son 12 ayda aylık ciro",
        "Geçen çeyrekte bölgelere göre ciro",
        "Kanalların cirodaki payı",
        "En kârlı kategori hangisi?",
        "İzmir'deki kafelerin bu yılki cirosu",
        "Bu yıl geçen yıla göre ciro",
        "Kategorilere göre kâr marjı",
        "Kanallara göre aylık ciro bu yıl",
    ],
    "en": [
        "Total revenue this year",
        "Top 5 products by units",
        "Monthly revenue over the last 12 months",
        "Revenue by region last quarter",
        "Share of revenue by channel",
        "Top 3 customers by revenue last year",
    ],
}
LLM_SYSTEM = {
    "tr": """Sen deneyimli bir veri analistisin ve SQLite uzmanısın. Kullanıcının sorusunu aşağıdaki veritabanında
çalışacak TEK bir SELECT sorgusuna çevir.

Kurallar:
- Yalnızca SELECT yaz (WITH serbest). Veri ya da şema değiştiren hiçbir şey yazma.
- Yalnızca aşağıda listelenen tablo ve sütunları kullan. Metin değerlerini örneklerdeki yazımla birebir kullan.
- Tarihler 'YYYY-MM-DD' biçiminde TEXT olarak saklanır; dönemler için strftime kullan. Bugünün tarihi: {today}.
- Sütunlara anlaşılır Türkçe takma adlar ver (ör. AS "Ciro"). Para tutarlarını ROUND(..., 2) ile yuvarla.
- Liste döndüren sorgularda anlamlı bir ORDER BY kullan ve en fazla 50 satır döndür.
{rules}
Yanıtı YALNIZCA şu JSON biçiminde ver, başka hiçbir şey yazma:
{{"sql": "...", "explanation": "sorgunun ne yaptığını anlatan tek bir kısa Türkçe cümle"}}""",
    "en": """You are an experienced data analyst and SQLite expert. Translate the user's question into ONE SELECT query
for the database below.

Rules:
- SELECT only (WITH is allowed). Never modify data or schema.
- Use only the tables and columns listed below. Use text values exactly as spelled in the samples.
- Dates are stored as TEXT 'YYYY-MM-DD'; use strftime for periods. Today's date: {today}.
- Give columns readable aliases (e.g. AS "Revenue"). Round money with ROUND(..., 2).
- For lists, use a meaningful ORDER BY and return at most 50 rows.
{rules}
Reply ONLY with this JSON and nothing else:
{{"sql": "...", "explanation": "one short sentence describing what the query does"}}""",
}
DRILL = {
    "kategori": "urun",
    "bolge": "sehir",
    "sehir": "musteri",
    "segment": "musteri",
    "kanal": "kategori",
    "urun": "musteri",
    "musteri": "urun",
}


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _normalize(ans: Answer) -> None:
    """İç adımlarda sözlük olarak biriken alanları API modellerine çevirir."""
    if ans.columns and isinstance(ans.columns[0], str):
        ans.columns = [Column(name=n, role="dim", fmt="text") for n in ans.columns]  # type: ignore[arg-type]
    ans.columns = [c if isinstance(c, Column) else Column(**c) for c in ans.columns]
    ans.plan = [p if isinstance(p, PlanStep) else PlanStep(**p) for p in ans.plan]
    ans.attempts = [a if isinstance(a, Attempt) else Attempt(**a) for a in ans.attempts]


class Engine:
    def __init__(self, s: Settings, llm_client: Any | None = None, today: date | None = None):
        self.s = s
        self._today = today
        self.registry = Registry(s.data_dir)
        self.history = History(s.data_dir / "kasif.sqlite3")
        self.llm_client = llm_client
        self._layers: dict[tuple[str, float], Layer | None] = {}

    # ------------------------------------------------------------ yardımcılar
    @property
    def today(self) -> date:
        return self._today or local_today()

    def llm_ok(self) -> bool:
        return self.llm_client is not None or llm.reachable(self.s)

    def path(self, ds: Dataset | None = None):
        return self.registry.path(ds or self.registry.active)

    def tables(self, ds: Dataset | None = None) -> dict[str, dict[str, str]]:
        con = connect_ro(self.path(ds))
        try:
            names = [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY 1"
                )
            ]
            return {n: {c[1]: (c[2] or "TEXT") for c in con.execute(f"PRAGMA table_info({_quote(n)})")} for n in names}
        finally:
            con.close()

    def layer(self, ds: Dataset | None = None) -> Layer | None:
        ds = ds or self.registry.active
        p = self.path(ds)
        key = (ds.id, p.stat().st_mtime)
        if key in self._layers:
            return self._layers[key]
        con = connect_ro(p)
        try:
            if ds.id == DEMO_ID:
                layer: Layer | None = load_values(demo_layer(), con)
            else:
                tables = [
                    r[0]
                    for r in con.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                ]
                if not tables:
                    layer = None
                else:
                    sizes = {t: con.execute(f"SELECT COUNT(*) FROM {_quote(t)}").fetchone()[0] for t in tables}
                    layer = auto_layer(con, max(sizes, key=sizes.get), ds.labels)
        finally:
            con.close()
        self._layers = {k: v for k, v in self._layers.items() if k[0] != ds.id}
        self._layers[key] = layer
        return layer

    def info(self, ds: Dataset) -> dict:
        try:
            con = connect_ro(self.registry.path(ds))
            names = [
                r[0]
                for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            ]
            rows = sum(con.execute(f"SELECT COUNT(*) FROM {_quote(n)}").fetchone()[0] for n in names)
            con.close()
            return {"tables": len(names), "rows": rows}
        except sqlite3.Error:
            return {"tables": 0, "rows": 0}

    # ------------------------------------------------------------ şema
    def schema(self, lang: str = "tr") -> dict:
        ds = self.registry.active
        con = connect_ro(self.path(ds))
        out = []
        try:
            names = [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY 1"
                )
            ]
            for n in names:
                cols = con.execute(f"PRAGMA table_info({_quote(n)})").fetchall()
                fks = {
                    f[3]: {"table": f[2], "column": f[4]} for f in con.execute(f"PRAGMA foreign_key_list({_quote(n)})")
                }
                count = con.execute(f"SELECT COUNT(*) FROM {_quote(n)}").fetchone()[0]
                sample = con.execute(f"SELECT * FROM {_quote(n)} LIMIT 6").fetchall()
                columns = []
                for c in cols:
                    distinct = con.execute(f"SELECT COUNT(DISTINCT {_quote(c[1])}) FROM {_quote(n)}").fetchone()[0]
                    columns.append(
                        {
                            "name": c[1],
                            "type": c[2] or "TEXT",
                            "pk": bool(c[5]),
                            "fk": fks.get(c[1]),
                            "distinct": distinct,
                        }
                    )
                out.append({"name": n, "rows": count, "columns": columns, "sample": [list(r) for r in sample]})
        finally:
            con.close()
        layer = self.layer(ds)
        return {
            "dataset": ds.public(self.s.data_dir),
            "tables": out,
            "layer": layer.describe() if layer else None,
            "examples": self.examples(lang),
        }

    def examples(self, lang: str = "tr") -> list[str]:
        ds = self.registry.active
        if ds.id == DEMO_ID:
            return DEMO_EXAMPLES[lang]
        layer = self.layer(ds)
        if not layer:
            return []
        tr = lang == "tr"
        main = layer.metric(layer.default_metric)
        m = label_of(main, lang).lower()
        ex = [f"{label_of(main, lang)} ne kadar?" if tr else f"What is the {m}?"]
        for d in layer.dimensions[:3]:
            ex.append(f"{label_of(d, lang)} bazında {m}" if tr else f"{m} by {label_of(d, lang).lower()}")
        if layer.dimensions:
            d = layer.dimensions[0]
            ex.append(
                f"En yüksek {m} olan 5 {label_of(d, lang).lower()}"
                if tr
                else f"Top 5 {label_of(d, lang).lower()} by {m}"
            )
        if layer.date_expr:
            ex.append(f"Aylık {m}" if tr else f"Monthly {m}")
        ex.append("Kaç kayıt var?" if tr else "How many rows are there?")
        return ex

    # ------------------------------------------------------------ soru
    def ask(self, question: str, mode: str = "auto", lang: str = "tr") -> Answer:
        t0 = time.perf_counter()
        ds = self.registry.active
        layer = self.layer(ds)
        tr = lang == "tr"
        plan = parse(question, layer, self.today, lang) if layer else None
        llm_ok = mode != "rules" and self.llm_ok()

        use_rules = (
            plan is not None
            and plan.understood
            and (mode == "rules" or (mode == "auto" and (not plan.unknown or not llm_ok)))
        )
        if mode == "auto" and plan is not None and plan.understood and plan.unknown and not llm_ok:
            use_rules = False  # emin değiliz ve LLM yok: tahmin yürütme, kullanıcıya sor

        if use_rules:
            ans = self._answer_rules(question, plan, layer, lang)
        elif mode != "rules" and llm_ok:
            ans = self._answer_llm(question, layer, lang)
        else:
            ans = Answer(question=question, ok=False, mode="rules", dataset=ds.name)
            ans.error_code = "llm_unavailable" if mode == "llm" else "not_understood"
            if mode == "llm":
                ans.error = (
                    (
                        "LLM'e bağlanılamadı. Ayarlar sayfasındaki adımlarla Ollama'yı kurabilir ya da kural "
                        "motorunu kullanabilirsiniz."
                    )
                    if tr
                    else ("Could not reach the LLM. Set up Ollama from Settings, or use the rule engine.")
                )
            elif plan is not None and plan.understood:
                ans.error = (
                    (
                        f"Şu sözcükleri anlayamadım: {', '.join(plan.unknown)}. Tahmin yürütmek yerine "
                        "soruyorum: anladığım kadarıyla çalıştırayım mı?"
                    )
                    if tr
                    else (
                        f"I couldn't interpret: {', '.join(plan.unknown)}. Rather than guess, should I run what I "
                        "understood?"
                    )
                )
                ans.plan = explain(plan, layer, lang)  # type: ignore[arg-type,assignment]
                ans.can_force = True
            else:
                ans.error = (
                    (
                        "Bu soruyu veri setindeki kavramlarla eşleştiremedim. Aşağıdaki örneklerden birini "
                        "deneyin ya da LLM bağlayın."
                    )
                    if tr
                    else (
                        "I couldn't map this question to the dataset. Try one of the examples below or connect an LLM."
                    )
                )
            ans.unknown = plan.unknown if plan else []
            ans.suggestions = self.examples(lang)[:6]
        _normalize(ans)
        ans.elapsed_ms = round((time.perf_counter() - t0) * 1000)
        ans.dataset = ds.name
        ans.query_id = self.history.log(
            ds.id, question, ans.mode, ans.sql, ans.ok, ans.row_count, ans.elapsed_ms, ans.error
        )
        return ans

    def _execute(self, ans: Answer, sql: str, lang: str) -> None:
        checked = check(sql, self.tables(), self.s.max_rows)
        ans.tables = checked.tables
        cols, rows, truncated, ms = run(self.path(), checked.sql, self.s.max_rows, self.s.timeout_s)
        ans.rows, ans.row_count, ans.truncated, ans.query_ms = rows, len(rows), truncated, ms
        ans.columns = cols  # type: ignore[assignment] - aşağıda Column listesine çevrilir

    def _answer_rules(self, question: str, plan: Plan, layer: Layer, lang: str) -> Answer:
        tr = lang == "tr"
        ans = Answer(question=question, ok=False, mode="rules")
        sql, colspec = build_sql(plan, layer, lang, self.today)
        ans.sql = sql
        ans.plan = explain(plan, layer, lang)  # type: ignore[assignment]
        if plan.unknown:
            ans.warnings.append(("Şu sözcükler yok sayıldı: " if tr else "Ignored words: ") + ", ".join(plan.unknown))
            ans.unknown = plan.unknown
        try:
            self._execute(ans, sql, lang)
        except (GuardError, QueryTimeout, sqlite3.Error) as e:
            ans.error, ans.error_code = str(e), getattr(e, "code", "exec")
            return ans
        names = ans.columns  # type: ignore[assignment]
        ans.columns = [dict(c, name=n) for c, n in zip(colspec, names, strict=False)]  # type: ignore[assignment]
        ans.chart = choose_chart(ans.columns, ans.rows)  # type: ignore[arg-type]
        ans.summary = summarize(
            ans.columns,
            ans.rows,
            lang,
            ans.chart,
            plan.highlight,
            plan.order,
            self.today,  # type: ignore[arg-type]
            plan.time.same_period if plan.time else False,
            ans.truncated,
        )
        ans.ok = True
        ans.followups = self.followups(plan, layer, ans, lang)
        return ans

    # ------------------------------------------------------------ LLM
    def llm_prompt(self, layer: Layer | None, lang: str) -> str:
        ds = self.registry.active
        con = connect_ro(self.path())
        try:
            if ds.id == DEMO_ID:
                ddl = DEMO_DDL.strip()
            else:
                ddl = "\n".join(
                    r[0] + ";"
                    for r in con.execute(
                        "SELECT sql FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
                        "AND sql IS NOT NULL"
                    )
                )
            samples = []
            for t, cols in self.tables().items():
                for c, typ in cols.items():
                    if "TEXT" not in typ.upper() and typ:
                        continue
                    n = con.execute(f"SELECT COUNT(DISTINCT {_quote(c)}) FROM {_quote(t)}").fetchone()[0]
                    if 0 < n <= 25:
                        vals = [
                            str(r[0])
                            for r in con.execute(
                                f"SELECT DISTINCT {_quote(c)} FROM {_quote(t)} WHERE {_quote(c)} IS NOT NULL LIMIT 25"
                            )
                        ]
                        samples.append(f"-- {t}.{c}: " + ", ".join(vals))
        finally:
            con.close()
        rules = ""
        if layer and ds.id == DEMO_ID:
            tr = lang == "tr"
            rules = (
                "- İş kuralları (k = siparis_kalemleri, s = siparisler, u = urunler, m = musteriler):\n"
                if tr
                else "- Business rules (k = siparis_kalemleri, s = siparisler, u = urunler, m = musteriler):\n"
            )
            rules += f"  * {('Satış ölçülerinde yalnızca ' if tr else 'Sales metrics only use ')}{layer.base_where}\n"
            for m in layer.metrics:
                rules += f"  * {label_of(m, lang)} = {m.expr}\n"
        prompt = LLM_SYSTEM[lang].format(today=self.today.isoformat(), rules=rules)
        examples = ""
        if layer:
            shots = DEMO_EXAMPLES["tr"][:3] if ds.id == DEMO_ID else self.examples("tr")[1:3]
            for q in shots:
                p = parse(q, layer, self.today, "tr")
                if p.understood and not p.unknown:
                    sql, _ = build_sql(p, layer, "tr", self.today)
                    examples += f"\nSoru: {q}\n" + json.dumps({"sql": sql, "explanation": "…"}, ensure_ascii=False)
        return (
            f"{prompt}\n\nVeritabanı şeması:\n{ddl}\n\nÖrnek değerler:\n"
            + "\n".join(samples)
            + (f"\n\nÖrnekler:{examples}" if examples else "")
        )

    @staticmethod
    def _parse_llm(text: str) -> tuple[str, str | None]:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict) and obj.get("sql"):
                    return str(obj["sql"]), (str(obj.get("explanation")) if obj.get("explanation") else None)
            except json.JSONDecodeError:
                pass
        m = re.search(r"```(?:sql)?\s*(.+?)```", text, re.S | re.I)
        if m:
            return m.group(1).strip(), None
        m = re.search(r"((?:WITH|SELECT)\b.+)", text, re.S | re.I)
        return (m.group(1).strip() if m else text.strip()), None

    def _answer_llm(self, question: str, layer: Layer | None, lang: str) -> Answer:
        tr = lang == "tr"
        ans = Answer(question=question, ok=False, mode="llm")
        messages = [{"role": "system", "content": self.llm_prompt(layer, lang)}, {"role": "user", "content": question}]
        last_err = None
        for _attempt in range(2):
            try:
                reply = llm.chat(self.s, messages, client=self.llm_client)
            except Exception as e:  # noqa: BLE001 - bağlantı/model hatası kullanıcıya gösterilir
                ans.error, ans.error_code = (f"LLM yanıt vermedi: {e}" if tr else f"LLM did not respond: {e}"), "llm"
                return ans
            sql, expl = self._parse_llm(reply)
            ans.sql, ans.explanation = sql, expl
            try:
                self._execute(ans, sql, lang)
                last_err = None
                break
            except (GuardError, QueryTimeout, sqlite3.Error) as e:
                last_err = e
                ans.attempts.append({"sql": sql, "error": str(e)})  # type: ignore[arg-type]
                if isinstance(e, GuardError) and e.code in ("write", "multi", "func", "db"):
                    break  # güvenlik ihlali: düzeltme denemesi yapılmaz
                messages += [
                    {"role": "assistant", "content": reply},
                    {
                        "role": "user",
                        "content": (f"Bu sorgu hata verdi: {e}\nHatayı düzelt ve yalnızca JSON döndür.")
                        if tr
                        else f"That query failed: {e}\nFix it and reply with JSON only.",
                    },
                ]
        if last_err is not None:
            ans.error, ans.error_code = str(last_err), getattr(last_err, "code", "exec")
            return ans
        names = ans.columns  # type: ignore[assignment]
        ans.columns = infer_columns(names, ans.rows)  # type: ignore[arg-type,assignment]
        ans.chart = choose_chart(ans.columns, ans.rows)  # type: ignore[arg-type]
        ans.summary = summarize(ans.columns, ans.rows, lang, ans.chart, today=self.today, truncated=ans.truncated)  # type: ignore[arg-type]
        ans.plan = [
            {"kind": "llm", "label": "Model", "text": self.s.llm_model},  # type: ignore[list-item]
            {"kind": "tables", "label": "Tablolar" if tr else "Tables", "text": ", ".join(ans.tables) or "—"},
        ]
        if ans.attempts:
            ans.plan.append(
                {
                    "kind": "fix",
                    "label": "Düzeltme" if tr else "Self-fix",  # type: ignore[arg-type]
                    "text": (
                        "ilk sorgu hata verdi, model düzeltti" if tr else "first query failed, the model fixed it"
                    ),
                }
            )
        ans.plan.append(
            {
                "kind": "guard",
                "label": "Güvenlik" if tr else "Guard",  # type: ignore[arg-type]
                "text": "salt-okunur, denetlendi" if tr else "read-only, checked",
            }
        )
        ans.ok = True
        return ans

    # ------------------------------------------------------------ elle SQL
    def run_sql(self, sql: str, lang: str = "tr", question: str | None = None) -> Answer:
        t0 = time.perf_counter()
        ans = Answer(question=question or sql.strip().splitlines()[0][:120], ok=False, mode="sql", sql=sql)
        try:
            self._execute(ans, sql, lang)
            names = ans.columns  # type: ignore[assignment]
            ans.columns = infer_columns(names, ans.rows)  # type: ignore[arg-type,assignment]
            ans.chart = choose_chart(ans.columns, ans.rows)  # type: ignore[arg-type]
            ans.summary = summarize(ans.columns, ans.rows, lang, ans.chart, today=self.today, truncated=ans.truncated)  # type: ignore[arg-type]
            ans.ok = True
        except (GuardError, QueryTimeout, sqlite3.Error) as e:
            ans.error, ans.error_code = str(e), getattr(e, "code", "exec")
        _normalize(ans)
        ans.elapsed_ms = round((time.perf_counter() - t0) * 1000)
        ans.dataset = self.registry.active.name
        ans.query_id = self.history.log(
            self.registry.active.id, ans.question, "sql", sql, ans.ok, ans.row_count, ans.elapsed_ms, ans.error
        )
        return ans

    # ------------------------------------------------------------ takip soruları
    def followups(self, plan: Plan, layer: Layer, ans: Answer, lang: str) -> list[str]:
        if lang != "tr":
            return []
        m = plan.metrics[0]
        metric = m.label.lower().replace(" (%)", "")
        when = plan.time.label.split(" (")[0] if plan.time else ""
        if " + " in when:
            when = ""
        elif when.split(" ")[0] in ("Bu", "Geçen", "Son", "Dün", "Bugün"):
            when = when[0].lower() + when[1:]
        filt = " ".join(v for f in plan.filters if not f.negate for v in f.values[:1])
        cands: list[str] = []
        used = {g.key for g in plan.groups} | {f.dim.key for f in plan.filters}
        if layer.date_expr and not plan.grain:
            cands.append(f"{when} {filt} aylık {metric}".strip())
        if plan.groups and ans.rows and plan.groups[0].key in DRILL:
            nxt = DRILL[plan.groups[0].key]
            try:
                d2 = layer.dimension(nxt)
                top = ans.rows[0][1 if plan.grain else 0]
                if nxt not in used and isinstance(top, str):
                    cands.append(f"{when} {top} için {d2.label.lower()} bazında {metric}".strip())
            except StopIteration:
                pass
        if not plan.groups:
            for key in ("kategori", "bolge", "kanal", "segment"):
                if key not in used:
                    try:
                        cands.append(f"{when} {filt} {layer.dimension(key).label.lower()} bazında {metric}".strip())
                        break
                    except StopIteration:
                        continue
        if m.key == "ciro":
            g = f"{plan.groups[0].label.lower()} bazında " if plan.groups else ""
            cands.append(f"{when} {filt} {g}kâr marjı".strip())
        elif plan.groups and not plan.share and m.fmt != "pct":
            cands.append(f"{when} {plan.groups[0].label.lower()} bazında {metric} payı".strip())
        out: list[str] = []
        for c in cands:
            c = re.sub(r"\s+", " ", c).strip()
            c = c[0].upper() + c[1:]
            p = parse(c, layer, self.today, "tr")
            if p.understood and not p.unknown and c not in out and c.lower() != ans.question.lower():
                out.append(c)
        return out[:3]
