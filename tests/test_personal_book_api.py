from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main
from app import personal_book as book
from app.config import get_settings


def _auth():
    settings = get_settings()
    if settings.basic_auth_user:
        return (settings.basic_auth_user, settings.basic_auth_password)
    return None


def _wire_personal_paths(tmp_path: Path, monkeypatch, *, bounce: dict, price: float = 12.0) -> None:
    bounce_path = tmp_path / "bounce.json"
    bounce_path.write_text(json.dumps(bounce), encoding="utf-8")
    latest = tmp_path / "LATEST.json"
    latest.write_text(json.dumps({"reference_price": price}), encoding="utf-8")
    monkeypatch.setattr(main, "BOUNCE_DAILY_LATEST_PATH", bounce_path)
    monkeypatch.setattr(main, "PERSONAL_LATEST_PATH", latest)
    monkeypatch.setattr(main, "PERSONAL_STATE_PATH", tmp_path / "STATE.json")
    monkeypatch.setattr(book, "BOUNCE_LATEST", bounce_path)
    monkeypatch.setattr(book, "DEFAULT_OUTPUT_ROOT", tmp_path)
    monkeypatch.setattr(book, "DEFAULT_LATEST_PATH", latest)
    monkeypatch.setattr(book, "DEFAULT_STATE_PATH", tmp_path / "STATE.json")


def test_personal_api_get_and_skip_then_fill(tmp_path, monkeypatch) -> None:
    _wire_personal_paths(
        tmp_path,
        monkeypatch,
        bounce={
            "status": "buy",
            "as_of": "2026-08-21",
            "data_through": "2026-08-21",
            "pick": {
                "symbol": "600621",
                "name": "华鑫股份",
                "entry_date": "2026-08-22",
                "exit_date": "2026-08-28",
            },
            "holding": None,
            "automatic_trading_allowed": False,
            "effective_strategy": False,
        },
    )
    client = TestClient(main.create_app())
    auth = _auth()
    got = client.get("/api/personal/book", auth=auth)
    assert got.status_code == 200
    body = got.json()
    assert body["auto_order"] is False
    assert body["effective_strategy"] is False
    assert body["action"] in {
        "cash",
        "open",
        "hold",
        "close",
        "halted",
        "mismatch",
        "untradeable",
        "missing",
    }
    cash0 = body["cash"]
    skipped = client.post(
        "/api/personal/fill",
        auth=auth,
        json={"kind": "skip", "symbol": "600621", "side": "buy", "as_of": "2026-08-21"},
    )
    assert skipped.status_code == 200
    assert skipped.json()["cash"] == cash0
    filled = client.post(
        "/api/personal/fill",
        auth=auth,
        json={
            "kind": "fill",
            "symbol": "600621",
            "name": "华鑫股份",
            "side": "buy",
            "price": 12.05,
            "shares": 8300,
            "commission": 25.0,
            "as_of": "2026-08-22",
        },
    )
    assert filled.status_code == 200
    assert filled.json()["cash"] < cash0
    delta = cash0 - filled.json()["cash"]
    assert abs(delta - (8300 * 12.05 + 25.0 + 8300 * 12.05 * 0.00001)) < 0.02
