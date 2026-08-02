from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from app.config import Settings
from app.current_pool_gate import CurrentPoolGateError, load_current_pool_audit
from app.industry_strength import IndustryStrengthProvider
from app.recommendation_contract import (
    recommendation_operation_contract_errors,
    recommendation_publication_receipt_errors,
    recommendation_snapshot_publication_errors,
)
from app.recommendation_evidence import verify_profile_evidence_receipt
from app.storage import read_jsonl_strict, write_json


RANK = {"healthy": 0, "degraded": 1, "unhealthy": 2}


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _check(
    name: str, status: str, message: str, domain: str = "core", **evidence: Any
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "domain": domain,
        "message": message,
        "evidence": evidence,
    }


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
    age = (now - _parse(value)).total_seconds() / 3600
    if age < -0.25:
        raise ValueError("timestamp is in the future")
    return max(0.0, age)


def _recommendations(settings: Settings, now: datetime, trade_dates: set[str]) -> dict[str, Any]:
    try:
        payload = _load(settings.latest_recommendations_path)
        generated = payload.get("generated_at")
        if not generated:
            return _check("recommendations", "unhealthy", "最新推荐缺少生成时间。")
        age = _age_hours(generated, now)
        summary = payload.get("summary") or {}
        if summary.get("failed"):
            return _check("recommendations", "unhealthy", "最近一次推荐任务执行失败。", error_count=len(payload.get("errors") or []))
        if summary.get("running") and age * 60 > settings.production_running_max_minutes:
            return _check("recommendations", "unhealthy", "推荐任务长时间停留在 running。", age_hours=round(age, 2))
        trade_date = str(payload.get("trade_date") or "")[:10]
        target_trade_date = str(payload.get("target_trade_date") or "")[:10]
        run_slot = str(payload.get("run_slot") or "")
        if not target_trade_date:
            return _check("recommendations", "unhealthy", "推荐缺少目标交易日。")
        if run_slot not in {"pre_open", "open_confirm", "pre_close", "post_close"}:
            return _check("recommendations", "unhealthy", "推荐缺少有效运行时段。", run_slot=run_slot)
        if target_trade_date:
            if target_trade_date not in trade_dates:
                return _check("recommendations", "unhealthy", "推荐目标交易日不在交易日历中。", target_trade_date=target_trade_date)
            if summary.get("target_trade_date") and str(summary.get("target_trade_date"))[:10] != target_trade_date:
                return _check("recommendations", "unhealthy", "推荐摘要目标交易日与快照不一致。")
            summary_slot = summary.get("run_slot")
            if isinstance(summary_slot, dict):
                summary_slot = summary_slot.get("slot")
            if summary_slot and str(summary_slot) != run_slot:
                return _check("recommendations", "unhealthy", "推荐摘要运行时段与快照不一致。")
            target_semantics = str(
                payload.get("target_trade_date_semantics")
                or summary.get("target_trade_date_semantics")
                or ""
            )
            if trade_date and target_trade_date != trade_date:
                if target_semantics == "next_trading_session":
                    expected_next = next(
                        (
                            value
                            for value in sorted(trade_dates)
                            if str(value)[:10] > trade_date
                        ),
                        None,
                    )
                    if target_trade_date != expected_next:
                        return _check(
                            "recommendations",
                            "unhealthy",
                            "推荐目标交易日不是信号日后的下一交易日。",
                            run_slot=run_slot,
                            target_trade_date=target_trade_date,
                            expected_next_trade_date=expected_next,
                        )
                elif run_slot in {"pre_open", "open_confirm", "pre_close"}:
                    return _check(
                        "recommendations",
                        "unhealthy",
                        "盘中推荐目标交易日与信号交易日不一致。",
                        run_slot=run_slot,
                    )
            if run_slot == "post_close" and trade_date and target_trade_date < trade_date:
                return _check("recommendations", "unhealthy", "盘后推荐目标交易日早于信号交易日。", run_slot=run_slot)
        if now.date().isoformat() in trade_dates and age > settings.production_recommendation_max_age_hours:
            return _check("recommendations", "unhealthy", "交易日推荐快照已过期。", age_hours=round(age, 2))
        items = payload.get("items") or []
        if not isinstance(items, list):
            return _check("recommendations", "unhealthy", "推荐 items 结构无效。")
        if len(items) > 3:
            return _check("recommendations", "unhealthy", "推荐数量超过 3 只上限。", item_count=len(items))
        if payload.get("recommendation_status") == "blocked_profile_gate":
            return _check("recommendations", "unhealthy", "推荐被策略证据/生产健康门槛阻断。")
        if payload.get("recommendation_status") == "blocked_current_pool_gate":
            return _check("recommendations", "unhealthy", "推荐被股票池审计/生产资格门禁阻断。")
        if payload.get("recommendation_status") in {
            "blocked_operation_contract",
            "blocked_daily_publication_cap",
            "blocked_snapshot_contract",
        }:
            return _check(
                "recommendations",
                "unhealthy",
                "推荐被发布合同门禁阻断。",
            )
        data_as_of = str(payload.get("data_as_of") or summary.get("data_as_of") or "")[:10]
        if payload.get("recommendation_status") not in {"not_run_not_trade_day", "no_snapshot"} and not data_as_of:
            return _check("recommendations", "unhealthy", "推荐缺少数据截至日期。")
        generated_date = _parse(generated).date().isoformat()
        if data_as_of and data_as_of > generated_date:
            return _check(
                "recommendations",
                "unhealthy",
                "推荐数据截至日期晚于生成时间。",
                data_as_of=data_as_of,
            )
        if data_as_of and payload.get("recommendation_status") not in {
            "not_run_not_trade_day",
            "no_snapshot",
        }:
            expected_dates = _expected_current_pool_dates(now, trade_dates)
            if run_slot != "pre_open" and now.date().isoformat() in trade_dates:
                expected_dates = {now.date().isoformat()}
            if expected_dates and data_as_of not in expected_dates:
                return _check(
                    "recommendations",
                    "unhealthy",
                    "推荐依赖的最旧数据日已过期。",
                    data_as_of=data_as_of,
                    expected_data_dates=sorted(expected_dates),
                )
        if items:
            if not data_as_of:
                return _check("recommendations", "unhealthy", "推荐缺少数据截至日期。")
            try:
                generated_date = _parse(generated).date().isoformat()
            except Exception:
                generated_date = ""
            if generated_date and data_as_of > generated_date:
                return _check("recommendations", "unhealthy", "推荐数据截至日期晚于生成时间。", data_as_of=data_as_of)
            if summary.get("data_as_of") and str(summary.get("data_as_of"))[:10] != data_as_of:
                return _check("recommendations", "unhealthy", "推荐摘要数据截至日期与快照不一致。")
        snapshot_contract_errors = list(
            recommendation_snapshot_publication_errors(payload)
        )
        if items:
            try:
                ledger_rows = read_jsonl_strict(
                    f"{settings.recommendation_history_path}.publications"
                )
            except (OSError, TypeError, ValueError):
                snapshot_contract_errors.append("publication_ledger")
            else:
                snapshot_contract_errors.extend(
                    recommendation_publication_receipt_errors(
                        payload,
                        ledger_rows,
                    )
                )
        snapshot_contract_errors = list(
            dict.fromkeys(snapshot_contract_errors)
        )
        if snapshot_contract_errors:
            return _check(
                "recommendations",
                "unhealthy",
                "推荐缺少完整操作建议或发布状态无效。",
                contract_errors=list(snapshot_contract_errors),
            )
        for item in items:
            contract_errors = recommendation_operation_contract_errors(item)
            if contract_errors:
                return _check(
                    "recommendations",
                    "unhealthy",
                    "推荐缺少完整操作建议。",
                    symbol=item.get("symbol"),
                    contract_errors=list(contract_errors),
                )
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


