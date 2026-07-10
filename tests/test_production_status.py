from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.production_status import build_production_status
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
    assert build_production_status(cfg, NOW)["status"] == "healthy"


def test_stale_recommendation_is_unhealthy(tmp_path):
    cfg = settings(tmp_path)
    write_healthy_artifacts(cfg)
    write_json(cfg.latest_recommendations_path, {"generated_at": (NOW - timedelta(hours=48)).isoformat(), "trade_date": "2026-07-08", "items": [], "summary": {}})
    result = build_production_status(cfg, NOW)
    assert find(result, "recommendations")["status"] == "unhealthy"


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
