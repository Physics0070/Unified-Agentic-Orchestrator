"""Single-service hosting: SPA static mount and the relaxed prod settings."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.config import Settings
from backend.main import mount_frontend


@pytest.fixture
def spa_client(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>APP</html>")
    (tmp_path / "assets" / "a.js").write_text("console.log(1)")
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    assert mount_frontend(app, tmp_path)
    return TestClient(app)


def test_root_serves_index(spa_client):
    assert "APP" in spa_client.get("/").text


def test_static_asset_served(spa_client):
    assert spa_client.get("/assets/a.js").text == "console.log(1)"


def test_unknown_path_falls_back_to_index(spa_client):
    r = spa_client.get("/some/client/route")
    assert r.status_code == 200 and "APP" in r.text


def test_api_routes_not_shadowed_and_api_miss_is_404_json(spa_client):
    assert spa_client.get("/api/ping").json() == {"ok": True}
    r = spa_client.get("/api/nope")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_no_dist_means_api_only(tmp_path):
    assert mount_frontend(FastAPI(), tmp_path / "missing") is False


def test_prod_needs_only_secret_key(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-only-value")
    s = Settings(app_env="prod", turso_database_url="", turso_auth_token="")
    assert s.is_prod


def test_prod_still_requires_secret_key():
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(app_env="prod", secret_key="")


def test_run_timeout_depends_on_platform(monkeypatch):
    monkeypatch.delenv("RUN_TIMEOUT_S", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert Settings().run_timeout_s == 270
    monkeypatch.delenv("VERCEL")
    assert Settings().run_timeout_s == 1800
