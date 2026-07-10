from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.production_status import build_production_status, process_health_alert
from app.storage import write_json


NOW = datetime(2026, 7, 10, 16, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def complete_item():
    return {
        "symbol": "600519",
        "entry_zone": {"low": 100, "high": 102},
        "levels": {"support": 98, "resistance": 110, "stop_loss": 95, "take_profit": 115},
        "trade_plans": {"short_term": {"horizon": "3-10个交易日"}},
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
    }
    return replace(Settings(), **paths)


def write_healthy_artifacts(cfg):
    write_json(cfg.trade_calendar_cache_path, {"updated_at": NOW.isoformat(), "dates": ["2026-07-10"]})
    write_json(cfg.latest_recommendations_path, {"generated_at": NOW.isoformat(), "trade_date": "2026-07-10", "items": [], "summary": {"running": False}})
    write_json(cfg.industry_cache_path, {"updated_at": NOW.isoformat(), "symbol_map": {}})
    write_json(cfg.akshare_status_path, {"updated_at": NOW.isoformat(), "endpoints": {}, "events": []})
    with open(cfg.market_data_cache_path, "wb") as handle:
        handle.write(b"sqlite")


def find(result, name):
    return next(item for item in result["checks"] if item["name"] == name)


def test_fresh_empty_recommendation_is_healthy(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    result = build_production_status(cfg, NOW)
    assert result["status"] == "healthy"
    assert result["core_status"] == "healthy"
    assert result["enhancement_status"] == "healthy"


def test_stale_recommendation_is_unhealthy(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    write_json(cfg.latest_recommendations_path, {"generated_at": (NOW - timedelta(hours=48)).isoformat(), "trade_date": "2026-07-08", "items": [], "summary": {}})
    result = build_production_status(cfg, NOW)
    assert find(result, "recommendations")["status"] == "unhealthy"


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
