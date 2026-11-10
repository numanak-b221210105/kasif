import json
import sqlite3

from conftest import TODAY, FakeLLM, cfg

from kasif.engine import Engine


def test_rules_answer_end_to_end(engine):
    a = engine.ask("Geçen çeyrekte bölgelere göre ciro")
    assert a.ok and a.mode == "rules"
    assert [c.name for c in a.columns] == ["Bölge", "Ciro"]
    assert a.chart["type"] == "bar"
    assert a.rows[0][0] == "Marmara"
    assert a.summary and "Marmara" in a.summary[0]
    assert any(p.kind == "time" for p in a.plan)
    assert a.followups and all(isinstance(f, str) for f in a.followups)


def test_trend_ignores_unfinished_month(engine):
    a = engine.ask("Son 12 ayda aylık ciro")
    assert a.chart["type"] == "line"
    assert "2026-11" in a.summary[-1] and "tamamlanmadı" in a.summary[-1]


def test_refuses_instead_of_guessing(engine):
    a = engine.ask("Personel maaşları ne kadar?")
    assert not a.ok and a.error_code == "not_understood" and a.suggestions
    a = engine.ask("Lale Kahvecisi ile Mars Kahvecisi ne kadar alışveriş yaptı?")
    assert not a.ok and a.can_force and "mars" in a.unknown
    forced = engine.ask("Lale Kahvecisi ile Mars Kahvecisi ne kadar alışveriş yaptı?", mode="rules")
    assert forced.ok and forced.warnings


def test_llm_self_correction_and_guard(shared_dir):
    bad = json.dumps({"sql": "SELECT kanal, SUM(tutar) FROM siparisler GROUP BY 1", "explanation": "x"})
    good = json.dumps(
        {
            "sql": 'SELECT kanal AS "Kanal", COUNT(*) AS "Sipariş" FROM siparisler GROUP BY 1 ORDER BY 2 DESC',
            "explanation": "Kanallara göre sipariş sayısı.",
        }
    )
    fake = FakeLLM([bad, good])
    e = Engine(cfg(shared_dir), llm_client=fake, today=TODAY)
    a = e.ask("hangi kanaldan kaç sipariş geldi acaba bilmiyorum", mode="llm")
    assert a.ok and a.mode == "llm" and len(a.attempts) == 1 and "tutar" in a.attempts[0].error
    assert "Bilinmeyen sütun" in fake.calls[1][-1]["content"]
    assert a.explanation == "Kanallara göre sipariş sayısı."
    assert a.chart["type"] == "bar"
    # veri değiştirmeye çalışan bir yanıt düzeltme denemesine bile girmeden reddedilir
    evil = FakeLLM(['{"sql": "DELETE FROM siparisler"}'])
    e = Engine(cfg(shared_dir), llm_client=evil, today=TODAY)
    a = e.ask("tüm siparişleri sil", mode="llm")
    assert not a.ok and a.error_code == "write" and len(evil.calls) == 1


def test_llm_prompt_contains_schema_and_rules(engine):
    p = engine.llm_prompt(engine.layer(), "tr")
    assert "CREATE TABLE siparisler" in p and "durum = 'tamamlandı'" in p and "2026-11-10" in p
    assert "Saha Satış" in p  # örnek değerler


def test_run_sql_is_read_only(engine):
    a = engine.run_sql("SELECT segment, COUNT(*) AS n FROM musteriler GROUP BY 1 ORDER BY 2 DESC")
    assert a.ok and a.mode == "sql" and a.rows
    a = engine.run_sql("UPDATE musteriler SET segment = 'X'")
    assert not a.ok and a.error_code == "write"
    with sqlite3.connect(engine.path()) as con:
        assert con.execute("SELECT COUNT(*) FROM musteriler WHERE segment = 'X'").fetchone()[0] == 0


def test_timeout(shared_dir):
    from dataclasses import replace

    e = Engine(replace(cfg(shared_dir), timeout_s=0.2), today=TODAY)
    a = e.run_sql("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT COUNT(*) FROM r")
    assert not a.ok and "durduruldu" in a.error


def test_history(engine):
    engine.history.clear()
    engine.ask("Bu yıl toplam ciro ne kadar?")
    engine.ask("Personel maaşları ne kadar?")
    h = engine.history.list()
    assert len(h) == 2 and h[0]["ok"] is False and h[1]["mode"] == "rules"


def test_csv_upload_with_turkish_formats(tmp_path):
    e = Engine(cfg(tmp_path), today=TODAY)
    csv = (
        "Tarih;Şube;Ürün Grubu;Tutar\n"
        "01.10.2026;Kadıköy;İçecek;1.250,50\n"
        "02.10.2026;Beşiktaş;Yiyecek;980,00\n"
        "15.10.2026;Kadıköy;Yiyecek;2.000,00\n"
        "03.11.2026;Beşiktaş;İçecek;500,25\n"
    ).encode("cp1254")
    ds = e.registry.add("Şube Satışları.csv", csv)
    assert e.registry.active.id == ds.id
    tables = e.tables()
    assert list(tables) == ["sube_satislari"]
    assert tables["sube_satislari"] == {"tarih": "TEXT", "sube": "TEXT", "urun_grubu": "TEXT", "tutar": "REAL"}
    a = e.ask("Şube bazında toplam tutar")
    assert a.ok, a.error
    assert dict((r[0], r[1]) for r in a.rows) == {"Kadıköy": 3250.5, "Beşiktaş": 1480.25}
    a = e.ask("Geçen ay toplam tutar")
    assert a.ok and a.rows[0][0] == 4230.5
    assert e.examples()


def test_sqlite_upload_and_delete(tmp_path, engine):
    e = Engine(cfg(tmp_path), today=TODAY)
    data = engine.path().read_bytes()
    ds = e.registry.add("kopya.db", data)
    assert e.layer() is not None and "siparis_kalemleri" in e.tables()
    e.registry.delete(ds.id)
    assert e.registry.active.id == "demo"
    import pytest

    from kasif.datasets import DatasetError

    with pytest.raises(DatasetError):
        e.registry.delete("demo")
    with pytest.raises(DatasetError):
        e.registry.add("bozuk.db", b"not a database")


def test_sample_csv_answers_everyday_questions(tmp_path):
    from pathlib import Path

    e = Engine(cfg(tmp_path), today=TODAY)
    data = (Path(__file__).resolve().parent.parent / "samples" / "sube-satislari.csv").read_bytes()
    e.registry.add("sube-satislari.csv", data)
    for q, cols in [
        ("Şubelere göre ciro", ["Şube", "Toplam tutar"]),
        ("En çok satan 3 ürün", ["Ürün", "Toplam adet"]),
        ("Ekim ayında Kadıköy şubesinin toplam tutarı", ["Toplam tutar"]),
        ("Şehirlere göre ortalama tutar", ["Şehir", "Ortalama tutar"]),
        ("Ödeme tipi dağılımı", ["Ödeme Tipi", "Toplam tutar", "Pay (%)"]),
    ]:
        a = e.ask(q)
        assert a.ok, (q, a.error)
        assert [c.name for c in a.columns] == cols, q
    assert e.ask("En çok satan 3 ürün").row_count == 3