def _json_freshness(
    name: str,
    path: str,
    now: datetime,
    max_hours: int,
    stale_status: str,
    domain: str = "core",
) -> dict[str, Any]:
    try:
        payload = _load(path)
        age = _age_hours(payload.get("updated_at"), now)
        status = stale_status if age > max_hours else "healthy"
        return _check(name, status, "数据已过期。" if status != "healthy" else "数据新鲜。", domain=domain, age_hours=round(age, 2))
    except Exception as exc:
        return _check(name, stale_status, f"数据不可读：{type(exc).__name__}。", domain=domain)


def _market_cache(
    settings: Settings,
    now: datetime,
    trade_dates: set[str] | None = None,
) -> dict[str, Any]:
    try:
        path = Path(settings.market_data_cache_path).resolve()
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=now.tzinfo)
        file_age = (now - modified).total_seconds() / 3600
        uri = "file:%s?mode=ro" % quote(str(path), safe="/")
        with sqlite3.connect(uri, uri=True) as connection:
            max_bar_date, row_count = connection.execute(
                "SELECT MAX(date), COUNT(*) FROM daily_bars"
            ).fetchone()
            non_jiaoch_rows = 0
            if settings.market_data_provider == "jiaoch":
                non_jiaoch_rows = int(
                    connection.execute(
                        """
                        SELECT COUNT(*)
                        FROM daily_bars
                        WHERE lower(trim(coalesce(source, ''))) NOT LIKE 'jiaoch%'
                        """
                    ).fetchone()[0]
                    or 0
                )
        max_bar_date = str(max_bar_date or "")[:10]
        if not max_bar_date or int(row_count or 0) <= 0:
            raise ValueError("daily_bars is empty")
        if settings.market_data_provider == "jiaoch" and non_jiaoch_rows:
            raise ValueError("daily_bars contains non-Jiaoch rows")

        expected = sorted(
            value
            for value in (trade_dates or set())
            if str(value)[:10] <= now.date().isoformat()
        )
        lag_sessions = None
        if expected:
            if max_bar_date not in expected:
                raise ValueError("latest daily bar is outside the trade calendar")
            lag_sessions = len([value for value in expected if value > max_bar_date])
        stale_by_file = file_age > settings.production_market_cache_max_age_hours
        stale_by_session = lag_sessions is not None and lag_sessions > 1
        status = "unhealthy" if stale_by_file or stale_by_session else "healthy"
        message = "行情缓存已过期。" if status != "healthy" else "行情缓存新鲜。"
        return _check(
            "market_cache",
            status,
            message,
            file_age_hours=round(file_age, 2),
            max_bar_date=max_bar_date,
            row_count=int(row_count),
            trade_date_lag_sessions=lag_sessions,
        )
    except Exception as exc:
        return _check("market_cache", "unhealthy", f"行情缓存不可读：{type(exc).__name__}。")


