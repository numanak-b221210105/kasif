"""Demo veritabanı: kurgusal "Demlik Kahve A.Ş." (kahve kavurma ve toptan satış).

Veri tamamen sentetiktir ve sabit bir tohumla üretilir: aynı bitiş tarihiyle her
çalıştırmada birebir aynı veritabanı oluşur. Gerçekçi olması için mevsimsellik,
yıllık büyüme, fiyat artışı, kanal ve segment farkları ile iptal edilen
siparişler içerir.
"""

from __future__ import annotations

import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .text import local_today

SEED = 1907

SCHEMA = """
CREATE TABLE musteriler (
    id INTEGER PRIMARY KEY,
    ad TEXT NOT NULL,            -- müşteri (firma) adı
    sehir TEXT NOT NULL,
    bolge TEXT NOT NULL,          -- coğrafi bölge
    segment TEXT NOT NULL,        -- Kafe, Restoran, Otel, Market, Ofis
    kayit_tarihi TEXT NOT NULL    -- YYYY-MM-DD
);
CREATE TABLE urunler (
    id INTEGER PRIMARY KEY,
    ad TEXT NOT NULL,
    kategori TEXT NOT NULL,       -- Çekirdek Kahve, Öğütülmüş Kahve, Kapsül, Ekipman, Aksesuar
    liste_fiyati REAL NOT NULL    -- güncel liste fiyatı, TL
);
CREATE TABLE siparisler (
    id INTEGER PRIMARY KEY,
    musteri_id INTEGER NOT NULL REFERENCES musteriler(id),
    tarih TEXT NOT NULL,          -- YYYY-MM-DD
    kanal TEXT NOT NULL,          -- Saha Satış, Web, Pazaryeri
    durum TEXT NOT NULL           -- tamamlandı, iptal
);
CREATE TABLE siparis_kalemleri (
    id INTEGER PRIMARY KEY,
    siparis_id INTEGER NOT NULL REFERENCES siparisler(id),
    urun_id INTEGER NOT NULL REFERENCES urunler(id),
    adet INTEGER NOT NULL,
    birim_fiyat REAL NOT NULL,    -- o tarihteki satış fiyatı, TL
    indirim REAL NOT NULL,        -- 0-1 arası oran (0.1 = %10)
    birim_maliyet REAL NOT NULL   -- o tarihteki birim maliyet, TL
);
CREATE INDEX ix_siparis_tarih ON siparisler(tarih);
CREATE INDEX ix_kalem_siparis ON siparis_kalemleri(siparis_id);
"""

CITIES = [
    ("İstanbul", "Marmara", 30),
    ("Bursa", "Marmara", 5),
    ("Kocaeli", "Marmara", 4),
    ("Ankara", "İç Anadolu", 13),
    ("Eskişehir", "İç Anadolu", 4),
    ("Konya", "İç Anadolu", 3),
    ("İzmir", "Ege", 12),
    ("Muğla", "Ege", 5),
    ("Antalya", "Akdeniz", 8),
    ("Adana", "Akdeniz", 3),
    ("Mersin", "Akdeniz", 3),
    ("Trabzon", "Karadeniz", 3),
    ("Samsun", "Karadeniz", 2),
    ("Gaziantep", "Güneydoğu Anadolu", 3),
    ("Diyarbakır", "Güneydoğu Anadolu", 1),
    ("Erzurum", "Doğu Anadolu", 1),
]

# segment: (ağırlık, sipariş sıklığı çarpanı, sepet büyüklüğü çarpanı)
SEGMENTS = {
    "Kafe": (40, 1.3, 1.0),
    "Restoran": (20, 0.9, 1.1),
    "Otel": (10, 0.8, 2.2),
    "Market": (15, 1.1, 1.6),
    "Ofis": (15, 0.6, 0.7),
}

FIRST = [
    "Lale",
    "Çınar",
    "Martı",
    "Ada",
    "Kule",
    "Liman",
    "Bulut",
    "Pera",
    "Moda",
    "Asmalı",
    "Nar",
    "Ayva",
    "Defne",
    "Zeytin",
    "Fener",
    "Kordon",
    "Badem",
    "Yosun",
    "Kuzey",
    "Poyraz",
    "Mavi",
    "Tepe",
    "Köprü",
    "Saray",
    "Selvi",
    "Ilgaz",
    "Mercan",
    "Akasya",
    "Rüzgar",
    "Kardelen",
    "Eflatun",
    "Sarnıç",
    "Kervan",
    "Begonya",
    "Orkide",
    "Pervane",
    "Kumsal",
    "Gümüş",
    "Lacivert",
    "Yakamoz",
]
SUFFIX = {
    "Kafe": ["Kahvecisi", "Kahve Evi", "Coffee", "Kafe", "Kahve Durağı"],
    "Restoran": ["Lokantası", "Restoran", "Bistro", "Meyhanesi", "Mutfak"],
    "Otel": ["Otel", "Butik Otel", "Resort", "Konukevi"],
    "Market": ["Market", "Gurme", "Şarküteri", "Bakkal"],
    "Ofis": ["Yazılım", "Danışmanlık", "Holding", "Mimarlık", "Hukuk Bürosu"],
}

