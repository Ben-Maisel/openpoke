"""Smoke tests: the app boots and tests are isolated from real data."""

import os
from pathlib import Path

from fastapi.testclient import TestClient

from server import config
from server.app import app

REPO_DEFAULT_DATA_DIR = Path(config.__file__).resolve().parent / "data"


def test_tests_use_isolated_data_dir():
    assert config.DATA_DIR == Path(os.environ["OPENPOKE_DATA_DIR"]).resolve()
    assert config.DATA_DIR != REPO_DEFAULT_DATA_DIR


def test_resolve_data_dir_defaults_to_server_data(monkeypatch):
    monkeypatch.delenv("OPENPOKE_DATA_DIR", raising=False)
    assert config._resolve_data_dir() == REPO_DEFAULT_DATA_DIR


def test_resolve_data_dir_uses_override(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENPOKE_DATA_DIR", str(tmp_path))
    assert config._resolve_data_dir() == tmp_path.resolve()


def test_resolve_data_dir_ignores_blank_override(monkeypatch):
    monkeypatch.setenv("OPENPOKE_DATA_DIR", "   ")
    assert config._resolve_data_dir() == REPO_DEFAULT_DATA_DIR


def test_app_boots_and_reports_healthy():
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_unknown_route_returns_404():
    with TestClient(app) as client:
        response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
