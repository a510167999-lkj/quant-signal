from fastapi.testclient import TestClient
from dataclasses import replace

import app.main as main
from app.recommendation_contract import (
    build_publication_ledger_record,
    recommendation_snapshot_sha256,
)
from app.storage import append_jsonl, write_json
from tests.test_signals import sample_frame


def recommendation_item(symbol="600519"):
    return {
        "symbol": symbol,
        "market": "a",
        "entry_zone": {"low": 99.0, "high": 101.0},
        "levels": {
            "support": 98.0,
            "resistance": 105.0,
            "stop_loss": 96.0,
            "take_profit": 108.0,
        },
        "trade_plans": {"short_term": {"horizon": "3-10 trading days"}},
        "operation_advice": {
            "action": "buy",
            "entry_zone": {"low": 99.0, "high": 101.0},
            "stop_loss": 96.0,
            "take_profit": 108.0,
            "holding_period": "3-10 trading days",
            "invalidation": "close_below_stop",
            "trigger_conditions": ["entry_zone_and_signal_confirmed"],
            "take_profit_or_reduce": {
                "condition": "price_gte_take_profit",
                "trigger_price": 108.0,
                "action": "reduce_or_take_profit",
            },
            "invalidation_conditions": ["close_below_stop"],
            "expected_holding_period": "3-10 trading days",
        },
        "auto_order": False,
        "risks": ["止损纪律"],
        "rank_score": 9.0,
    }


def recommendation_snapshot(items):
    payload = {
        "generated_at": "2026-07-13T15:02:00+08:00",
        "trade_date": "2026-07-13",
        "signal_date": "2026-07-13",
        "target_trade_date": "2026-07-13",
        "items": items,
        "errors": [],
        "recommendation_status": "live_proven",
        "evidence_scope": "live_proof",
        "live_proof": True,
        "auto_order": False,
        "publication_gate": {"status": "allowed", "reason": None},
        "profile_gate": {
            "live_proof": True,
            "evidence_receipt_sha256": "b" * 64,
        },
        "current_pool_audit_sha256": "a" * 64,
        "daily_publication_cap": {
            "target_trade_date": "2026-07-13",
            "limit": 3,
            "prior_symbols": [],
            "prior_count": 0,
            "remaining_before_run": 3,
            "published_this_run": len(items),
            "rejected_this_run": 0,
            "valid": True,
        },
        "summary": {},
    }
    if items:
        payload["publication_receipt"] = build_publication_ledger_record(
            sequence=1,
            generated_at=payload["generated_at"],
            target_trade_date=payload["target_trade_date"],
            prior_symbols=set(),
            published_symbols={
                str(item.get("symbol") or "")
                for item in items
                if isinstance(item, dict) and item.get("symbol")
            },
            previous_record_hash=None,
            seeded_from_legacy_history=False,
            snapshot_sha256=recommendation_snapshot_sha256(payload),
            current_pool_audit_sha256="a" * 64,
            profile_evidence_receipt_sha256="b" * 64,
        )
    return payload


def test_analyze_endpoint_with_fake_provider(monkeypatch):
    def fake_history(symbol, market, lookback_days=360, adjust="qfq"):
        return sample_frame("up"), "test-provider"

    monkeypatch.setattr(main, "_require_jiaoch_market_runtime", lambda: None)
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


def test_recommendations_api_preserves_valid_operation_contract(
    monkeypatch,
):
    monkeypatch.setattr(
        main.RECOMMENDATIONS,
        "latest",
        lambda: recommendation_snapshot([recommendation_item()]),
    )
    client = TestClient(main.create_app())

    response = client.get("/api/recommendations/latest")

    assert response.status_code == 200
    assert response.json()["items"][0]["rank_score"] == 9.0


def test_recommendations_api_rejects_more_than_three_items(monkeypatch):
    monkeypatch.setattr(
        main.RECOMMENDATIONS,
        "latest",
        lambda: recommendation_snapshot(
            [recommendation_item(f"60051{index}") for index in range(4)]
        ),
    )
    client = TestClient(main.create_app(), raise_server_exceptions=False)

    response = client.get("/api/recommendations/latest")

    assert response.status_code == 500


def test_recommendations_api_rejects_incomplete_operation_contract(
    monkeypatch,
):
    item = recommendation_item()
    item["operation_advice"].pop("trigger_conditions")
    monkeypatch.setattr(
        main.RECOMMENDATIONS,
        "latest",
        lambda: recommendation_snapshot([item]),
    )
    client = TestClient(main.create_app(), raise_server_exceptions=False)

    response = client.get("/api/recommendations/latest")

    assert response.status_code == 500


def test_bounce_daily_api_is_research_ledger_not_an_order(monkeypatch, tmp_path):
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(main, "BOUNCE_DAILY_LATEST_PATH", missing)
    client = TestClient(main.create_app())
    empty = client.get("/api/research/bounce-daily")
    assert empty.status_code == 200
    assert empty.json()["available"] is False
    assert empty.json()["auto_order"] is False
    assert empty.json()["effective_strategy"] is False

    report = tmp_path / "LATEST.json"
    write_json(
        str(report),
        {
            "status": "buy",
            "as_of": "2026-08-14",
            "look_date": "2026-08-14",
            "data_through": "2026-08-14",
            "empty_reason": None,
            "pick": {
                "symbol": "000001",
                "name": "平安银行",
                "signal_date": "2026-08-14",
                "entry_date": "2026-08-17",
                "stock_return_20d_pct": -2.0,
            },
            "holding": None,
            "ledger": [],
            "qualified_signal_count": 1,
            "selected_trade_count": 1,
        },
    )
    monkeypatch.setattr(main, "BOUNCE_DAILY_LATEST_PATH", report)
    filled = client.get("/api/research/bounce-daily")
    assert filled.status_code == 200
    body = filled.json()
    assert body["status"] == "buy"
    assert body["pick"]["symbol"] == "000001"
    assert body["auto_order"] is False
    assert "不自动下单" in body["detail"]


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
    monkeypatch.setattr(main, "_require_jiaoch_market_runtime", lambda: None)
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


def test_production_status_endpoint_is_authenticated_and_read_only(monkeypatch):
    monkeypatch.setattr(
        main,
        "build_production_status",
        lambda settings: {"status": "healthy", "observed_at": "now", "checks": []},
    )
    client = TestClient(main.create_app())
    response = client.get("/api/production/status")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_holdings_endpoint_tracks_default_positions(monkeypatch, tmp_path):
    def fake_history(symbol, market, lookback_days=360, adjust="qfq"):
        return sample_frame("up"), "test-provider"

    class MustNotReadL1:
        def quotes(self, symbols):
            raise AssertionError("holdings endpoint must not read a non-Jiaoch L1 provider")

    monkeypatch.setattr(main, "SETTINGS", replace(main.SETTINGS, holdings_path=str(tmp_path / "holdings.json")))
    monkeypatch.setattr(main, "_require_jiaoch_market_runtime", lambda: None)
    monkeypatch.setattr(main.DATA_PROVIDER, "history", fake_history)
    monkeypatch.setattr(main.RECOMMENDATIONS, "l1_quotes", MustNotReadL1())
    client = TestClient(main.create_app())

    response = client.get("/api/holdings")

    assert response.status_code == 200
    data = response.json()
    assert [item["symbol"] for item in data["items"]] == ["159567", "520700"]
    assert data["l1_quote"]["quote_count"] == 0
