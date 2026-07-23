from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from app.config import Settings
from app.current_pool import POLICY_ID
from app.recommendation_profile import DEFAULT_PROFILE, profile_to_dict
from app.production_status import build_production_status, process_health_alert
from app.storage import write_json


NOW = datetime(2026, 7, 10, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def complete_item():
    return {
        "symbol": "600519",
        "entry_zone": {"low": 100, "high": 102},
        "levels": {"support": 98, "resistance": 110, "stop_loss": 95, "take_profit": 115},
        "trade_plans": {"short_term": {"horizon": "3-10个交易日"}},
        "operation_advice": {
            "action": "watch",
            "entry_zone": {"low": 100, "high": 102},
            "stop_loss": 95,
            "take_profit": 115,
            "trigger_conditions": ["entry_zone_and_signal_confirmed"],
            "take_profit_or_reduce": {
                "condition": "price_gte_take_profit",
                "trigger_price": 115,
                "action": "reduce_or_take_profit",
            },
            "invalidation_conditions": ["price_lte_stop_loss"],
            "expected_holding_period": "3-10 trading days",
            "holding_period": "3-10个交易日",
            "invalidation": "跌破止损位",
        },
        "auto_order": False,
        "market": "a",
        "risks": ["测试风险"],
    }


def settings(tmp_path):
    paths = {
        "latest_recommendations_path": str(tmp_path / "latest.json"),
        "recommendation_lock_path": str(tmp_path / "lock.json"),
        "market_data_cache_path": str(tmp_path / "market.sqlite"),
        "industry_cache_path": str(tmp_path / "industry.json"),
        "akshare_status_path": str(tmp_path / "akshare.json"),
        "trade_calendar_cache_path": str(tmp_path / "calendar.json"),
        "current_pool_audit_path": str(tmp_path / "current-pool-audit.json"),
    }
    return replace(Settings(), **paths)


def write_healthy_artifacts(cfg):
    write_json(cfg.trade_calendar_cache_path, {"updated_at": NOW.isoformat(), "dates": ["2026-07-10"]})
    write_json(
        cfg.latest_recommendations_path,
        {
            "generated_at": NOW.isoformat(),
            "trade_date": "2026-07-10",
            "target_trade_date": "2026-07-10",
            "run_slot": "pre_open",
            "data_as_of": "2026-07-10",
            "items": [],
            "summary": {"running": False, "target_trade_date": "2026-07-10", "data_as_of": "2026-07-10"},
        },
    )
    write_json(
        cfg.industry_cache_path,
        {
            "updated_at": NOW.isoformat(),
            "source": "AKShare stock_board_industry_*",
            "industries": [],
            "symbol_map": {},
        },
    )
    write_json(cfg.akshare_status_path, {"updated_at": NOW.isoformat(), "endpoints": {}, "events": []})
    write_current_pool_audit(cfg.current_pool_audit_path)
    with sqlite3.connect(cfg.market_data_cache_path) as connection:
        connection.execute("CREATE TABLE daily_bars (date TEXT NOT NULL)")
        connection.execute("INSERT INTO daily_bars(date) VALUES (?)", ("2026-07-10",))
        connection.commit()


def write_current_pool_audit(path, *, source_as_of="2026-07-10"):
    payload = {
        "schema": "current-pool-coverage-audit",
        "schema_version": "current-pool-coverage-audit/v1",
        "policy_id": POLICY_ID,
        "development_only": True,
        "evidence_scope": "development_only",
        "source_as_of": source_as_of,
        "source_ids": {"universe": "jiaoch", "history_summary": "jiaoch", "risk_snapshot": "jiaoch"},
        "input_descriptor_sha256": {"universe": "1" * 64, "history_summary": "2" * 64, "risk_snapshot": "3" * 64},
        "risk_snapshot_complete": True,
        "risk_gate_passed": True,
        "production_recommendation_eligible": False,
        "min_signal_bars": 90,
        "universe_items": [{"ts_code": "600001.SH", "name": "甲", "market": "主板", "exchange": "SSE", "list_status": "L", "risk_flags": {"is_st": False, "is_suspended": False}}],
        "item_history_status": [{"symbol": "600001", "eligible": True, "bar_count": 120, "fetch_status": "success", "signal_ready": True, "signal_allowed_today": True}],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    payload["canonical_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
    write_json(path, payload)


def find(result, name):
    return next(item for item in result["checks"] if item["name"] == name)


def test_fresh_empty_recommendation_is_healthy(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    result = build_production_status(cfg, NOW)
    assert result["status"] == "healthy"
    assert result["core_status"] == "healthy"
    assert result["enhancement_status"] == "healthy"
    assert find(result, "current_pool")["status"] == "healthy"


def test_missing_or_stale_current_pool_audit_is_unhealthy(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    Path(cfg.current_pool_audit_path).unlink()
    assert find(build_production_status(cfg, NOW), "current_pool")["status"] == "unhealthy"

    write_current_pool_audit(cfg.current_pool_audit_path, source_as_of="2026-07-07")
    assert find(build_production_status(cfg, NOW), "current_pool")["status"] == "unhealthy"


def test_invalid_utf8_current_pool_audit_is_unhealthy_without_escaping(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    Path(cfg.current_pool_audit_path).write_bytes(b"\xff\xfe")

    check = find(build_production_status(cfg, NOW), "current_pool")

    assert check["status"] == "unhealthy"
    assert "Unicode" not in check["message"]


def test_blocked_current_pool_recommendation_is_explicitly_unhealthy(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    payload = json.loads(Path(cfg.latest_recommendations_path).read_text(encoding="utf-8"))
    payload["recommendation_status"] = "blocked_current_pool_gate"
    write_json(cfg.latest_recommendations_path, payload)

    check = find(build_production_status(cfg, NOW), "recommendations")

    assert check["status"] == "unhealthy"
    assert "股票池" in check["message"]


def test_stale_recommendation_is_unhealthy(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    write_json(cfg.latest_recommendations_path, {"generated_at": (NOW - timedelta(hours=48)).isoformat(), "trade_date": "2026-07-08", "items": [], "summary": {}})
    result = build_production_status(cfg, NOW)
    assert find(result, "recommendations")["status"] == "unhealthy"


def test_recommendation_oldest_data_as_of_must_match_expected_trade_date(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    payload = json.loads(Path(cfg.latest_recommendations_path).read_text(encoding="utf-8"))
    payload["data_as_of"] = "2026-07-08"
    payload["summary"]["data_as_of"] = "2026-07-08"
    write_json(cfg.latest_recommendations_path, payload)

    check = find(build_production_status(cfg, NOW), "recommendations")

    assert check["status"] == "unhealthy"
    assert "最旧数据日" in check["message"]


def test_failed_recommendation_is_unhealthy_and_provider_failure_is_degraded(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    write_json(cfg.akshare_status_path, {"updated_at": NOW.isoformat(), "endpoints": {"stock_zh_a_hist": {"status": "failed", "updated_at": NOW.isoformat()}}, "events": []})
    degraded = build_production_status(cfg, NOW)
    assert find(degraded, "provider")["status"] == "degraded"
    assert degraded["core_status"] == "healthy"
    assert degraded["enhancement_status"] == "degraded"
    assert find(degraded, "provider")["domain"] == "enhancement"
    write_json(cfg.latest_recommendations_path, {"generated_at": NOW.isoformat(), "trade_date": "2026-07-10", "items": [], "errors": [{"message": "upstream"}], "summary": {"failed": True}})
    assert find(build_production_status(cfg, NOW), "recommendations")["status"] == "unhealthy"


def test_too_many_or_incomplete_recommendations_are_unhealthy(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    write_json(cfg.latest_recommendations_path, {"generated_at": NOW.isoformat(), "trade_date": "2026-07-10", "items": [complete_item()] * 4, "summary": {}})
    assert find(build_production_status(cfg, NOW), "recommendations")["status"] == "unhealthy"
    broken = complete_item()
    broken["levels"].pop("stop_loss")
    write_json(cfg.latest_recommendations_path, {"generated_at": NOW.isoformat(), "trade_date": "2026-07-10", "items": [broken], "summary": {}})
    assert find(build_production_status(cfg, NOW), "recommendations")["status"] == "unhealthy"


def test_stale_lock_is_unhealthy_and_stale_industry_is_degraded(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    write_json(cfg.recommendation_lock_path, {"started_at": (NOW - timedelta(hours=2)).isoformat()})
    assert find(build_production_status(cfg, NOW), "recommendation_lock")["status"] == "unhealthy"
    cfg = settings(tmp_path / "industry")
    write_healthy_artifacts(cfg)
    write_json(cfg.industry_cache_path, {"updated_at": (NOW - timedelta(hours=100)).isoformat()})
    assert find(build_production_status(cfg, NOW), "industry_cache")["status"] == "degraded"


def test_market_cache_checks_latest_bar_date_not_only_file_mtime(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    write_json(
        cfg.trade_calendar_cache_path,
        {
            "updated_at": NOW.isoformat(),
            "dates": ["2026-07-08", "2026-07-09", "2026-07-10"],
        },
    )
    with sqlite3.connect(cfg.market_data_cache_path) as connection:
        connection.execute("DELETE FROM daily_bars")
        connection.execute("INSERT INTO daily_bars(date) VALUES (?)", ("2026-07-08",))
        connection.commit()

    check = find(build_production_status(cfg, NOW), "market_cache")

    assert check["status"] == "unhealthy"
    assert check["evidence"]["max_bar_date"] == "2026-07-08"
    assert check["evidence"]["trade_date_lag_sessions"] == 2


def test_profile_evidence_health_is_explicit_and_missing_fails_closed(tmp_path):
    cfg = replace(
        settings(tmp_path),
        recommendation_profile_id="primary_50_return_15_drawdown",
        recommendation_profile_evidence_path=str(tmp_path / "profile-evidence.json"),
    )
    write_healthy_artifacts(cfg)

    result = build_production_status(cfg, NOW)
    check = find(result, "recommendation_profile")

    assert check["status"] == "unhealthy"
    assert "缺少" in check["message"]


def test_incomplete_profile_receipt_is_not_reported_healthy(tmp_path):
    cfg = replace(
        settings(tmp_path),
        recommendation_profile_id=DEFAULT_PROFILE.profile_id,
        recommendation_profile_evidence_path=str(tmp_path / "profile-evidence.json"),
    )
    write_healthy_artifacts(cfg)
    profile = profile_to_dict(DEFAULT_PROFILE)
    write_json(
        cfg.recommendation_profile_evidence_path,
        {
            "profile_id": profile["profile_id"],
            "version": profile["version"],
            "profile_hash": profile["profile_hash"],
            "status": "incomplete",
            "blocking_gates": ["all_rolling_12m"],
            "metrics": {},
            "evidence": {"pit_contract": True, "temporal_contract": True, "cost_slippage": True},
        },
    )

    check = find(build_production_status(cfg, NOW), "recommendation_profile")

    assert check["status"] == "unhealthy"
    assert "未完成" in check["message"]


def test_industry_cache_source_mismatch_is_degraded(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    write_json(
        cfg.industry_cache_path,
        {
            "updated_at": NOW.isoformat(),
            "source": "research_membership_fixture",
            "industries": [],
            "symbol_map": {},
        },
    )

    check = find(build_production_status(cfg, NOW), "industry_cache")

    assert check["status"] == "degraded"


def test_recommendations_health_rejects_run_slot_target_mismatch(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    write_json(
        cfg.latest_recommendations_path,
        {
            "generated_at": NOW.isoformat(),
            "trade_date": "2026-07-10",
            "signal_date": "2026-07-10",
            "target_trade_date": "2026-07-13",
            "run_slot": "pre_open",
            "items": [],
            "summary": {"running": False},
        },
    )

    check = find(build_production_status(cfg, NOW), "recommendations")

    assert check["status"] == "unhealthy"
    assert "目标交易日" in check["message"]


def test_recommendations_health_accepts_explicit_next_session_pre_open(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    write_json(
        cfg.trade_calendar_cache_path,
        {"updated_at": NOW.isoformat(), "dates": ["2026-07-10", "2026-07-13"]},
    )
    write_json(
        cfg.latest_recommendations_path,
        {
            "generated_at": NOW.isoformat(),
            "trade_date": "2026-07-10",
            "signal_date": "2026-07-10",
            "target_trade_date": "2026-07-13",
            "target_trade_date_semantics": "next_trading_session",
            "run_slot": "pre_open",
            "data_as_of": "2026-07-10",
            "items": [],
            "summary": {
                "running": False,
                "target_trade_date": "2026-07-13",
                "target_trade_date_semantics": "next_trading_session",
                "data_as_of": "2026-07-10",
            },
        },
    )

    check = find(build_production_status(cfg, NOW), "recommendations")

    assert check["status"] == "healthy"


def test_recommendations_health_rejects_future_data_as_of(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    item = complete_item()
    write_json(
        cfg.latest_recommendations_path,
        {
            "generated_at": NOW.isoformat(),
            "trade_date": "2026-07-10",
            "signal_date": "2026-07-10",
            "target_trade_date": "2026-07-10",
            "run_slot": "pre_open",
            "data_as_of": "2026-07-11",
            "items": [item],
            "summary": {"running": False, "data_as_of": "2026-07-11"},
        },
    )

    check = find(build_production_status(cfg, NOW), "recommendations")

    assert check["status"] == "unhealthy"
    assert "数据截至" in check["message"]


def test_recommendations_health_requires_a_share_operation_contract(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    item = complete_item()
    item["market"] = "etf"
    item["auto_order"] = True
    item["operation_advice"].pop("invalidation")
    write_json(
        cfg.latest_recommendations_path,
        {
            "generated_at": NOW.isoformat(),
            "trade_date": "2026-07-10",
            "target_trade_date": "2026-07-10",
            "run_slot": "pre_open",
            "data_as_of": "2026-07-10",
            "items": [item],
            "summary": {"running": False, "data_as_of": "2026-07-10"},
        },
    )

    check = find(build_production_status(cfg, NOW), "recommendations")

    assert check["status"] == "unhealthy"
    assert "操作建议" in check["message"]


def test_recommendations_health_requires_explicit_trigger_contract(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    item = complete_item()
    item["operation_advice"].pop("trigger_conditions")
    write_json(
        cfg.latest_recommendations_path,
        {
            "generated_at": NOW.isoformat(),
            "trade_date": "2026-07-10",
            "target_trade_date": "2026-07-10",
            "run_slot": "pre_open",
            "data_as_of": "2026-07-10",
            "items": [item],
            "summary": {"running": False, "data_as_of": "2026-07-10"},
        },
    )

    check = find(build_production_status(cfg, NOW), "recommendations")

    assert check["status"] == "unhealthy"


def test_cli_exit_codes(monkeypatch, capsys):
    import app.jobs as jobs

    for status, expected in (("healthy", 0), ("degraded", 1), ("unhealthy", 2)):
        monkeypatch.setattr(jobs, "build_production_status", lambda settings, value=status: {"status": value, "checks": []})
        assert jobs.main(["production-check", "--no-alert"]) == expected
        assert f'"status": "{status}"' in capsys.readouterr().out


def test_alert_transition_dedup_and_recovery(tmp_path):
    cfg = replace(settings(tmp_path), alert_webhook_url="https://example.invalid", production_health_state_path=str(tmp_path / "health-state.json"))
    sent = []

    def sender(url, payload):
        sent.append(payload)

    bad = {"status": "unhealthy", "core_status": "unhealthy", "enhancement_status": "healthy", "observed_at": NOW.isoformat(), "checks": [{"name": "calendar", "status": "unhealthy", "message": "stale"}]}
    good = {"status": "healthy", "core_status": "healthy", "enhancement_status": "healthy", "observed_at": NOW.isoformat(), "checks": []}
    assert process_health_alert(cfg, bad, NOW, sender)["notified"] is True
    assert process_health_alert(cfg, bad, NOW + timedelta(minutes=5), sender)["notified"] is False
    assert process_health_alert(cfg, bad, NOW + timedelta(hours=7), sender)["notified"] is True
    assert process_health_alert(cfg, good, NOW + timedelta(hours=8), sender)["notified"] is True
    assert [item["status"] for item in sent] == ["unhealthy", "unhealthy", "healthy"]
    assert sent[0]["core_status"] == "unhealthy"
    assert sent[0]["enhancement_status"] == "healthy"
