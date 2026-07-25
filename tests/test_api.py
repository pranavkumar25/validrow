from fastapi.testclient import TestClient

from eve.api.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"


def test_verify_disposable():
    r = client.post("/v1/verify", json={"email": "x@mailinator.com", "check_dns": False})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "disposable"
    assert body["is_disposable"] is True


def test_verify_invalid_syntax():
    r = client.post("/v1/verify", json={"email": "nope", "check_dns": False})
    assert r.json()["status"] == "invalid"


def test_verify_role_with_suggestion_shape():
    r = client.post("/v1/verify", json={"email": "sales@acme.io", "check_dns": False})
    body = r.json()
    assert body["status"] == "risky"
    assert body["is_role"] is True
    assert "checks" in body
