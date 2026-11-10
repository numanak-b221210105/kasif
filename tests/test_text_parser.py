from datetime import date

import pytest

from kasif.parser import parse
from kasif.semantic import demo_layer, load_values
from kasif.text import ascii_fold, tok_match, tokens, tr_lower

T = date(2026, 11, 10)


def test_turkish_lowercase_and_tokens():
    assert tr_lower("IŞIK İZMİR") == "ışık izmir"
    assert tokens("İzmir'deki KAFELER, 2025'te") == ["izmir", "deki", "kafeler", "2025", "te"]
    assert ascii_fold("Sipariş Tarihi (TL)") == "siparis_tarihi_tl"


def test_suffix_tolerant_match():
    assert tok_match("kafelerin", "kafe")
    assert tok_match("bölgelere", "bölge")
    assert not tok_match("ilk", "il")
    assert not tok_match("egemen", "ege")


@pytest.fixture(scope="module")
def layer(request):
    import sqlite3

    from kasif import demo

    path = request.config.cache.mkdir("kasif_demo") / "demo.sqlite"
    demo.build(path, end=T)
    return load_values(demo_layer(), sqlite3.connect(path))


@pytest.mark.parametrize(
    "q,start,end",
    [
        ("bu yıl ciro", "2026-01-01", "2026-11-10"),
        ("geçen yıl ciro", "2025-01-01", "2025-12-31"),
        ("geçen ay ciro", "2026-10-01", "2026-10-31"),
        ("geçen çeyrek ciro", "2026-07-01", "2026-09-30"),
        ("son 3 ayda ciro", "2026-08-11", "2026-11-10"),
        ("Ekim 2025 ciro", "2025-10-01", "2025-10-31"),
        ("ekimde ciro", "2026-10-01", "2026-10-31"),
        ("2025 3. çeyrek ciro", "2025-07-01", "2025-09-30"),
        ("yılbaşından beri ciro", "2026-01-01", "2026-11-10"),
        ("revenue last month", "2026-10-01", "2026-10-31"),
    ],
)
def test_time_expressions(layer, q, start, end):
    p = parse(q, layer, T)
    assert (p.time.start.isoformat(), p.time.end.isoformat()) == (start, end)


def test_same_period_comparison(layer):
    p = parse("Bu yıl geçen yıla göre ciro", layer, T)
    assert p.grain == "yil" and p.time.same_period


def test_top_n_and_metric(layer):
    p = parse("En çok satan 5 ürün hangisi?", layer, T)
    assert [m.key for m in p.metrics] == ["adet"]
    assert [g.key for g in p.groups] == ["urun"]
    assert (p.order, p.limit) == ("desc", 5)


def test_values_negation_and_qualifiers(layer):
    p = parse("İstanbul hariç şehirlere göre müşteri sayısı", layer, T)
    assert p.filters[0].values == ["İstanbul"] and p.filters[0].negate
    p = parse("Web kanalında bu yıl en çok ciro yapan kategori", layer, T)
    assert [g.key for g in p.groups] == ["kategori"]
    assert p.filters[0].values == ["Web"]
    p = parse("Türk kahvesi satışları", layer, T)
    assert sorted(p.filters[0].values) == ["Türk Kahvesi 1 kg", "Türk Kahvesi 250 g"]


def test_customer_name_is_not_a_month(layer):
    p = parse("Martı Bakkal cirosu", layer, T)
    assert p.time is None and p.filters[0].values == ["Martı Bakkal"]


def test_share_of_a_single_value(layer):
    p = parse("Web kanalının payı", layer, T)
    assert p.share and [g.key for g in p.groups] == ["kanal"] and p.highlight == ["Web"]


def test_unknown_words_are_reported(layer):
    p = parse("Personel maaşları ne kadar?", layer, T)
    assert not p.understood and "personel" in p.unknown


@pytest.mark.parametrize(
    "q,grain",
    [
        ("her ay ciro", "ay"),
        ("her yıl sipariş sayısı", "yil"),
        ("çeyreklik ciro", "ceyrek"),
        ("haftalık sipariş", "hafta"),
        ("aylara göre ciro", "ay"),
    ],
)
def test_grains(layer, q, grain):
    assert parse(q, layer, T).grain == grain
