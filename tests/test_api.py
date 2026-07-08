from fastapi.testclient import TestClient
from dataclasses import replace

import app.main as main
from app.storage import append_jsonl, write_json
from tests.test_signals import sample_frame


def test_analyze_endpoint_with_fake_provider(monkeypatch):
    def fake_history(symbol, market, lookback_days=360, adjust="qfq"):
        return sample_frame("up"), "test-provider"

    monkeypatch.setattr(main.DATA_PROVIDER, "history", fake_history)
    client = TestClient(main.create_app())

    response = client.post(
        "/api/analyze",
        json={"symbol": "510300", "market": "etf", "lookback_days": 360, "adjust": "qfq"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["symbol"] == "510300"
    assert data["source"] == "test-provider"
    assert data["candles"]


def test_recommendation_performance_endpoint(monkeypatch, tmp_path):
    history_path = tmp_path / "recommendations_history.jsonl"
    append_jsonl(
        str(history_path),
        {
            "generated_at": "2025-01-10T09:00:00+08:00",
            "trade_date": "2025-01-10",
            "items": [{"symbol": "600519", "market": "a", "as_of": "2025-01-10", "action": "BUY"}],
        },
    )

    def fake_history(symbol, market, lookback_days=900, adjust="qfq"):
        return sample_frame("up"), "test-provider"

    monkeypatch.setattr(main, "SETTINGS", replace(main.SETTINGS, recommendation_history_path=str(history_path)))
    monkeypatch.setattr(main.DATA_PROVIDER, "history", fake_history)
    client = TestClient(main.create_app())

    response = client.get("/api/performance/recommendations")

    assert response.status_code == 200
    data = response.json()
    assert data["summary"]["matured_10d_count"] == 1
    assert data["items"][0]["entry_date"] > "2025-01-10"


def test_akshare_status_endpoint_reads_status_file(monkeypatch, tmp_path):
    status_path = tmp_path / "akshare_status.json"
    write_json(
        str(status_path),
        {
            "updated_at": "2026-07-06T09:32:00+08:00",
            "endpoints": {"stock_zh_a_hist": {"status": "recovered"}},
            "events": [],
        },
    )

    monkeypatch.setattr(main, "SETTINGS", replace(main.SETTINGS, akshare_status_path=str(status_path)))
    client = TestClient(main.create_app())

    response = client.get("/api/data/akshare-status")

    assert response.status_code == 200
    assert response.json()["endpoints"]["stock_zh_a_hist"]["status"] == "recovered"


def test_holdings_endpoint_tracks_default_positions(monkeypatch, tmp_path):
    def fake_history(symbol, market, lookback_days=360, adjust="qfq"):
        return sample_frame("up"), "test-provider"

    class FakeL1:
        def quotes(self, symbols):
            return {
                "enabled": True,
                "available": True,
                "quotes": {symbol: {"price": 120, "change_pct": 1.2} for symbol in symbols},
                "errors": [],
                "elapsed_seconds": 0.01,
            }

    monkeypatch.setattr(main, "SETTINGS", replace(main.SETTINGS, holdings_path=str(tmp_path / "holdings.json")))
    monkeypatch.setattr(main.DATA_PROVIDER, "history", fake_history)
    monkeypatch.setattr(main.RECOMMENDATIONS, "l1_quotes", FakeL1())
    client = TestClient(main.create_app())

    response = client.get("/api/holdings")

    assert response.status_code == 200
    data = response.json()
    assert [item["symbol"] for item in data["items"]] == ["159567", "520700"]
    assert data["l1_quote"]["quote_count"] == 2