def _provider(settings: Settings, now: datetime) -> dict[str, Any]:
    if settings.market_data_provider == "jiaoch":
        missing = [
            name
            for name in ("JIAOCH_TOKEN", "JIAOCH_STK_MINS_TOKEN")
            if not os.getenv(name, "")
        ]
        if missing:
            return _check(
                "provider",
                "unhealthy",
                "Jiaoch credential slots are not configured",
                domain="enhancement",
                source="jiaoch",
                missing_env=missing,
            )
        return _check(
            "provider",
            "healthy",
            "Jiaoch-only market provider is configured",
            domain="enhancement",
            source="jiaoch",
            fallback_enabled=False,
        )
    try:
        payload = _load(settings.akshare_status_path)
        failures = [v for v in (payload.get("endpoints") or {}).values() if v.get("status") == "failed" and _age_hours(v.get("updated_at"), now) <= settings.production_provider_failure_window_hours]
        if not failures:
            return _check("provider", "healthy", "近期无关键 provider 失败。", domain="enhancement")
        fallback = settings.market_data_provider == "tushare" and settings.tushare_fallback_to_akshare
        return _check("provider", "degraded", "provider 近期存在局部失败，当前依靠缓存或降级路径。", domain="enhancement", failure_count=len(failures), fallback_enabled=fallback)
    except Exception as exc:
        return _check("provider", "degraded", f"provider 状态不可读：{type(exc).__name__}。", domain="enhancement")


