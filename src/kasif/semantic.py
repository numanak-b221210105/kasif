"""Anlam katmanı: iş dilindeki kavramların (ciro, kategori, bölge...) SQL karşılıkları.

Kural motoru soruyu doğrudan SQL'e değil, önce bu katmandaki ölçü ve boyutlara
çevirir. Böylece üretilen her sorgu tanımlı, denetlenebilir ve açıklanabilir olur.
Demo veritabanı için katman elle yazılmıştır; yüklenen veriler için tablo
yapısından otomatik çıkarılır.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from .text import ascii_fold, tokens, tr_lower


@dataclass
class Metric:
    key: str
    label: str
    expr: str
    fmt: str  # money | int | num | pct
    synonyms: list[str]
    needs: tuple[str, ...] = ()
    description: str = ""
    label_en: str = ""


@dataclass
class Dimension:
    key: str
    label: str
    expr: str
    synonyms: list[str]
    needs: tuple[str, ...] = ()
    values: list[str] = field(default_factory=list)
    label_en: str = ""
    plural: list[str] = field(default_factory=list)  # çoğul biçimler: "ürünler" → ilk N listesi


@dataclass
class Layer:
    name: str
    base: str  # FROM ...
    joins: dict[str, str]  # takma ad → JOIN cümlesi
    date_expr: str | None
    base_where: str | None
    base_where_label: str | None
    metrics: list[Metric]
    dimensions: list[Dimension]
    default_metric: str
    order: list[str] = field(default_factory=list)  # JOIN sırası

    def metric(self, key: str) -> Metric:
        return next(m for m in self.metrics if m.key == key)

    def dimension(self, key: str) -> Dimension:
        return next(d for d in self.dimensions if d.key == key)

    def describe(self) -> dict:
        return {
            "name": self.name,
            "metrics": [
                {
                    "key": m.key,
                    "label": m.label,
                    "label_en": m.label_en or m.label,
                    "expr": m.expr,
                    "description": m.description,
                    "synonyms": m.synonyms[:6],
                }
                for m in self.metrics
            ],
            "dimensions": [
                {
                    "key": d.key,
                    "label": d.label,
                    "label_en": d.label_en or d.label,
                    "expr": d.expr,
                    "values": d.values[:12],
                    "value_count": len(d.values),
                    "synonyms": d.synonyms[:6],
                }
                for d in self.dimensions
            ],
            "filter": self.base_where_label,
            "date": self.date_expr,
        }


UNIT_TOKENS = {"tl", "try", "usd", "eur", "kg", "₺"}
MONEY_WORDS = (
    "tutar",
    "fiyat",
    "ciro",
    "gelir",
    "maliyet",
    "ücret",
    "price",
    "amount",
    "cost",
    "revenue",
    "(tl)",
    "₺",
    "total",
    "satış",
)
REVENUE = "k.adet * k.birim_fiyat * (1 - k.indirim)"


def demo_layer() -> Layer:
    metrics = [
        Metric(
            "ciro",
            "Ciro",
            f"ROUND(SUM({REVENUE}), 2)",
            "money",
            [
                "ciro",
                "gelir",
                "hasılat",
                "satış tutarı",
                "satış geliri",
                "satış",
                "satışlar",
                "alışveriş",
                "kazandık",
                "revenue",
                "sales",
                "turnover",
            ],
            description="İndirim düşülmüş satış tutarı (adet × birim fiyat × (1 − indirim))",
            label_en="Revenue",
        ),
        Metric(
            "adet",
            "Satılan adet",
            "SUM(k.adet)",
            "int",
            [
                "satılan adet",
                "satış adedi",
                "adet",
                "miktar",
                "kaç tane",
                "kaç adet",
                "satan",
                "satılan",
                "units",
                "quantity",
                "best selling",
                "selling",
            ],
            description="Satılan ürün adedi",
            label_en="Units sold",
        ),
        Metric(
            "siparis",
            "Sipariş sayısı",
            "COUNT(DISTINCT s.id)",
            "int",
            ["sipariş sayısı", "kaç sipariş", "sipariş adedi", "sipariş", "orders", "order count"],
            description="Tamamlanan sipariş sayısı",
            label_en="Orders",
        ),
        Metric(
            "musteri_sayisi",
            "Müşteri sayısı",
            "COUNT(DISTINCT s.musteri_id)",
            "int",
            [
                "müşteri sayısı",
                "kaç müşteri",
                "aktif müşteri",
                "alışveriş yapan müşteri",
                "number of customers",
                "customer count",
                "active customers",
            ],
            description="Sipariş veren farklı müşteri sayısı",
            label_en="Customers",
        ),
        Metric(
            "sepet",
            "Ortalama sepet",
            f"ROUND(SUM({REVENUE}) / COUNT(DISTINCT s.id), 2)",
            "money",
            [
                "ortalama sepet tutarı",
                "ortalama sepet",
                "sepet ortalaması",
                "ortalama sipariş tutarı",
                "sipariş başına",
                "sepet",
                "average order value",
                "basket",
            ],
            description="Sipariş başına ortalama ciro",
            label_en="Avg. order value",
        ),
        Metric(
            "kar",
            "Brüt kâr",
            "ROUND(SUM(k.adet * (k.birim_fiyat * (1 - k.indirim) - k.birim_maliyet)), 2)",
            "money",
            ["brüt kâr", "brüt kar", "kâr", "kar", "kazanç", "karlılık", "kârlılık", "profit", "gross profit"],
            description="Ciro − satılan malın maliyeti",
            label_en="Gross profit",
        ),
        Metric(
            "marj",
            "Kâr marjı (%)",
            f"ROUND(100.0 * SUM(k.adet * (k.birim_fiyat * (1 - k.indirim) - k.birim_maliyet)) / SUM({REVENUE}), 1)",
            "pct",
            ["kâr marjı", "kar marjı", "marj", "margin"],
            description="Brüt kârın ciroya oranı",
            label_en="Margin (%)",
        ),
        Metric(
            "indirim",
            "İndirim tutarı",
            "ROUND(SUM(k.adet * k.birim_fiyat * k.indirim), 2)",
            "money",
            ["indirim tutarı", "verilen indirim", "indirim", "iskonto", "discount"],
            description="Liste fiyatından yapılan toplam indirim",
            label_en="Discounts",
        ),
    ]
    dims = [
        Dimension(
            "urun",
            "Ürün",
            "u.ad",
            ["ürün", "product", "item"],
            ("u",),
            label_en="Product",
            plural=["ürünler", "products", "items"],
        ),
        Dimension(
            "kategori",
            "Kategori",
            "u.kategori",
            ["kategori", "ürün grubu", "category"],
            ("u",),
            label_en="Category",
            plural=["kategoriler", "categories"],
        ),
        Dimension(
            "musteri",
            "Müşteri",
            "m.ad",
            ["müşteri", "firma", "customer", "client"],
            ("m",),
            label_en="Customer",
            plural=["müşteriler", "firmalar", "customers", "clients"],
        ),
        Dimension(
            "sehir",
            "Şehir",
            "m.sehir",
            ["şehir", "il", "city"],
            ("m",),
            label_en="City",
            plural=["şehirler", "iller", "cities"],
        ),
        Dimension(
            "bolge", "Bölge", "m.bolge", ["bölge", "region"], ("m",), label_en="Region", plural=["bölgeler", "regions"]
        ),
        Dimension(
            "segment",
            "Segment",
            "m.segment",
            ["segment", "müşteri tipi", "müşteri türü", "sektör"],
            ("m",),
            label_en="Segment",
            plural=["segmentler", "segments"],
        ),
        Dimension(
            "kanal",
            "Satış kanalı",
            "s.kanal",
            ["satış kanalı", "kanal", "channel"],
            (),
            label_en="Channel",
            plural=["kanallar", "channels"],
        ),
    ]
    return Layer(
        name="Demlik Kahve A.Ş.",
        base="siparis_kalemleri k\nJOIN siparisler s ON s.id = k.siparis_id",
        joins={"u": "JOIN urunler u ON u.id = k.urun_id", "m": "JOIN musteriler m ON m.id = s.musteri_id"},
        order=["u", "m"],
        date_expr="s.tarih",
        base_where="s.durum = 'tamamlandı'",
        base_where_label="İptal edilen siparişler hariç",
        metrics=metrics,
        dimensions=dims,
        default_metric="ciro",
    )


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _humanize(col: str) -> str:
    s = col.replace("_", " ").strip()
    return s[:1].upper() + s[1:]


def auto_layer(con: sqlite3.Connection, table: str, labels: dict[str, str] | None = None) -> Layer:
    """Tek bir tablo için ölçü ve boyutları sütun tiplerinden çıkarır.

    `labels`: CSV'deki orijinal başlıklar ("sube" → "Şube"); Türkçe eşanlamlı olarak kullanılır.
    """
    cols = con.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
    n_rows = con.execute(f"SELECT COUNT(*) FROM {_quote(table)}").fetchone()[0] or 1
    metrics = [
        Metric(
            "kayit",
            "Kayıt sayısı",
            "COUNT(*)",
            "int",
            ["kayıt sayısı", "kaç kayıt", "satır sayısı", "kayıt", "kaç", "sayısı", "count", "rows"],
            description="Satır sayısı",
            label_en="Rows",
        )
    ]
    dims: list[Dimension] = []
    date_expr = None
    for _cid, name, ctype, *_ in cols:
        q = f"t.{_quote(name)}"
        orig = (labels or {}).get(name)
        human = orig or _humanize(name)
        raw = re.sub(r"\(.*?\)", " ", orig or name.replace("_", " "))
        words = " ".join(t for t in tokens(raw) if t not in UNIT_TOKENS) or " ".join(tokens(raw))
        folded = " ".join(tokens(name.replace("_", " ")))
        ctype = (ctype or "").upper()
        if any(k in ctype for k in ("INT", "REAL", "NUM", "FLOA", "DOUB", "DEC")):
            if name.lower() == "id" or name.lower().endswith("_id"):
                continue
            low = tr_lower(orig or name)
            money = any(w in low for w in MONEY_WORDS)
            qty = any(w in low for w in ("adet", "miktar", "quantity", "qty", "units"))
            extra = (
                ["ciro", "satış", "satışlar", "gelir", "hasılat", "tutar", "revenue", "sales"]
                if money
                else ["satan", "satılan", "kaç adet", "kaç tane", "miktar", "units", "selling"]
                if qty
                else []
            )
            key = ascii_fold(name)
            metrics.append(
                Metric(
                    f"sum_{key}",
                    f"Toplam {words}",
                    f"ROUND(SUM({q}), 2)",
                    "money" if money else "int" if (qty or "INT" in ctype) else "num",
                    [f"toplam {words}", words, folded, f"total {words}", *extra],
                    description=f"{orig or name} sütununun toplamı",
                    label_en=f"Total {words}",
                )
            )
            metrics.append(
                Metric(
                    f"avg_{key}",
                    f"Ortalama {words}",
                    f"ROUND(AVG({q}), 2)",
                    "money" if money else "num",
                    [f"ortalama {words}", f"ortalama {folded}", f"average {words}", f"avg {words}"],
                    description=f"{orig or name} sütununun ortalaması",
                    label_en=f"Average {words}",
                )
            )
            continue
        sample = con.execute(f"SELECT {_quote(name)} FROM {_quote(table)} WHERE {_quote(name)} IS NOT NULL LIMIT 1")
        first = (sample.fetchone() or [None])[0]
        if date_expr is None and isinstance(first, str) and len(first) >= 7 and first[:4].isdigit() and first[4] == "-":
            date_expr = q
            continue
        distinct = con.execute(f"SELECT COUNT(DISTINCT {_quote(name)}) FROM {_quote(table)}").fetchone()[0]
        if 1 < distinct <= min(300, max(2, n_rows // 2)):
            values = [
                r[0]
                for r in con.execute(
                    f"SELECT DISTINCT {_quote(name)} FROM {_quote(table)} WHERE {_quote(name)} IS NOT NULL ORDER BY 1 LIMIT 300"
                )
            ]
            syns = [words] + ([folded] if folded != words else [])
            dims.append(Dimension(ascii_fold(name), human, q, syns, values=[str(v) for v in values], label_en=human))
    default = next(
        (m.key for m in metrics if m.key.startswith("sum_") and m.fmt == "money"),
        next((m.key for m in metrics if m.key.startswith("sum_")), "kayit"),
    )
    # ölçüleri, varsayılan ölçü önce gelecek şekilde sırala (örnek sorular ve öneriler için)
    metrics.sort(key=lambda m: (m.key != default, m.key == "kayit"))
    return Layer(
        name=table,
        base=f"{_quote(table)} t",
        joins={},
        date_expr=date_expr,
        base_where=None,
        base_where_label=None,
        metrics=metrics,
        dimensions=dims,
        default_metric=default,
    )


def load_values(layer: Layer, con: sqlite3.Connection) -> Layer:
    """Demo katmanındaki boyutların olası değerlerini veritabanından okur."""
    for d in layer.dimensions:
        if d.values:
            continue
        joins = " ".join(layer.joins[a] for a in layer.order if a in d.needs)
        sql = f"SELECT DISTINCT {d.expr} FROM {layer.base} {joins} ORDER BY 1"
        d.values = [str(r[0]) for r in con.execute(sql) if r[0] is not None]
    return layer
