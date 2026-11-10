import pytest

from kasif.guard import GuardError, check

SCHEMA = {
    "musteriler": {"id": "INTEGER", "ad": "TEXT", "sehir": "TEXT"},
    "siparisler": {"id": "INTEGER", "musteri_id": "INTEGER", "tarih": "TEXT"},
}


@pytest.mark.parametrize(
    "sql,code",
    [
        ("DELETE FROM musteriler", "write"),
        ("UPDATE musteriler SET ad = 'x'", "write"),
        ("DROP TABLE musteriler", "write"),
        ("select 1; drop table musteriler", "multi"),
        ("ATTACH 'x.db' AS y", "write"),
        ("PRAGMA table_info(musteriler)", "write"),
        ("select load_extension('evil')", "func"),
        ("select * from sqlite_master", "table"),
        ("select * from kullanicilar", "table"),
        ("select maas from musteriler", "column"),
        ("", "empty"),
    ],
)
def test_rejects(sql, code):
    with pytest.raises(GuardError) as e:
        check(sql, SCHEMA)
    assert e.value.code == code


def test_allows_reads_and_adds_limit():
    out = check("select sehir, count(*) n from musteriler group by sehir", SCHEMA, max_rows=100)
    assert "LIMIT 101" in out.sql and out.tables == ["musteriler"]
    out = check("WITH x AS (SELECT ad FROM musteriler) SELECT * FROM x", SCHEMA)
    assert out.tables == ["musteriler"]
    out = check("select m.ad from musteriler m join siparisler s on s.musteri_id = m.id limit 5", SCHEMA)
    assert "LIMIT 5" in out.sql