# (ad, kategori, 2026 liste fiyatı, maliyet oranı, talep ağırlığı)
PRODUCTS = [
    ("Espresso Harmanı 1 kg", "Çekirdek Kahve", 1150, 0.52, 30),
    ("Brezilya Santos 1 kg", "Çekirdek Kahve", 980, 0.50, 18),
    ("Etiyopya Yirgacheffe 1 kg", "Çekirdek Kahve", 1480, 0.55, 9),
    ("Kolombiya Supremo 1 kg", "Çekirdek Kahve", 1260, 0.53, 12),
    ("Guatemala Antigua 1 kg", "Çekirdek Kahve", 1390, 0.54, 6),
    ("Kenya AA 1 kg", "Çekirdek Kahve", 1620, 0.57, 4),
    ("Filtre Kahve 250 g", "Öğütülmüş Kahve", 290, 0.45, 20),
    ("Türk Kahvesi 250 g", "Öğütülmüş Kahve", 210, 0.42, 26),
    ("Türk Kahvesi 1 kg", "Öğütülmüş Kahve", 760, 0.44, 10),
    ("Moka Pot Öğütüm 250 g", "Öğütülmüş Kahve", 270, 0.46, 6),
    ("Kapsül Espresso 10'lu", "Kapsül", 185, 0.40, 16),
    ("Kapsül Lungo 10'lu", "Kapsül", 185, 0.40, 9),
    ("Kapsül Kafeinsiz 10'lu", "Kapsül", 195, 0.41, 4),
    ("Soğuk Demleme Paketi", "Kapsül", 240, 0.43, 5),
    ("Espresso Makinesi Pro", "Ekipman", 48500, 0.68, 0.35),
    ("Kahve Değirmeni", "Ekipman", 14900, 0.64, 0.6),
    ("Filtre Kahve Makinesi", "Ekipman", 6900, 0.62, 0.9),
    ("Süt Köpürtücü", "Ekipman", 3200, 0.58, 1.2),
    ("V60 Demleme Seti", "Aksesuar", 890, 0.48, 3),
    ("French Press 1 L", "Aksesuar", 760, 0.47, 2.5),
    ("Barista Termometresi", "Aksesuar", 420, 0.45, 1.5),
    ("Karton Bardak 1000'li", "Aksesuar", 1350, 0.60, 7),
    ("Kahve Filtre Kağıdı 100'lü", "Aksesuar", 140, 0.40, 8),
    ("Tamper 58 mm", "Aksesuar", 950, 0.50, 1),
]

CHANNELS = [("Saha Satış", 55), ("Web", 30), ("Pazaryeri", 15)]

# aylık mevsimsellik (Ocak..Aralık)
SEASON = [0.92, 0.88, 0.95, 1.0, 1.02, 0.96, 0.9, 0.9, 1.04, 1.1, 1.12, 1.26]
START = date(2024, 1, 1)


def _price_index(d: date) -> float:
    """2026 başı = 1.0; aylık ~%2,6 fiyat artışı."""
    months = (d.year - 2026) * 12 + (d.month - 1)
    return 1.026**months


def _demand_index(d: date) -> float:
    """Yıllık ~%22 hacim büyümesi ve mevsimsellik."""
    years = (d - START).days / 365.25
    return (1.22**years) * SEASON[d.month - 1]


