from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings


RANK = {"healthy": 0, "degraded": 1, "unhealthy": 2}


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _check(name: str, status: str, message: str, **evidence: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "message": message, "evidence": evidence}


def _load(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def _parse(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed


def _age_hours(value: Any, now: datetime) -> float:
    return max(0.0, (now - _parse(value)).total_seconds() / 3600)


def _recommendations(settings: Settings, now: datetime, trade_dates: set[str]) -> dict[str, Any]:
    try:
        payload = _load(settings.latest_recommendations_path)
        generated = payload.get("generated_at")
        if not generated:
            return _check("recommendations", "unhealthy", "最新推荐缺少生成时间。")
        age = _age_hours(generated, now)
        summary = payload.get("summary") or {}
        if summary.get("running") and age * 60 > settings.production_running_max_minutes:
            return _check("recommendations", "unhealthy", "推荐任务长时间停留在 running。", age_hours=round(age, 2))
        if now.date().isoformat() in trade_dates and age > settings.production_recommendation_max_age_hours:
            return _check("recommendations", "unhealthy", "交易日推荐快照已过期。", age_hours=round(age, 2))
        items = payload.get("items") or []
        if not isinstance(items, list):
            return _check("recommendations", "unhealthy", "推荐 items 结构无效。")
        if len(items) > 3:
            return _check("recommendations", "unhealthy", "推荐数量超过 3 只上限。", item_count=len(items))
        for item in items:
            zone, levels = item.get("entry_zone") or {}, item.get("levels") or {}
            plan = (item.get("trade_plans") or {}).get("short_term") or {}
            required = all(zone.get(key) is not None for key in ("low", "high")) and all(
                levels.get(key) is not None for key in ("support", "resistance", "stop_loss", "take_profit")
            )
            if not required or not item.get("risks") or not (plan.get("horizon") or plan.get("holding_period")):
                return _check("recommendations", "unhealthy", "推荐缺少完整操作建议。", symbol=item.get("symbol"))
        return _check("recommendations", "healthy", "推荐快照可读且结构完整。", item_count=len(items), age_hours=round(age, 2))
    except Exception as exc:
        return _check("recommendations", "unhealthy", f"无法读取最新推荐：{type(exc).__name__}。")


def _lock(settings: Settings, now: datetime) -> dict[str, Any]:
    path = Path(settings.recommendation_lock_path)
    if not path.exists():
        return _check("recommendation_lock", "healthy", "当前无推荐任务锁。")
    try:
        age = _age_hours(_load(str(path)).get("started_at"), now)
        if age * 60 > settings.production_running_max_minutes:
            return _check("recommendation_lock", "unhealthy", "推荐任务锁已陈旧。", age_hours=round(age, 2))
        return _check("recommendation_lock", "healthy", "推荐任务锁仍在允许时长内。", age_hours=round(age, 2))
    except Exception as exc:
        return _check("recommendation_lock", "unhealthy", f"任务锁无效：{type(exc).__name__}。")


def _json_freshness(name: str, path: str, now: datetime, max_hours: int, stale_status: str) -> dict[str, Any]:
    try:
        payload = _load(path)
        age = _age_hours(payload.get("updated_at"), now)
        status = stale_status if age > max_hours else "healthy"
        return _check(name, status, "数据已过期。" if status != "healthy" else "数据新鲜。", age_hours=round(age, 2))
    except Exception as exc:
        return _check(name, stale_status, f"数据不可读：{type(exc).__name__}。")


def _market_cache(settings: Settings, now: datetime) -> dict[str, Any]:
    try:
        modified = datetime.fromtimestamp(Path(settings.market_data_cache_path).stat().st_mtime, tz=now.tzinfo)
        age = (now - modified).total_seconds() / 3600
        status = "unhealthy" if age > settings.production_market_cache_max_age_hours else "healthy"
        return _check("market_cache", status, "行情缓存已过期。" if status != "healthy" else "行情缓存新鲜。", age_hours=round(age, 2))
    except Exception as exc:
        return _check("market_cache", "unhealthy", f"行情缓存不可读：{type(exc).__name__}。")


def _provider(settings: Settings, now: datetime) -> dict[str, Any]:
    try:
        payload = _load(settings.akshare_status_path)
        failures = [v for v in (payload.get("endpoints") or {}).values() if v.get("status") == "failed" and _age_hours(v.get("updated_at"), now) <= settings.production_provider_failure_window_hours]
        if not failures:
            return _check("provider", "healthy", "近期无关键 provider 失败。")
        fallback = settings.market_data_provider == "tushare" and settings.tushare_fallback_to_akshare
        return _check("provider", "degraded" if fallback else "unhealthy", "provider 近期存在失败。", failure_count=len(failures), fallback_enabled=fallback)
    except Exception as exc:
        return _check("provider", "degraded", f"provider 状态不可读：{type(exc).__name__}。")


def build_production_status(settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    observed = now or _now()
    try:
        calendar = _load(settings.trade_calendar_cache_path)
        trade_dates = {str(item)[:10] for item in calendar.get("dates", [])}
    except Exception:
        trade_dates = set()
    checks = [
        _recommendations(settings, observed, trade_dates),
        _lock(settings, observed),
        _json_freshness("trade_calendar", settings.trade_calendar_cache_path, observed, settings.production_calendar_max_age_hours, "unhealthy"),
        _market_cache(settings, observed),
        _json_freshness("industry_cache", settings.industry_cache_path, observed, settings.production_industry_max_age_hours, "degraded"),
        _provider(settings, observed),
    ]
    status = max((item["status"] for item in checks), key=RANK.__getitem__)
    return {"status": status, "observed_at": observed.isoformat(), "checks": checks}
