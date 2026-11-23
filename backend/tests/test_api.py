from fastapi.testclient import TestClient

from app.main import create_app


def test_health_and_cities(real_db):
    with TestClient(create_app(real_db)) as client:
        res = client.get("/health")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ok"
        assert body["network"]["cities"] == 711 and body["network"]["hubs"] == 40
        assert body["network"]["hub_links"] == 151 and body["network"]["unserved"] == 10
        assert body["network"]["fingerprint"].startswith("sha256:")

        res = client.get("/api/cities")
        assert res.status_code == 200
        cities = res.json()
        assert cities["count"] == 711 == len(cities["cities"])
        by_name = {c["name"]: c for c in cities["cities"]}
        assert by_name["Shanghai"]["is_hub"] is True and by_name["Shanghai"]["served"] is True
        assert by_name["Abu Dhabi"]["served"] is False
        assert by_name["Guangzhou"]["connected_hubs"] == ["Frankfurt", "Johannesburg"]