def build(path: Path | str, end: date | None = None, seed: int = SEED) -> Path:
    """Demo veritabanını `path` konumuna (yeniden) oluşturur."""
    end = end or local_today()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    rng = random.Random(seed)
    con = sqlite3.connect(tmp)
    con.executescript(SCHEMA)

    # müşteriler
    customers: list[tuple] = []
    used: set[str] = set()
    seg_names = list(SEGMENTS)
    seg_w = [SEGMENTS[s][0] for s in seg_names]
    city_w = [c[2] for c in CITIES]
    for cid in range(1, 141):
        seg = rng.choices(seg_names, seg_w)[0]
        while True:
            name = f"{rng.choice(FIRST)} {rng.choice(SUFFIX[seg])}"
            if name not in used:
                used.add(name)
                break
        city, region, _ = rng.choices(CITIES, city_w)[0]
        if rng.random() < 0.45:
            joined = START + timedelta(days=rng.randint(0, 90))
        else:
            joined = START + timedelta(days=int(rng.betavariate(1.3, 1.8) * ((end - START).days - 30)))
        customers.append((cid, name, city, region, seg, joined.isoformat()))
    con.executemany("INSERT INTO musteriler VALUES (?,?,?,?,?,?)", customers)

    products = [(i + 1, p[0], p[1], float(p[2])) for i, p in enumerate(PRODUCTS)]
    con.executemany("INSERT INTO urunler VALUES (?,?,?,?)", products)
    prod_w = [p[4] for p in PRODUCTS]
    # segmentlere göre ürün eğilimi
    seg_bias = {
        "Kafe": {"Çekirdek Kahve": 1.6, "Ekipman": 1.3, "Aksesuar": 1.5, "Kapsül": 0.4},
        "Restoran": {"Türk": 1.0, "Öğütülmüş Kahve": 1.5, "Kapsül": 0.6},
        "Otel": {"Kapsül": 2.2, "Ekipman": 1.4},
        "Market": {"Öğütülmüş Kahve": 1.8, "Kapsül": 1.6, "Ekipman": 0.3, "Aksesuar": 0.6},
        "Ofis": {"Kapsül": 2.6, "Öğütülmüş Kahve": 1.2, "Çekirdek Kahve": 0.5},
    }
    weights_by_seg = {
        seg: [w * seg_bias[seg].get(PRODUCTS[i][1], 1.0) for i, w in enumerate(prod_w)] for seg in SEGMENTS
    }
    ch_names = [c[0] for c in CHANNELS]
    ch_w = [c[1] for c in CHANNELS]

    orders: list[tuple] = []
    lines: list[tuple] = []
    oid = lid = 0
    day = START
    base_rate = 0.042  # müşteri başına günlük sipariş olasılığı
    while day <= end:
        demand = _demand_index(day) * (0.55 if day.weekday() == 6 else 1.0)
        pidx = _price_index(day)
        for c in customers:
            if c[5] > day.isoformat():
                continue
            seg = c[4]
            if rng.random() > base_rate * SEGMENTS[seg][1] * demand:
                continue
            oid += 1
            # web kanalı zamanla büyüyor
            web_boost = 1 + 0.5 * ((day - START).days / 1000)
            w = [ch_w[0], ch_w[1] * web_boost, ch_w[2] * web_boost]
            channel = rng.choices(ch_names, w)[0]
            status = "iptal" if rng.random() < (0.055 if channel == "Pazaryeri" else 0.03) else "tamamlandı"
            orders.append((oid, c[0], day.isoformat(), channel, status))
            n_lines = max(1, min(6, int(rng.gauss(2.2, 1.1))))
            picked = set()
            for _ in range(n_lines):
                pi = rng.choices(range(len(PRODUCTS)), weights_by_seg[seg])[0]
                if pi in picked:
                    continue
                picked.add(pi)
                name, cat, price, cost_ratio, _w = PRODUCTS[pi]
                if cat == "Ekipman":
                    qty = 1 if rng.random() < 0.85 else 2
                else:
                    qty = max(1, int(rng.expovariate(1 / (4.5 * SEGMENTS[seg][2]))) + 1)
                    if cat == "Çekirdek Kahve":
                        qty = max(1, int(qty * 0.8))
                unit = round(price * pidx * rng.uniform(0.97, 1.03), 2)
                disc = 0.0
                if qty >= 10 or channel == "Saha Satış" and seg in ("Otel", "Market") and rng.random() < 0.5:
                    disc = rng.choice([0.05, 0.1, 0.1, 0.15])
                elif channel == "Pazaryeri" and rng.random() < 0.3:
                    disc = 0.05
                lid += 1
                lines.append((lid, oid, pi + 1, qty, unit, disc, round(unit * cost_ratio * rng.uniform(0.96, 1.04), 2)))
        day += timedelta(days=1)

    con.executemany("INSERT INTO siparisler VALUES (?,?,?,?,?)", orders)
    con.executemany("INSERT INTO siparis_kalemleri VALUES (?,?,?,?,?,?,?)", lines)
    con.commit()
    con.close()
    path.unlink(missing_ok=True)
    tmp.replace(path)
    return path


if __name__ == "__main__":
    import sys

    out = build(sys.argv[1] if len(sys.argv) > 1 else "demo.sqlite")
    con = sqlite3.connect(out)
    for t in ("musteriler", "urunler", "siparisler", "siparis_kalemleri"):
        print(t, con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