def _industry_cache(settings: Settings, now: datetime) -> dict[str, Any]:
    try:
        payload = _load(settings.industry_cache_path)
        if not IndustryStrengthProvider._valid_live_cache(payload):
            raise ValueError("industry cache source or schema is not live-compatible")
        age = _age_hours(payload.get("updated_at"), now)
        status = "degraded" if age > settings.production_industry_max_age_hours else "healthy"
        return _check(
            "industry_cache",
            status,
            "行业缓存已过期。" if status != "healthy" else "行业缓存新鲜。",
            domain="enhancement",
            age_hours=round(age, 2),
            source=payload.get("source"),
            industry_count=len(payload.get("industries") or []),
            symbol_count=len(payload.get("symbol_map") or {}),
        )
    except Exception as exc:
        return _check("industry_cache", "degraded", f"行业缓存不可用：{type(exc).__name__}。", domain="enhancement")


def _expected_current_pool_dates(now: datetime, trade_dates: set[str]) -> set[str] | None:
    dates = sorted(value for value in trade_dates if value <= now.date().isoformat())
    if not dates:
        return None
    today = now.date().isoformat()
    if today in dates and (now.hour, now.minute) < (9, 30):
        previous = [value for value in dates if value < today]
        return {today, previous[-1]} if previous else {today}
    if today in dates:
        return {today}
    return {dates[-1]}


def _current_pool(
    settings: Settings, now: datetime, trade_dates: set[str]
) -> dict[str, Any]:
    try:
        audit = load_current_pool_audit(
            settings.current_pool_audit_path,
            now=now,
            max_age_hours=settings.production_current_pool_max_age_hours,
            expected_source_dates=_expected_current_pool_dates(now, trade_dates),
        )
        return _check(
            "current_pool",
            "healthy",
            "当前股票池审计门禁有效。",
            canonical_sha256=audit["canonical_sha256"],
            source_as_of=audit["source_as_of"],
            age_hours=round(float(audit["age_hours"]), 2),
            allowed_symbol_count=len(audit["allowed_symbols"]),
            evidence_scope=audit["evidence_scope"],
            production_recommendation_eligible=False,
        )
    except CurrentPoolGateError as exc:
        return _check(
            "current_pool",
            "unhealthy",
            f"当前股票池审计门禁无效：{str(exc)[:200]}。",
        )


