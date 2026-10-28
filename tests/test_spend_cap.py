"""Per-IP rate limits and the global daily token cap on the public API."""

import time

import pytest
from fastapi.testclient import TestClient

from backend import budget
from backend.config import settings
from backend.limiter import limiter
from backend.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean():
    limiter.reset()
    budget.reset()
    yield
    limiter.reset()
    budget.reset()


def _upload():
    return client.post(
        "/api/knowledge/upload",
        data={"workspace_id": "w-cap"},
        files={"file": ("a.txt", b"hello world", "text/plain")},
    )


def test_upload_rate_limited(monkeypatch) -> None:
    monkeypatch.setattr(settings, "rate_upload_per_min", 2)
    assert _upload().status_code != 429
    assert _upload().status_code != 429
    r = _upload()
    assert r.status_code == 429
    assert "daily_cap_reached" not in r.text


def test_chat_rate_limited(monkeypatch) -> None:
    from fastapi import HTTPException

    from backend.api import routes

    def no_models(_m):  # stop before any SSE stream starts
        raise HTTPException(503, "no models")

    monkeypatch.setattr(routes, "_require_models", no_models)
    monkeypatch.setattr(settings, "rate_chat_per_min", 2)
    body = {"message": "hi there", "models": ["ollama:x"]}
    for _ in range(2):
        assert client.post("/api/chat", json=body).status_code != 429
    r = client.post("/api/chat", json=body)
    assert r.status_code == 429
    assert "daily_cap_reached" not in r.text


def test_cap_blocks_after_exceeding(monkeypatch) -> None:
    monkeypatch.setattr(settings, "daily_token_cap", 1000)
    budget.record(600)
    budget.ensure_available()  # under the cap: fine
    budget.record(500)
    with pytest.raises(budget.DailyCapReached):
        budget.ensure_available()
    r = client.post("/api/chat", json={"message": "hi there", "models": ["ollama:x"]})
    assert r.status_code == 429
    assert r.json()["error"] == "daily_cap_reached"
    r = client.post("/api/compile", json={"goal": "summarise the quarterly report"})
    assert r.status_code == 429
    assert r.json()["error"] == "daily_cap_reached"


def test_cap_zero_is_unlimited(monkeypatch) -> None:
    monkeypatch.setattr(settings, "daily_token_cap", 0)
    budget.record(10**9)
    budget.ensure_available()
    u = client.get("/api/usage").json()
    assert u["tokens"] == 10**9
    assert u["cap_reached"] is False
    assert u["remaining"] is None


def test_usage_endpoint_reports_tokens_and_cost(monkeypatch) -> None:
    monkeypatch.setattr(settings, "daily_token_cap", 5000)
    budget.record(1200, 0.0034)
    u = client.get("/api/usage").json()
    assert u["tokens"] == 1200
    assert u["cost_usd"] == pytest.approx(0.0034)
    assert u["remaining"] == 3800
    assert u["cap_reached"] is False


def test_usage_survives_process_restart(monkeypatch) -> None:
    monkeypatch.setattr(settings, "daily_token_cap", 5000)
    budget.record(1200, 0.5)
    budget.flush()
    budget.forget()  # a new process: empty memory, same database
    u = budget.usage()
    assert u["tokens"] == 1200
    assert u["cost_usd"] == pytest.approx(0.5)


def test_cap_blocks_across_restart(monkeypatch) -> None:
    monkeypatch.setattr(settings, "daily_token_cap", 1000)
    budget.record(1500)
    budget.flush()
    budget.forget()
    with pytest.raises(budget.DailyCapReached):
        budget.ensure_available()
    assert client.get("/api/usage").json()["cap_reached"] is True


def test_record_is_batched_then_flushed(monkeypatch) -> None:
    monkeypatch.setattr(budget, "FLUSH_SECONDS", 3600.0)
    monkeypatch.setattr(budget, "_last_flush", time.monotonic())  # inside the window
    budget.record(10)
    budget.record(10)
    budget.forget()
    assert budget.usage()["tokens"] == 0  # nothing was flushed yet
    budget.record(10)
    budget.flush()
    budget.forget()
    assert budget.usage()["tokens"] == 10


def test_picks_up_other_instances_usage(monkeypatch) -> None:
    monkeypatch.setattr(settings, "daily_token_cap", 1000)
    budget.usage()  # loads baseline 0
    other = budget._connect()[0]  # a second instance writing to the shared db
    other.execute("INSERT INTO usage_daily (day, tokens, cost_usd) VALUES (?, 900, 0) "
                  "ON CONFLICT(day) DO UPDATE SET tokens = 900", [budget._day.isoformat()])
    other.commit()
    other.close()
    monkeypatch.setattr(budget, "REFRESH_SECONDS", 0.0)
    assert budget.usage()["tokens"] == 900


def test_db_failure_falls_back_to_memory(monkeypatch) -> None:
    def boom():
        raise OSError("db down")

    monkeypatch.setattr(budget, "_connect", boom)
    monkeypatch.setattr(settings, "daily_token_cap", 1000)
    budget.record(600)
    budget.flush()  # must not raise
    budget.ensure_available()
    budget.record(500)
    with pytest.raises(budget.DailyCapReached):
        budget.ensure_available()
    assert budget.usage()["tokens"] == 1100
