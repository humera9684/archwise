"""FastAPI endpoints."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "template")
    from advisor import service
    service.get_book(force=True)
    return TestClient(app)


def test_health_and_templates(client):
    assert client.get("/health").json()["status"] == "ok"
    t = client.get("/templates").json()
    assert {"rest_api", "ecommerce"} <= set(t)


def test_unknown_traffic_returns_scenarios(client):
    r = client.post("/evaluate", json={"app_type": "ecommerce", "cloud_preference": "aws"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "unknown" and set(body["report"]["tiers"]) == {"low", "medium", "high"}


def test_known_traffic_returns_recommendation(client):
    r = client.post("/evaluate", json={"traffic_known": True, "requests_per_day": 20000})
    assert r.status_code == 200 and r.json()["mode"] == "known"
    assert r.json()["recommendation"]["all_candidates"][0]["pricing_status"] == "approximate"


def test_invalid_input_rejected(client):
    assert client.post("/evaluate", json={"traffic_known": True}).status_code == 422
    assert client.post("/evaluate", json={"monthly_budget_usd": -5}).status_code == 422


def test_explain_round_trip(client):
    result = client.post("/evaluate", json={"app_type": "rest_api"}).json()
    e = client.post("/explain", json={"result": result})
    assert e.status_code == 200
    assert e.json()["unsupported_figures"] == [] and "Recommendation" in e.json()["text"]


def test_requirements_endpoint_and_assumptions(client):
    assert client.post("/evaluate/requirements", json={}).json()["mode"] == "requirements"
    a = client.post("/assumptions", json={"app_type": "ecommerce"}).json()
    assert any(x["field"] == "p95_latency_ms" and x["provenance"] == "derived" for x in a)


def test_pricing_status(client):
    s = client.get("/pricing/status").json()
    assert s["clouds"]["aws"]["kind"] == "approximate"


def test_config_for_web_ui(client):
    c = client.get("/config").json()
    assert {"rest_api", "ecommerce"} <= set(c["templates"])
    assert set(c["tiers"]) == {"low", "medium", "high"}
    assert "Startup MVP API" in c["presets"] and "template" in c["providers"]
    assert c["active_provider"]["is_template"] is True


def test_diagram_endpoint(client):
    result = client.post("/evaluate", json={"traffic_known": True, "requests_per_day": 20000}).json()
    arch = result["recommendation"]["all_candidates"][0]
    r = client.post("/diagram", json=arch)
    assert r.status_code == 200 and r.json()["mermaid"].startswith("flowchart")


def test_extract_intake_needs_llm(client):
    r = client.post("/extract-intake", json={"text": "an online store"})
    assert r.status_code == 422 and "LLM" in r.json()["detail"]


def test_cors_allows_dev_server(client):
    r = client.options("/config", headers={"Origin": "http://localhost:5173",
                                           "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_chat_needs_llm(client):
    r = client.post("/chat", json={"message": "we expand to Europe", "intake": {}})
    assert r.status_code == 422 and "LLM" in r.json()["detail"]