def _recommendation_profile(settings: Settings) -> dict[str, Any]:
    profile_id = str(getattr(settings, "recommendation_profile_id", "") or "").strip()
    if not profile_id:
        return _check("recommendation_profile", "healthy", "未启用强制策略 profile。")
    path = str(getattr(settings, "recommendation_profile_evidence_path", "") or "")
    try:
        payload = _load(path)
        metrics = payload.get("metrics")
        evidence = payload.get("evidence")
        if not isinstance(metrics, dict) or not isinstance(evidence, dict):
            raise ValueError("metrics/evidence missing")
        if payload.get("profile_id") != profile_id or payload.get("version") != "v1" or not payload.get("profile_hash"):
            raise ValueError("profile binding missing or mismatched")
        if payload.get("status") not in {"qualified", "live_proven"}:
            raise ValueError("receipt incomplete: %s" % ",".join(payload.get("blocking_gates") or []))
        if payload.get("status") == "live_proven" and payload.get("live_proof") is not True:
            raise ValueError("live proof flag mismatch")
        receipt_check = verify_profile_evidence_receipt(payload)
        if not receipt_check.get("ok"):
            raise ValueError("receipt invalid: %s" % ",".join(receipt_check.get("errors") or []))
        required_gates = {
            "annualized_return",
            "max_drawdown",
            "observed_win_rate",
            "wilson_lower",
            "payoff_ratio",
            "profit_factor",
            "calmar",
            "minimum_sample",
            "signal_days_120",
            "all_rolling_12m",
            "pit_contract",
            "temporal_contract",
            "cost_slippage",
            "artifact_execution",
            "strategy_signal_replay",
            "outcome_replay",
            "double_cost",
            "regime",
        }
        gates = payload.get("gates")
        if not isinstance(gates, dict):
            raise ValueError("receipt gates missing")
        missing_gates = sorted(name for name in required_gates if gates.get(name) is not True)
        if missing_gates:
            raise ValueError("receipt gates incomplete: %s" % ",".join(missing_gates))
        required_evidence = (
            "pit_contract",
            "temporal_contract",
            "cost_slippage",
            "artifact_execution",
            "strategy_signal_replay",
            "outcome_replay",
        )
        missing = [key for key in required_evidence if evidence.get(key) is not True]
        if missing:
            raise ValueError("evidence missing: %s" % ",".join(missing))
        return _check(
            "recommendation_profile",
            "healthy",
            "推荐策略 profile 证据可读。",
            profile_id=profile_id,
            evidence_receipt_id=payload.get("evidence_receipt_id"),
        )
    except FileNotFoundError:
        return _check("recommendation_profile", "unhealthy", "推荐策略 profile 证据文件缺少。", profile_id=profile_id)
    except ValueError as exc:
        return _check(
            "recommendation_profile",
            "unhealthy",
            f"推荐策略 profile 证据未完成：{str(exc)[:200]}。",
            profile_id=profile_id,
        )
    except Exception as exc:
        return _check(
            "recommendation_profile",
            "unhealthy",
            f"推荐策略 profile 证据无效：{type(exc).__name__}。",
            profile_id=profile_id,
        )


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
        _market_cache(settings, observed, trade_dates),
        _industry_cache(settings, observed),
        _provider(settings, observed),
        _current_pool(settings, observed, trade_dates),
        _recommendation_profile(settings),
    ]
    core_status = max(
        (item["status"] for item in checks if item["domain"] == "core"),
        key=RANK.__getitem__,
        default="healthy",
    )
    enhancement_status = max(
        (item["status"] for item in checks if item["domain"] == "enhancement"),
        key=RANK.__getitem__,
        default="healthy",
    )
    status = max((core_status, enhancement_status), key=RANK.__getitem__)
    return {
        "status": status,
        "core_status": core_status,
        "enhancement_status": enhancement_status,
        "observed_at": observed.isoformat(),
        "checks": checks,
    }


def _fingerprint(status: dict[str, Any]) -> str:
    problems = [(item.get("name"), item.get("status"), item.get("message")) for item in status.get("checks", []) if item.get("status") != "healthy"]
    encoded = json.dumps(
        {
            "core_status": status.get("core_status"),
            "enhancement_status": status.get("enhancement_status"),
            "problems": problems,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _post_webhook(url: str, payload: dict[str, Any]) -> None:
    request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(request, timeout=10).read()
    except (urllib.error.URLError, TimeoutError):
        return


def process_health_alert(settings: Settings, status: dict[str, Any], now: datetime | None = None, sender=None) -> dict[str, Any]:
    observed = now or _now()
    path = Path(settings.production_health_state_path)
    try:
        previous = _load(str(path)) if path.exists() else {}
    except Exception:
        previous = {}
    fingerprint = _fingerprint(status)
    changed = previous.get("status") != status.get("status") or previous.get("fingerprint") != fingerprint
    last_notified = previous.get("last_notified_at")
    reminder = bool(last_notified and status.get("status") != "healthy" and _age_hours(last_notified, observed) >= settings.production_health_reminder_hours)
    notify = bool(settings.production_health_alerts_enabled and settings.alert_webhook_url and (changed or reminder))
    if notify:
        problems = [{"name": item.get("name"), "status": item.get("status"), "message": item.get("message")} for item in status.get("checks", []) if item.get("status") != "healthy"]
        (sender or _post_webhook)(settings.alert_webhook_url, {"event_type": "production_health", "status": status.get("status"), "core_status": status.get("core_status"), "enhancement_status": status.get("enhancement_status"), "observed_at": status.get("observed_at"), "problems": problems})
    state = {"status": status.get("status"), "core_status": status.get("core_status"), "enhancement_status": status.get("enhancement_status"), "fingerprint": fingerprint, "updated_at": observed.isoformat(), "last_notified_at": observed.isoformat() if notify else previous.get("last_notified_at")}
    write_json(str(path), state)
    return {"notified": notify, "reason": "changed" if changed else "reminder" if reminder else "suppressed"}
