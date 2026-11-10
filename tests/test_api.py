import pytest
from fastapi.testclient import TestClient

from kasif import api


@pytest.fixture
def client(engine, monkeypatch):
    monkeypatch.setattr(api, "engine", lambda: engine)
    return TestClient(api.app)


def test_status_and_ui(client):
    assert client.get("/health").json()["status"] == "ok"
    s = client.get("/status").json()
    assert s["dataset"]["id"] == "demo" and s["rules"] is True and s["llm"]["reachable"] is False
    assert "<title>" in client.get("/").text


def test_ask_and_sql(client):
    r = client.post("/ask", json={"question": "En çok satan 5 ürün hangisi?"}).json()
    assert r["ok"] and len(r["rows"]) == 5 and r["columns"][1]["fmt"] == "int"
    r = client.post("/ask", json={"question": "Top 3 customers by revenue last year", "lang": "en"}).json()
    assert r["ok"] and r["columns"][0]["name"] == "Customer"
    r = client.post("/sql", json={"sql": "DROP TABLE urunler"}).json()
    assert not r["ok"] and r["error_code"] == "write"


def test_schema_examples_history(client):
    s = client.get("/schema").json()
    names = {t["name"] for t in s["tables"]}
    assert names == {"musteriler", "urunler", "siparisler", "siparis_kalemleri"}
    sip = next(t for t in s["tables"] if t["name"] == "siparisler")
    assert next(c for c in sip["columns"] if c["name"] == "musteri_id")["fk"] == {"table": "musteriler", "column": "id"}
    assert s["layer"]["metrics"][0]["key"] == "ciro"
    assert client.get("/examples?lang=en").json()
    client.post("/ask", json={"question": "Bu yıl ciro"})
    assert client.get("/history").json()[0]["question"] == "Bu yıl ciro"


def test_dataset_upload_flow(client, tmp_path):
    r = client.post("/datasets", files={"file": ("notlar.pdf", b"%PDF", "application/pdf")})
    assert r.status_code == 400
    r = client.post("/datasets", files={"file": ("puan.csv", b"ogrenci,ders,not\nA,Mat,80\nB,Mat,90\nA,Fiz,70\n")})
    assert r.status_code == 200 and r.json()["active"]
    ds_id = r.json()["id"]
    a = client.post("/ask", json={"question": "Ders bazında ortalama not"}).json()
    assert a["ok"] and {r[0]: r[1] for r in a["rows"]} == {"Mat": 85.0, "Fiz": 70.0}
    assert client.post("/datasets/demo/activate").json()["active"]
    assert client.delete(f"/datasets/{ds_id}").status_code == 204


def test_live_preview(client):
    r = client.post("/parse", json={"question": "Geçen çeyrekte bölgelere göre ciro"}).json()
    assert r["understood"] and r["confident"] and {p["kind"] for p in r["plan"]} >= {"metric", "dim", "time"}
    r = client.post("/parse", json={"question": "Personel maaşları"}).json()
    assert not r["understood"] and "personel" in r["unknown"]
    assert client.post("/parse", json={"question": "select * from urunler"}).json()["sql"]
    a = client.post("/ask", json={"question": "Bu yıl ciro"}).json()
    assert "limit_added" in a["checks"] or "limit" in a["checks"]


def test_live_parse_preview(client):
    q = "Geçen çeyrekte İzmir'deki kafelerin maaşları"
    r = client.post("/parse", json={"question": q}).json()
    words = {q[t["s"] : t["e"]]: t["k"] for t in r["tokens"]}
    assert words["Geçen"] == "time" and words["İzmir"] == "filter" and words["kafelerin"] == "filter"
    assert words["maaşları"] == "unknown" and r["understood"] and not r["confident"]
    r = client.post("/parse", json={"question": "En çok satan 5 ürün"}).json()
    words = {"En çok satan 5 ürün"[t["s"] : t["e"]]: t["k"] for t in r["tokens"]}
    assert words == {"En": "order", "çok": "order", "satan": "metric", "5": "limit", "ürün": "dim"}
    assert r["confident"]
