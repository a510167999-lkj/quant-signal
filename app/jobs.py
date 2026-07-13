import argparse
import hashlib
import json
import math
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.a_share_universe import select_deep_scan_candidates
from app.artifact_native_evidence import (
    build_artifact_native_evidence,
    verify_artifact_native_evidence,
    write_artifact_native_evidence,
)
from app.config import get_settings
from app.current_pool import build_current_pool_coverage, classify_current_pool_item
from app.current_pool_gate import load_current_pool_audit
from app.current_pool_history_source import (
    build_current_pool_history_summary,
    verify_current_pool_history_descriptor,
)
from app.current_pool_source import (
    _strict_json_loads,
    fetch_jiaoch_current_pool_descriptor,
    verify_current_pool_universe_descriptor,
)
from app.current_pool_risk_source import (
    fetch_jiaoch_current_pool_risk_descriptor,
    verify_current_pool_risk_descriptor,
)
from app.industry_history import IndustryHistoryProvider
from app.logging_setup import configure_logging
from app.main import DATA_PROVIDER, DISCLAIMER
from app.margin_eligibility import MarginEligibilityProvider
from app.production_status import build_production_status, process_health_alert
from app.recommendation_evidence import build_profile_evidence_receipt
from app.recommendations import RUN_SLOT_AUTO, RUN_SLOT_CONTEXTS, RecommendationService
from app.research_backtest import (
    run_candidate_research_backtest,
    run_historical_universe_research_backtest,
)
from app.research_pit import (
    build_pit_universe_payload,
    verify_research_evidence_bundle,
    write_pit_universe_artifact,
    write_research_evidence_bundle,
    write_strict_qualified_trades_payload,
    write_strict_research_evidence_bundle,
)
from app.research_pit_collector import (
    ControlledTushareCollector,
    SystemTrustedClock,
    UrllibTushareTransport,
)
from app.research_partitions import (
    assert_range_allowed,
    load_temporal_partition_contract,
)
from app.research_pit_store import (
    MAX_CNINFO_PDF_BYTES,
    MAX_RAW_BYTES,
    AuditedPointInTimeUniverse,
    PITReceiptStore,
)
from app.research_pit_sources import resolve_tushare_source
from app.research_sweep import sweep_qualified_trades
from app.research_validation import (
    append_experiment_event,
    audited_authority_from_universe,
    run_frozen_strategy_validation,
    validate_point_in_time_contract,
    write_report_artifact,
)
from app.storage import read_json, write_json


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _positive_int_arg(value: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("value must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def _positive_float_arg(value: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("value must be a positive finite number") from exc
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive finite number")
    return parsed


def _exposure_multipliers(args) -> list[float]:
    exposure_multiplier = getattr(args, "exposure_multiplier", None)
    if exposure_multiplier is not None:
        return [max(float(exposure_multiplier), 0.0)]
    if not getattr(args, "exposure_sweep", False):
        return [1.0]
    maximum = max(float(getattr(args, "max_exposure_multiplier", 6.0) or 1.0), 1.0)
    values = [1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0]
    if maximum not in values:
        values.append(maximum)
    return [value for value in sorted(set(values)) if value <= maximum]


def _force_exposure_multipliers(args) -> bool:
    return getattr(args, "exposure_multiplier", None) is not None


def _split_csv_arg(value: str):
    if value is None:
        return None
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _compact_research_sweep_payload(payload, sweep_payload, output_limit=12):
    source_summary = payload.get("summary") or {}
    source_keys = [
        "start_date",
        "end_date",
        "candidate_mode",
        "max_universe_symbols",
        "daily_prefilter_max_deep",
        "historical_candidate_days",
        "historical_market_breadth_days",
        "industry_rotation_context_enabled",
        "industry_rotation_max_boards",
        "industry_rotation_days",
        "dragon_tiger_context_enabled",
        "dragon_tiger_days",
        "dragon_tiger_fetch_errors",
        "dragon_tiger_caveat",
        "margin_eligibility_context_enabled",
        "margin_eligibility_scope",
        "margin_eligibility_days",
        "margin_eligibility_fetch_errors",
        "margin_eligibility_caveat",
        "candidate_count",
        "fetched_symbols",
        "raw_qualified_trade_count",
        "selected_trade_count",
        "trade_win_rate_pct",
        "portfolio_compounded_return_pct",
        "portfolio_max_drawdown_pct",
        "stop_loss_pct",
        "take_profit_pct",
        "trailing_stop_pct",
    ]
    row_keys = [
        "label",
        "required_signal_tags",
        "excluded_signal_tags",
        "market_levels",
        "exposure_multiplier",
        "annual_financing_rate_pct",
        "roundtrip_cost_bps",
        "slippage_bps",
        "capital_model",
        "pre_exit_calendar_gap_days",
        "prior_high_trailing_stop_pct",
        "prior_high_trailing_activation_pct",
        "partial_profit_activation_pct",
        "partial_profit_fraction",
        "correlation_threshold",
        "correlation_lookback_days",
        "correlation_skip_count",
        "selected_trade_count",
        "trade_win_count",
        "trade_nonwin_count",
        "signal_days",
        "trade_win_rate_pct",
        "trade_avg_return_pct",
        "portfolio_compounded_return_pct",
        "portfolio_max_drawdown_pct",
        "rolling_1y_latest_return_pct",
        "rolling_1y_latest_full_window",
        "rolling_1y_latest_trade_count",
        "rolling_1y_latest_active_position_days",
        "target_win_drawdown_pass",
        "target_one_year_return_pass",
        "target_all_pass",
        "target_gap_1y_return_pct",
    ]
    return {
        "source_summary": {key: source_summary.get(key) for key in source_keys},
        "sweep_summary": {
            key: sweep_payload.get(key)
            for key in [
                "qualified_trade_count",
                "available_tag_count",
                "spec_count",
                "returned_count",
                "target_win_drawdown_pass_count",
                "target_all_pass_count",
                "target_win_rate_pct",
                "target_drawdown_pct",
                "target_one_year_return_pct",
                "min_trades",
                "exposure_multipliers",
                "annual_financing_rate_pct",
                "roundtrip_cost_bps",
                "slippage_bps",
                "capital_model",
                "pre_exit_calendar_gap_days",
                "prior_high_trailing_stop_pct",
                "prior_high_trailing_activation_pct",
                "partial_profit_activation_pct",
                "partial_profit_fraction",
                "excluded_signal_tags",
                "correlation_threshold",
                "correlation_lookback_days",
                "correlation_min_periods",
            ]
        },
        "diagnostics": sweep_payload.get("diagnostics") or {},
        "top": [
            {key: row.get(key) for key in row_keys}
            for row in (sweep_payload.get("top") or [])[: max(0, output_limit)]
        ],
    }


def _parse_hold_days_list(value: str) -> list[int]:
    days = []
    for item in str(value or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            day = int(item)
        except ValueError:
            continue
        if day > 0 and day not in days:
            days.append(day)
    return days or [3, 5, 7, 10]


def _load_qualified_trades_payload(path: str) -> dict:
    payload = read_json(path, {})
    if isinstance(payload, list):
        return {"summary": {}, "qualified_trades": payload}
    if not isinstance(payload, dict):
        return {"summary": {}, "qualified_trades": []}
    return {
        "summary": payload.get("summary") or payload.get("source_summary") or {},
        "qualified_trades": payload.get("qualified_trades") or [],
    }


def _write_qualified_trades_payload(path: str, payload) -> dict:
    export_payload = {
        "summary": payload.get("summary") or {},
        "qualified_trades": payload.get("qualified_trades") or [],
    }
    write_json(path, export_payload)
    return {
        "path": path,
        "qualified_trade_count": len(export_payload["qualified_trades"]),
    }


def _load_json_rows(path: str, *keys: str) -> list:
    payload = read_json(path, [])
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            if isinstance(payload.get(key), list):
                return payload[key]
    raise ValueError(f"expected JSON row list: {path}")


def _load_current_pool_descriptor(path: str, expected_schema: str) -> dict:
    try:
        descriptor_path = Path(path)
        payload = _strict_json_loads(descriptor_path.read_bytes())
        if (
            not isinstance(payload, dict)
            or payload.get("schema") != expected_schema
            or payload.get("source_id") != "jiaoch"
            or not isinstance(payload.get("items"), list)
        ):
            raise ValueError
        as_of = str(payload.get("as_of") or "")
        if len(as_of) == 10:
            source_date = date.fromisoformat(as_of).isoformat()
        else:
            instant = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise ValueError
            source_date = instant.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        if expected_schema in {
            "current-pool-universe-input/v1",
            "current-pool-risk-input/v1",
        }:
            embedded_sha256 = payload.get("descriptor_sha256")
            unsigned_payload = {
                key: value for key, value in payload.items() if key != "descriptor_sha256"
            }
            canonical_unsigned = json.dumps(
                unsigned_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            verified_sha256 = hashlib.sha256(canonical_unsigned).hexdigest()
            if embedded_sha256 != verified_sha256:
                raise ValueError
            if len(descriptor_path.stem) == 64 and descriptor_path.stem != verified_sha256:
                raise ValueError
            retrieved_at = datetime.fromisoformat(str(payload.get("retrieved_at") or ""))
            if (
                retrieved_at.tzinfo is None
                or retrieved_at.utcoffset() is None
                or retrieved_at.utcoffset().total_seconds() != 8 * 60 * 60
                or retrieved_at.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
                != source_date
            ):
                raise ValueError
            if expected_schema == "current-pool-universe-input/v1":
                verify_current_pool_universe_descriptor(payload)
                if payload.get("partition_coverage") != {
                    "exchanges": ["SSE", "SZSE"],
                    "list_statuses": ["L", "D", "P", "G"],
                    "partition_count": 8,
                }:
                    raise ValueError
                if payload.get("risk_snapshot_complete") is not False:
                    raise ValueError
                if payload.get("production_recommendation_eligible") is not False:
                    raise ValueError
                if payload.get("risk_coverage") != {
                    "stock_st": "not_collected",
                    "namechange": "not_collected",
                    "suspend_d": "not_collected",
                }:
                    raise ValueError
            else:
                verify_current_pool_risk_descriptor(payload)
                expected_years = int(source_date[:4]) - 1989
                if payload.get("partition_coverage") != {
                    "stock_st": 1,
                    "suspend_d": 1,
                    "namechange": expected_years,
                    "partition_count": expected_years + 2,
                }:
                    raise ValueError
                if (
                    payload.get("risk_snapshot_complete") is not True
                    or payload.get("risk_gate_passed") is not True
                    or payload.get("production_recommendation_eligible") is not False
                    or not isinstance(payload.get("universe_descriptor_sha256"), str)
                    or len(payload["universe_descriptor_sha256"]) != 64
                ):
                    raise ValueError
                seen_risk_symbols = set()
                for item in payload["items"]:
                    if (
                        not isinstance(item, dict)
                        or set(item)
                        != {
                            "ts_code",
                            "is_st",
                            "st_type",
                            "is_suspended",
                            "suspension_reason",
                            "active_name",
                        }
                        or not isinstance(item.get("ts_code"), str)
                        or type(item.get("is_st")) is not bool
                        or type(item.get("is_suspended")) is not bool
                        or (
                            item.get("st_type") is not None
                            and not isinstance(item.get("st_type"), str)
                        )
                            or (
                                item.get("suspension_reason") is not None
                                and item.get("suspension_reason")
                                not in {
                                    "suspended",
                                    "resume_day_no_new_entry",
                                    "conflict",
                                }
                        )
                        or (
                            item.get("active_name") is not None
                            and not isinstance(item.get("active_name"), str)
                        )
                        or item["ts_code"] in seen_risk_symbols
                    ):
                        raise ValueError
                    seen_risk_symbols.add(item["ts_code"])
                receipts = payload.get("partition_receipts")
                if not isinstance(receipts, list) or len(receipts) != expected_years + 2:
                    raise ValueError
                for receipt in receipts:
                    if (
                        not isinstance(receipt, dict)
                        or set(receipt)
                        != {"api_name", "params", "row_count", "rows_sha256"}
                        or receipt.get("api_name")
                        not in {"stock_st", "suspend_d", "namechange"}
                        or not isinstance(receipt.get("params"), dict)
                        or type(receipt.get("row_count")) is not int
                        or receipt["row_count"] < 0
                        or not isinstance(receipt.get("rows_sha256"), str)
                        or len(receipt["rows_sha256"]) != 64
                    ):
                        raise ValueError
        elif (
            expected_schema == "current-pool-history-summary/v1"
            and "descriptor_sha256" in payload
        ):
            embedded_sha256 = payload.get("descriptor_sha256")
            unsigned_payload = {
                key: value for key, value in payload.items() if key != "descriptor_sha256"
            }
            verified_sha256 = hashlib.sha256(
                json.dumps(
                    unsigned_payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            if embedded_sha256 != verified_sha256:
                raise ValueError
            if len(descriptor_path.stem) == 64 and descriptor_path.stem != verified_sha256:
                raise ValueError
        else:
            canonical_unsigned = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            verified_sha256 = hashlib.sha256(canonical_unsigned).hexdigest()
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise ValueError("current-pool input descriptor rejected") from exc
    return {
        "payload": payload,
        "source_as_of": source_date,
        "descriptor_sha256": verified_sha256,
    }


def _load_current_pool_universe(descriptor: dict) -> list[dict]:
    items = descriptor["payload"]["items"]
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("current-pool input descriptor rejected")
    required = ("market", "exchange", "list_status")
    if any(not all(str(item.get(key) or "").strip() for key in required) for item in items):
        raise ValueError(
            "current-pool universe items require structured market, exchange, and list_status"
        )
    return items


def _load_current_pool_history_summary(descriptor: dict) -> dict[str, int]:
    items = descriptor["payload"]["items"]
    histories: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("history summary items must be objects")
        symbol = str(item.get("ts_code") or item.get("symbol") or "").strip()
        if not symbol or "bar_count" not in item or symbol in histories:
            raise ValueError("history summary items require unique symbol and bar_count")
        histories[symbol] = item["bar_count"]
    return histories


def _write_current_pool_audit(output_dir: str, payload: dict) -> dict:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{payload['canonical_sha256']}.json"
    content = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    created = not destination.exists()
    if created:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f"{destination.name}.", suffix=".tmp", dir=str(directory)
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            directory_descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    elif destination.read_bytes() != content:
        raise ValueError("content-addressed current-pool audit mismatch")
    return {
        "canonical_sha256": payload["canonical_sha256"],
        "path": str(destination),
        "created": created,
    }


class CurrentPoolPublishUncertainStateError(RuntimeError):
    """A failed publish rollback left the target's durable state unknown."""


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_replace_bytes(target: Path, content: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.rollback.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _publish_current_pool_audit(
    source_path: str,
    target_path: str,
    *,
    now: datetime,
    max_age_hours: int,
) -> dict:
    source = Path(source_path)
    target = Path(target_path)
    source_bytes = source.read_bytes()
    directory = target.parent
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(directory)
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(source_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        verified = load_current_pool_audit(
            temporary,
            now=now,
            max_age_hours=max_age_hours,
        )
        target_is_symlink = target.is_symlink()
        target_exists = target.exists()
        previous_target_bytes = (
            target.read_bytes() if target_exists and not target_is_symlink else None
        )
        published = (
            target_is_symlink
            or not target_exists
            or previous_target_bytes != source_bytes
        )
        if published:
            os.replace(temporary, target)
            try:
                _fsync_directory(directory)
            except OSError:
                try:
                    if previous_target_bytes is None:
                        try:
                            target.unlink()
                        except FileNotFoundError:
                            pass
                    else:
                        _atomic_replace_bytes(target, previous_target_bytes)
                    _fsync_directory(directory)
                except Exception as rollback_error:
                    raise CurrentPoolPublishUncertainStateError(
                        "current-pool publish state is uncertain after directory "
                        "fsync and rollback failure"
                    ) from rollback_error
                raise
        return {
            "canonical_sha256": verified["canonical_sha256"],
            "source_path": str(source),
            "path": str(target),
            "bytes_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "published": published,
        }
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _warm_market_cache(args) -> dict:
    settings = get_settings()
    service = RecommendationService(settings, DATA_PROVIDER, DISCLAIMER)
    industry_payload = service.industry.build_map(use_cache_on_error=True)
    industry_map = industry_payload.get("symbol_map", {})
    snapshot = service.universe.snapshot(use_cache_on_error=True)
    max_deep = args.max_deep or settings.scan_max_deep
    candidates = select_deep_scan_candidates(
        snapshot=snapshot,
        max_deep=max_deep,
        min_amount=settings.scan_min_amount,
        min_price=settings.scan_min_price,
        max_price=settings.scan_max_price,
        industry_map=industry_map,
        per_industry_top_n=settings.scan_per_industry_top_n,
        industry_top_n=settings.scan_industry_top_n,
    )
    targets = [
        {"symbol": "510300", "market": "etf", "name": "沪深300ETF"},
        {"symbol": "159915", "market": "etf", "name": "创业板ETF"},
    ]
    seen = {("%s:%s" % (item["market"], item["symbol"])) for item in targets}
    for candidate in candidates:
        key = "%s:%s" % (candidate.get("market", "a"), candidate.get("symbol"))
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            {
                "symbol": str(candidate.get("symbol")),
                "market": str(candidate.get("market") or "a"),
                "name": candidate.get("name"),
            }
        )

    workers = max(1, min(int(args.workers or 1), 12))
    errors = []
    warmed = []

    def warm_one(target):
        frame, source = DATA_PROVIDER.history(
            symbol=target["symbol"],
            market=target["market"],
            lookback_days=args.lookback_days,
            adjust=args.adjust,
        )
        return {
            **target,
            "rows": len(frame),
            "latest_date": str(frame["date"].iloc[-1])[:10],
            "source": source,
        }

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_map = {executor.submit(warm_one, target): target for target in targets}
        for future in as_completed(future_map):
            target = future_map[future]
            try:
                warmed.append(future.result())
            except Exception as exc:
                errors.append({**target, "message": str(exc)})

    warmed.sort(key=lambda item: (item.get("market", ""), item.get("symbol", "")))
    return {
        "target_count": len(targets),
        "warmed_count": len(warmed),
        "error_count": len(errors),
        "workers": workers,
        "lookback_days": args.lookback_days,
        "adjust": args.adjust,
        "cache_path": settings.market_data_cache_path,
        "candidate_count": len(candidates),
        "industry_count": len(
            {item.get("industry") for item in candidates if item.get("industry")}
        ),
        "errors": errors[:50],
        "sample": warmed[:10],
    }


def _compact_hold_sweep_result(hold_days: int, payload, sweep_payload, output_limit=5):
    compact = _compact_research_sweep_payload(payload, sweep_payload, output_limit=output_limit)
    return {
        "hold_days": hold_days,
        "source_summary": compact["source_summary"],
        "sweep_summary": compact["sweep_summary"],
        "diagnostics": compact["diagnostics"],
        "top": compact["top"],
    }


def main(argv=None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Quant signal scheduled jobs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate-recommendations")
    generate.add_argument("--force", action="store_true")
    generate.add_argument("--max-deep", type=int, default=None)
    generate.add_argument(
        "--run-slot",
        default=RUN_SLOT_AUTO,
        choices=[RUN_SLOT_AUTO, *RUN_SLOT_CONTEXTS.keys()],
    )
    generate.add_argument(
        "--target-trade-date",
        default=None,
        help="目标 A 股交易日，YYYY-MM-DD；使用 next 表示下一交易日",
    )

    warm = subparsers.add_parser("warm-market-cache")
    warm.add_argument("--max-deep", type=int, default=None)
    warm.add_argument("--workers", type=int, default=4)
    warm.add_argument("--lookback-days", type=int, default=620)
    warm.add_argument("--adjust", default="qfq", choices=["", "qfq", "hfq"])

    mootdx_check = subparsers.add_parser("mootdx-l1-check")
    mootdx_check.add_argument("--symbols", default="600519,000001,301308,002607")
    mootdx_check.add_argument("--servers", default=None)
    mootdx_check.add_argument("--timeout-seconds", type=float, default=None)

    monitor = subparsers.add_parser("monitor-recommendations")
    monitor.add_argument("--force", action="store_true")

    planned_exits = subparsers.add_parser("monitor-planned-exits")
    planned_exits.add_argument("--force", action="store_true")

    production_check = subparsers.add_parser("production-check")
    production_check.add_argument("--no-alert", action="store_true")

    research = subparsers.add_parser("research-backtest")
    research.add_argument("--start-date", default="2024-07-05")
    research.add_argument("--max-deep", type=int, default=120)
    research.add_argument("--top-n", type=int, default=10)
    research.add_argument("--hold-days", type=int, default=10)
    research.add_argument("--lookback-days", type=int, default=620)
    research.add_argument("--live-snapshot", action="store_true")
    research.add_argument("--cache-dir", default="data/research_cache")
    research.add_argument("--progress-every", type=int, default=10)
    research.add_argument("--buy-only", action="store_true")
    research.add_argument("--min-score", type=float, default=None)
    research.add_argument("--stop-loss-pct", type=float, default=None)
    research.add_argument("--take-profit-pct", type=float, default=None)
    research.add_argument("--trailing-stop-pct", type=float, default=None)
    research.add_argument("--symbol-cooldown-days", type=int, default=0)
    research.add_argument("--max-active-positions", type=int, default=0)
    research.add_argument("--announcement-context", action="store_true")
    research.add_argument("--announcement-lookback-days", type=int, default=None)
    research.add_argument("--require-announcement-event", default=None)
    research.add_argument("--require-all-announcement-events", action="store_true")
    research.add_argument("--exclude-announcement-event", default=None)
    research.add_argument("--require-market-level", default=None)
    research.add_argument("--require-signal-tag", default=None)
    research.add_argument("--require-all-signal-tags", action="store_true")
    research.add_argument("--exclude-signal-tag", default=None)
    research.add_argument("--min-prior-win-rate", type=float, default=None)
    research.add_argument("--min-prior-avg-return", type=float, default=None)
    research.add_argument("--max-prior-avg-adverse", type=float, default=None)
    research.add_argument("--margin-eligibility-context", action="store_true")
    research.add_argument("--include-qualified-trades", action="store_true")
    research.add_argument("--qualified-trades-output", default=None)

    historical = subparsers.add_parser("research-historical-universe")
    historical.add_argument("--start-date", default="2024-07-05")
    historical.add_argument("--end-date", required=True)
    historical.add_argument("--max-deep", type=int, default=80)
    historical.add_argument("--top-n", type=int, default=10)
    historical.add_argument("--hold-days", type=int, default=10)
    historical.add_argument("--lookback-days", type=int, default=620)
    historical.add_argument("--max-universe-symbols", type=int, default=0)
    historical_pit = historical.add_mutually_exclusive_group(required=True)
    historical_pit.add_argument("--pit-universe-path", default=None)
    historical_pit.add_argument(
        "--audited-pit-universe-path", "--audited-pit-artifact-path", default=None
    )
    historical_pit.add_argument("--composite-pit-descriptor-path", default=None)
    historical.add_argument("--expected-coverage-audit-sha256", default=None)
    historical.add_argument("--expected-artifact-root-sha256", default=None)
    historical.add_argument("--expected-composite-root-sha256", default=None)
    historical.add_argument("--temporal-contract-path", required=True)
    historical.add_argument("--expected-temporal-contract-sha256", required=True)
    historical.add_argument("--expected-temporal-role", required=True)
    historical.add_argument("--live-snapshot", action="store_true")
    historical.add_argument("--cache-dir", default="data/research_cache")
    historical.add_argument("--progress-every", type=int, default=25)
    historical.add_argument("--buy-only", action="store_true")
    historical.add_argument("--min-score", type=float, default=None)
    historical.add_argument("--stop-loss-pct", type=float, default=None)
    historical.add_argument("--take-profit-pct", type=float, default=None)
    historical.add_argument("--trailing-stop-pct", type=float, default=None)
    historical.add_argument("--symbol-cooldown-days", type=int, default=10)
    historical.add_argument("--max-active-positions", type=int, default=10)
    historical.add_argument("--announcement-context", action="store_true")
    historical.add_argument("--announcement-lookback-days", type=int, default=None)
    historical.add_argument("--require-announcement-event", default=None)
    historical.add_argument("--require-all-announcement-events", action="store_true")
    historical.add_argument("--exclude-announcement-event", default=None)
    historical.add_argument("--require-market-level", default=None)
    historical.add_argument("--require-signal-tag", default=None)
    historical.add_argument("--require-all-signal-tags", action="store_true")
    historical.add_argument("--exclude-signal-tag", default=None)
    historical.add_argument("--min-prior-win-rate", type=float, default=None)
    historical.add_argument("--min-prior-avg-return", type=float, default=None)
    historical.add_argument("--max-prior-avg-adverse", type=float, default=None)
    historical.add_argument("--industry-rotation-context", action="store_true")
    historical.add_argument("--industry-rotation-max-boards", type=int, default=40)
    historical.add_argument("--margin-eligibility-context", action="store_true")
    historical.add_argument("--dragon-tiger-context", action="store_true")
    historical.add_argument("--include-qualified-trades", action="store_true")
    historical.add_argument("--qualified-trades-output", default=None)

    historical_sweep = subparsers.add_parser("research-historical-sweep")
    historical_sweep.add_argument("--start-date", default="2024-07-05")
    historical_sweep.add_argument("--end-date", required=True)
    historical_sweep.add_argument("--max-deep", type=int, default=80)
    historical_sweep.add_argument("--top-n", type=int, default=10)
    historical_sweep.add_argument("--hold-days", type=int, default=10)
    historical_sweep.add_argument("--lookback-days", type=int, default=620)
    historical_sweep.add_argument("--max-universe-symbols", type=int, default=0)
    historical_sweep_pit = historical_sweep.add_mutually_exclusive_group(required=True)
    historical_sweep_pit.add_argument("--pit-universe-path", default=None)
    historical_sweep_pit.add_argument(
        "--audited-pit-universe-path", "--audited-pit-artifact-path", default=None
    )
    historical_sweep.add_argument("--expected-coverage-audit-sha256", default=None)
    historical_sweep.add_argument("--expected-artifact-root-sha256", required=True)
    historical_sweep.add_argument("--temporal-contract-path", required=True)
    historical_sweep.add_argument("--expected-temporal-contract-sha256", required=True)
    historical_sweep.add_argument("--expected-temporal-role", required=True)
    historical_sweep.add_argument("--live-snapshot", action="store_true")
    historical_sweep.add_argument("--cache-dir", default="data/research_cache")
    historical_sweep.add_argument("--progress-every", type=int, default=50)
    historical_sweep.add_argument("--stop-loss-pct", type=float, default=None)
    historical_sweep.add_argument("--take-profit-pct", type=float, default=None)
    historical_sweep.add_argument("--trailing-stop-pct", type=float, default=None)
    historical_sweep.add_argument("--symbol-cooldown-days", type=int, default=10)
    historical_sweep.add_argument("--max-active-positions", type=int, default=10)
    historical_sweep.add_argument("--min-trades", type=int, default=20)
    historical_sweep.add_argument("--max-filter-size", type=int, default=3)
    historical_sweep.add_argument("--required-signal-tags", default=None)
    historical_sweep.add_argument("--excluded-signal-tags", default=None)
    historical_sweep.add_argument("--market-levels", default=None)
    historical_sweep.add_argument("--target-win-rate-pct", type=float, default=52.0)
    historical_sweep.add_argument("--target-drawdown-pct", type=float, default=15.0)
    historical_sweep.add_argument("--target-one-year-return-pct", type=float, default=50.0)
    historical_sweep.add_argument("--industry-rotation-context", action="store_true")
    historical_sweep.add_argument("--industry-rotation-max-boards", type=int, default=40)
    historical_sweep.add_argument("--margin-eligibility-context", action="store_true")
    historical_sweep.add_argument("--dragon-tiger-context", action="store_true")
    historical_sweep.add_argument("--exposure-sweep", action="store_true")
    historical_sweep.add_argument("--exposure-multiplier", type=float, default=None)
    historical_sweep.add_argument("--max-exposure-multiplier", type=float, default=6.0)
    historical_sweep.add_argument("--annual-financing-rate-pct", type=float, default=0.0)
    historical_sweep.add_argument("--roundtrip-cost-bps", type=float, default=0.0)
    historical_sweep.add_argument("--slippage-bps", type=float, default=0.0)
    historical_sweep.add_argument("--pre-exit-calendar-gap-days", type=int, default=0)
    historical_sweep.add_argument("--prior-high-trailing-stop-pct", type=float, default=None)
    historical_sweep.add_argument("--prior-high-trailing-activation-pct", type=float, default=0.0)
    historical_sweep.add_argument("--partial-profit-activation-pct", type=float, default=None)
    historical_sweep.add_argument("--partial-profit-fraction", type=float, default=0.0)
    historical_sweep.add_argument("--correlation-threshold", type=float, default=None)
    historical_sweep.add_argument("--correlation-lookback-days", type=int, default=60)
    historical_sweep.add_argument("--correlation-min-periods", type=int, default=20)
    historical_sweep.add_argument("--correlation-history-lookback-days", type=int, default=620)
    historical_sweep.add_argument("--correlation-cache-dir", default=None)
    historical_sweep.add_argument(
        "--capital-model",
        choices=["signal-day", "slot-exit", "slot-daily"],
        default="signal-day",
    )
    historical_sweep.add_argument("--output-limit", type=int, default=12)
    historical_sweep.add_argument("--compact", action="store_true")
    historical_sweep.add_argument("--qualified-trades-output", default=None)

    historical_hold_sweep = subparsers.add_parser("research-historical-hold-sweep")
    historical_hold_sweep.add_argument("--start-date", default="2024-07-05")
    historical_hold_sweep.add_argument("--end-date", required=True)
    historical_hold_sweep.add_argument("--max-deep", type=int, default=80)
    historical_hold_sweep.add_argument("--top-n", type=int, default=10)
    historical_hold_sweep.add_argument("--hold-days-list", default="3,5,7,10")
    historical_hold_sweep.add_argument("--lookback-days", type=int, default=620)
    historical_hold_sweep.add_argument("--max-universe-symbols", type=int, default=0)
    historical_hold_sweep_pit = historical_hold_sweep.add_mutually_exclusive_group(required=True)
    historical_hold_sweep_pit.add_argument("--pit-universe-path", default=None)
    historical_hold_sweep_pit.add_argument(
        "--audited-pit-universe-path", "--audited-pit-artifact-path", default=None
    )
    historical_hold_sweep.add_argument("--expected-coverage-audit-sha256", default=None)
    historical_hold_sweep.add_argument("--expected-artifact-root-sha256", required=True)
    historical_hold_sweep.add_argument("--temporal-contract-path", required=True)
    historical_hold_sweep.add_argument("--expected-temporal-contract-sha256", required=True)
    historical_hold_sweep.add_argument("--expected-temporal-role", required=True)
    historical_hold_sweep.add_argument("--live-snapshot", action="store_true")
    historical_hold_sweep.add_argument("--cache-dir", default="data/research_cache")
    historical_hold_sweep.add_argument("--progress-every", type=int, default=50)
    historical_hold_sweep.add_argument("--stop-loss-pct", type=float, default=None)
    historical_hold_sweep.add_argument("--take-profit-pct", type=float, default=None)
    historical_hold_sweep.add_argument("--trailing-stop-pct", type=float, default=None)
    historical_hold_sweep.add_argument("--symbol-cooldown-days", type=int, default=None)
    historical_hold_sweep.add_argument("--max-active-positions", type=int, default=10)
    historical_hold_sweep.add_argument("--min-trades", type=int, default=20)
    historical_hold_sweep.add_argument("--max-filter-size", type=int, default=3)
    historical_hold_sweep.add_argument("--required-signal-tags", default=None)
    historical_hold_sweep.add_argument("--excluded-signal-tags", default=None)
    historical_hold_sweep.add_argument("--market-levels", default=None)
    historical_hold_sweep.add_argument("--target-win-rate-pct", type=float, default=52.0)
    historical_hold_sweep.add_argument("--target-drawdown-pct", type=float, default=15.0)
    historical_hold_sweep.add_argument("--target-one-year-return-pct", type=float, default=50.0)
    historical_hold_sweep.add_argument("--margin-eligibility-context", action="store_true")
    historical_hold_sweep.add_argument("--dragon-tiger-context", action="store_true")
    historical_hold_sweep.add_argument("--exposure-sweep", action="store_true")
    historical_hold_sweep.add_argument("--exposure-multiplier", type=float, default=None)
    historical_hold_sweep.add_argument("--max-exposure-multiplier", type=float, default=6.0)
    historical_hold_sweep.add_argument("--annual-financing-rate-pct", type=float, default=0.0)
    historical_hold_sweep.add_argument("--roundtrip-cost-bps", type=float, default=0.0)
    historical_hold_sweep.add_argument("--slippage-bps", type=float, default=0.0)
    historical_hold_sweep.add_argument("--pre-exit-calendar-gap-days", type=int, default=0)
    historical_hold_sweep.add_argument("--prior-high-trailing-stop-pct", type=float, default=None)
    historical_hold_sweep.add_argument(
        "--prior-high-trailing-activation-pct", type=float, default=0.0
    )
    historical_hold_sweep.add_argument("--partial-profit-activation-pct", type=float, default=None)
    historical_hold_sweep.add_argument("--partial-profit-fraction", type=float, default=0.0)
    historical_hold_sweep.add_argument("--correlation-threshold", type=float, default=None)
    historical_hold_sweep.add_argument("--correlation-lookback-days", type=int, default=60)
    historical_hold_sweep.add_argument("--correlation-min-periods", type=int, default=20)
    historical_hold_sweep.add_argument("--correlation-history-lookback-days", type=int, default=620)
    historical_hold_sweep.add_argument("--correlation-cache-dir", default=None)
    historical_hold_sweep.add_argument(
        "--capital-model",
        choices=["signal-day", "slot-exit", "slot-daily"],
        default="signal-day",
    )
    historical_hold_sweep.add_argument("--output-limit", type=int, default=5)
    historical_hold_sweep.add_argument("--compact", action="store_true")

    industry_check = subparsers.add_parser("industry-history-check")
    industry_check.add_argument("--start-date", default="2024-07-05")
    industry_check.add_argument("--end-date", default=None)
    industry_check.add_argument("--max-boards", type=int, default=3)

    margin_check = subparsers.add_parser("margin-eligibility-check")
    margin_check.add_argument("--as-of", default=None)

    sweep = subparsers.add_parser("research-sweep")
    sweep.add_argument("--start-date", default="2024-07-05")
    sweep.add_argument("--max-deep", type=int, default=80)
    sweep.add_argument("--top-n", type=int, default=10)
    sweep.add_argument("--hold-days", type=int, default=10)
    sweep.add_argument("--lookback-days", type=int, default=620)
    sweep.add_argument("--cache-dir", default="data/research_cache")
    sweep.add_argument("--progress-every", type=int, default=0)
    sweep.add_argument("--stop-loss-pct", type=float, default=None)
    sweep.add_argument("--take-profit-pct", type=float, default=None)
    sweep.add_argument("--trailing-stop-pct", type=float, default=None)
    sweep.add_argument("--symbol-cooldown-days", type=int, default=10)
    sweep.add_argument("--max-active-positions", type=int, default=10)
    sweep.add_argument("--min-trades", type=int, default=20)
    sweep.add_argument("--max-filter-size", type=int, default=3)
    sweep.add_argument("--required-signal-tags", default=None)
    sweep.add_argument("--excluded-signal-tags", default=None)
    sweep.add_argument("--market-levels", default=None)
    sweep.add_argument("--target-win-rate-pct", type=float, default=52.0)
    sweep.add_argument("--target-drawdown-pct", type=float, default=15.0)
    sweep.add_argument("--target-one-year-return-pct", type=float, default=50.0)
    sweep.add_argument("--margin-eligibility-context", action="store_true")
    sweep.add_argument("--exposure-sweep", action="store_true")
    sweep.add_argument("--exposure-multiplier", type=float, default=None)
    sweep.add_argument("--max-exposure-multiplier", type=float, default=6.0)
    sweep.add_argument("--annual-financing-rate-pct", type=float, default=0.0)
    sweep.add_argument("--roundtrip-cost-bps", type=float, default=0.0)
    sweep.add_argument("--slippage-bps", type=float, default=0.0)
    sweep.add_argument("--pre-exit-calendar-gap-days", type=int, default=0)
    sweep.add_argument("--prior-high-trailing-stop-pct", type=float, default=None)
    sweep.add_argument("--prior-high-trailing-activation-pct", type=float, default=0.0)
    sweep.add_argument("--partial-profit-activation-pct", type=float, default=None)
    sweep.add_argument("--partial-profit-fraction", type=float, default=0.0)
    sweep.add_argument("--correlation-threshold", type=float, default=None)
    sweep.add_argument("--correlation-lookback-days", type=int, default=60)
    sweep.add_argument("--correlation-min-periods", type=int, default=20)
    sweep.add_argument("--correlation-history-lookback-days", type=int, default=620)
    sweep.add_argument("--correlation-cache-dir", default="data/research_cache")
    sweep.add_argument(
        "--capital-model",
        choices=["signal-day", "slot-exit", "slot-daily"],
        default="signal-day",
    )
    sweep.add_argument("--output-limit", type=int, default=12)
    sweep.add_argument("--compact", action="store_true")
    sweep.add_argument("--qualified-trades-output", default=None)

    sweep_file = subparsers.add_parser("research-sweep-file")
    sweep_file.add_argument("--qualified-trades-path", required=True)
    sweep_file.add_argument("--audited-pit-universe-path", required=True)
    sweep_file.add_argument("--start-date", required=True)
    sweep_file.add_argument("--end-date", required=True)
    sweep_file.add_argument("--temporal-contract-path", required=True)
    sweep_file.add_argument("--expected-coverage-audit-sha256", required=True)
    sweep_file.add_argument("--expected-artifact-root-sha256", required=True)
    sweep_file.add_argument("--expected-temporal-contract-sha256", required=True)
    sweep_file.add_argument("--expected-temporal-role", required=True)
    sweep_file.add_argument("--top-n", type=int, default=10)
    sweep_file.add_argument("--hold-days", type=int, default=10)
    sweep_file.add_argument("--symbol-cooldown-days", type=int, default=10)
    sweep_file.add_argument("--max-active-positions", type=int, default=10)
    sweep_file.add_argument("--min-trades", type=int, default=20)
    sweep_file.add_argument("--max-filter-size", type=int, default=3)
    sweep_file.add_argument("--required-signal-tags", default=None)
    sweep_file.add_argument("--excluded-signal-tags", default=None)
    sweep_file.add_argument("--market-levels", default=None)
    sweep_file.add_argument("--target-win-rate-pct", type=float, default=52.0)
    sweep_file.add_argument("--target-drawdown-pct", type=float, default=15.0)
    sweep_file.add_argument("--target-one-year-return-pct", type=float, default=50.0)
    sweep_file.add_argument("--exposure-sweep", action="store_true")
    sweep_file.add_argument("--exposure-multiplier", type=float, default=None)
    sweep_file.add_argument("--max-exposure-multiplier", type=float, default=6.0)
    sweep_file.add_argument("--annual-financing-rate-pct", type=float, default=0.0)
    sweep_file.add_argument("--roundtrip-cost-bps", type=float, default=0.0)
    sweep_file.add_argument("--slippage-bps", type=float, default=0.0)
    sweep_file.add_argument("--pre-exit-calendar-gap-days", type=int, default=0)
    sweep_file.add_argument("--prior-high-trailing-stop-pct", type=float, default=None)
    sweep_file.add_argument("--prior-high-trailing-activation-pct", type=float, default=0.0)
    sweep_file.add_argument("--partial-profit-activation-pct", type=float, default=None)
    sweep_file.add_argument("--partial-profit-fraction", type=float, default=0.0)
    sweep_file.add_argument("--correlation-threshold", type=float, default=None)
    sweep_file.add_argument("--correlation-lookback-days", type=int, default=60)
    sweep_file.add_argument("--correlation-min-periods", type=int, default=20)
    sweep_file.add_argument("--correlation-history-lookback-days", type=int, default=620)
    sweep_file.add_argument("--correlation-cache-dir", default=None)
    sweep_file.add_argument(
        "--capital-model",
        choices=["signal-day", "slot-exit", "slot-daily"],
        default="signal-day",
    )
    sweep_file.add_argument("--output-limit", type=int, default=12)
    sweep_file.add_argument("--compact", action="store_true")

    validate_file = subparsers.add_parser("research-validate-file")
    validate_file.add_argument("--qualified-trades-path", required=True)
    validate_file.add_argument("--audited-pit-universe-path", required=True)
    validate_file.add_argument("--experiment-id", required=True)
    validate_file.add_argument("--hypothesis", required=True)
    validate_file.add_argument("--expected-mechanism", required=True)
    validate_file.add_argument("--falsification-criterion", required=True)
    validate_file.add_argument("--exit-criterion", required=True)
    validate_file.add_argument("--final-oos-start", required=True)
    validate_file.add_argument("--start-date", required=True)
    validate_file.add_argument("--end-date", required=True)
    validate_file.add_argument("--temporal-contract-path", required=True)
    validate_file.add_argument("--expected-coverage-audit-sha256", required=True)
    validate_file.add_argument("--expected-temporal-contract-sha256", required=True)
    validate_file.add_argument("--expected-temporal-role", required=True)
    validate_file.add_argument("--expected-artifact-root-sha256", required=True)
    validate_file.add_argument("--train-days", type=int, default=365)
    validate_file.add_argument("--validation-days", type=int, default=90)
    validate_file.add_argument("--step-days", type=int, default=90)
    validate_file.add_argument("--embargo-days", type=int, default=5)
    validate_file.add_argument("--minimum-oos-trades", type=int, default=200)
    validate_file.add_argument("--ledger-path", default="data/research_experiments/ledger.jsonl")
    validate_file.add_argument("--artifact-dir", default="data/research_experiments/artifacts")
    validate_file.add_argument("--hold-days", type=int, default=5)
    validate_file.add_argument("--top-n", type=int, default=10)
    validate_file.add_argument("--symbol-cooldown-days", type=int, default=5)
    validate_file.add_argument("--max-active-positions", type=int, default=3)
    validate_file.add_argument("--required-signal-tags", default=None)
    validate_file.add_argument("--excluded-signal-tags", default=None)
    validate_file.add_argument("--market-levels", default=None)
    validate_file.add_argument("--exposure-multiplier", type=float, default=1.0)
    validate_file.add_argument("--annual-financing-rate-pct", type=float, default=8.0)
    validate_file.add_argument("--roundtrip-cost-bps", type=float, default=25.0)
    validate_file.add_argument("--slippage-bps", type=float, default=10.0)
    validate_file.add_argument(
        "--capital-model",
        choices=["signal-day", "slot-exit", "slot-daily"],
        default="slot-daily",
    )
    validate_file.add_argument("--target-win-rate-pct", type=float, default=52.0)
    validate_file.add_argument("--target-drawdown-pct", type=float, default=15.0)
    validate_file.add_argument("--target-one-year-return-pct", type=float, default=50.0)
    validate_file.add_argument("--pre-exit-calendar-gap-days", type=int, default=0)
    validate_file.add_argument("--partial-profit-activation-pct", type=float, default=None)
    validate_file.add_argument("--partial-profit-fraction", type=float, default=0.0)
    validate_file.add_argument("--correlation-threshold", type=float, default=None)
    validate_file.add_argument("--correlation-lookback-days", type=int, default=60)
    validate_file.add_argument("--correlation-cache-dir", default=None)

    build_pit = subparsers.add_parser("research-build-pit-universe")
    build_pit.add_argument("--security-master-path", required=True)
    build_pit.add_argument("--trade-calendar-path", required=True)
    build_pit.add_argument("--daily-universe-path", required=True)
    build_pit.add_argument("--source-manifest-path", required=True)
    build_pit.add_argument("--start-date", required=True)
    build_pit.add_argument("--end-date", required=True)
    build_pit.add_argument("--output-dir", default="data/research_artifacts/pit_universe")

    pit_ingest = subparsers.add_parser("research-pit-ingest-response")
    pit_ingest.add_argument("--store-dir", required=True)
    pit_ingest.add_argument(
        "--dataset", required=True, choices=["stock_basic", "trade_cal", "bak_basic"]
    )
    pit_ingest.add_argument("--partition-key", required=True)
    pit_ingest.add_argument("--endpoint", required=True)
    pit_ingest.add_argument("--params-json", required=True)
    pit_ingest.add_argument("--raw-response-path", required=True)
    pit_ingest.add_argument("--http-status", type=int, required=True)
    pit_ingest.add_argument("--retrieved-at", required=True)
    pit_ingest.add_argument("--row-cap", type=int, required=True)

    pit_ingest_suspension = subparsers.add_parser("research-pit-ingest-cninfo-suspension")
    pit_ingest_suspension.add_argument("--store-dir", required=True)
    pit_ingest_suspension.add_argument("--ts-code", required=True)
    pit_ingest_suspension.add_argument("--start-pdf-path", required=True)
    pit_ingest_suspension.add_argument("--start-source-url", required=True)
    pit_ingest_suspension.add_argument("--start-published-at", required=True)
    pit_ingest_suspension.add_argument("--start-retrieved-at", required=True)
    pit_ingest_suspension.add_argument("--resume-pdf-path", required=True)
    pit_ingest_suspension.add_argument("--resume-source-url", required=True)
    pit_ingest_suspension.add_argument("--resume-published-at", required=True)
    pit_ingest_suspension.add_argument("--resume-retrieved-at", required=True)

    pit_audit = subparsers.add_parser("research-pit-audit-store")
    pit_audit.add_argument("--store-dir", required=True)
    pit_audit.add_argument("--start-date", required=True)
    pit_audit.add_argument("--end-date", required=True)
    pit_audit.add_argument("--calendar-exchanges", default="SSE,SZSE")

    current_pool_audit = subparsers.add_parser("research-current-pool-audit")
    current_pool_audit.add_argument("--universe-path", required=True)
    current_pool_audit.add_argument("--history-summary-path", required=True)
    current_pool_audit.add_argument("--risk-path", required=True)
    current_pool_audit.add_argument("--output-dir", required=True)
    current_pool_audit.add_argument("--min-signal-bars", type=int, default=90)

    current_pool_publish = subparsers.add_parser("research-current-pool-publish")
    current_pool_publish.add_argument("--audit-path", required=True)
    current_pool_publish.add_argument("--target-path")
    current_pool_publish.add_argument("--max-age-hours", type=int)

    current_pool_fetch = subparsers.add_parser("research-current-pool-fetch-jiaoch")
    current_pool_fetch.add_argument("--as-of", required=True)
    current_pool_fetch.add_argument("--output-dir", required=True)
    current_pool_fetch.add_argument("--timeout-seconds", type=float, default=30.0)

    current_pool_risk_fetch = subparsers.add_parser("research-current-pool-fetch-risk-jiaoch")
    current_pool_risk_fetch.add_argument("--as-of", required=True)
    current_pool_risk_fetch.add_argument("--universe-path", required=True)
    current_pool_risk_fetch.add_argument("--output-dir", required=True)
    current_pool_risk_fetch.add_argument("--timeout-seconds", type=float, default=30.0)

    current_pool_history = subparsers.add_parser(
        "research-current-pool-build-history-summary"
    )
    current_pool_history.add_argument("--universe-path", required=True)
    current_pool_history.add_argument("--store-dir", required=True)
    current_pool_history.add_argument("--start-date", required=True)
    current_pool_history.add_argument("--end-date", required=True)
    current_pool_history.add_argument("--as-of", required=True)
    current_pool_history.add_argument("--output-dir", required=True)

    pit_publish = subparsers.add_parser("research-pit-publish-universe")
    pit_publish.add_argument("--store-dir", required=True)
    pit_publish.add_argument("--start-date", required=True)
    pit_publish.add_argument("--end-date", required=True)
    pit_publish.add_argument("--expected-coverage-audit-sha256", required=True)
    pit_publish.add_argument("--output-dir", default="data/research_artifacts/audited_pit_universe")
    pit_publish.add_argument(
        "--temporal-contract-path",
        default="data/research_partitions/frozen-v1.json",
    )
    pit_publish.add_argument("--temporal-role", default="development")

    evidence_bundle = subparsers.add_parser("research-build-evidence-bundle")
    evidence_bundle.add_argument("--pit-universe-path", required=True)
    evidence_bundle.add_argument("--market-data-manifest-path", required=True)
    evidence_bundle.add_argument("--audited-pit-universe-path", required=True)
    evidence_bundle.add_argument("--expected-coverage-audit-sha256", required=True)
    evidence_bundle.add_argument("--expected-artifact-root-sha256", required=True)
    evidence_bundle.add_argument("--expected-temporal-contract-sha256", required=True)
    evidence_bundle.add_argument("--expected-temporal-role", required=True)
    evidence_bundle.add_argument("--output-dir", required=True)

    native_evidence = subparsers.add_parser("research-build-artifact-native-evidence")
    native_evidence.add_argument("--qualified-trades-path", required=True)
    native_evidence.add_argument("--audited-pit-universe-path", required=True)
    native_evidence.add_argument("--expected-coverage-audit-sha256", required=True)
    native_evidence.add_argument("--expected-artifact-root-sha256", required=True)
    native_evidence.add_argument("--expected-temporal-contract-sha256", required=True)
    native_evidence.add_argument("--expected-temporal-role", required=True)
    native_evidence.add_argument("--output-dir", required=True)

    strict_evidence = subparsers.add_parser("research-build-strict-evidence-bundle")
    strict_evidence.add_argument("--artifact-native-evidence-path", required=True)
    strict_evidence.add_argument("--audited-pit-universe-path", required=True)
    strict_evidence.add_argument("--expected-coverage-audit-sha256", required=True)
    strict_evidence.add_argument("--expected-artifact-root-sha256", required=True)
    strict_evidence.add_argument("--expected-temporal-contract-sha256", required=True)
    strict_evidence.add_argument("--expected-temporal-role", required=True)
    strict_evidence.add_argument("--output-dir", required=True)

    pit_fetch = subparsers.add_parser("research-pit-fetch-tushare")
    pit_fetch.add_argument("--store-dir", required=True)
    pit_fetch.add_argument("--start-date", required=True)
    pit_fetch.add_argument("--end-date", required=True)
    pit_fetch.add_argument("--source-profile", choices=("official", "jiaoch"), default="jiaoch")
    pit_fetch.add_argument("--api-url")
    pit_fetch.add_argument("--allow-insecure-official-http", action="store_true")
    pit_fetch.add_argument("--max-attempts", type=int, default=3)
    pit_fetch.add_argument("--timeout-seconds", type=float, default=30.0)
    pit_fetch.add_argument("--workers", type=int, choices=range(1, 9), default=1)
    pit_fetch.add_argument("--no-resume", action="store_true")
    pit_fetch.add_argument(
        "--temporal-contract-path",
        default="data/research_partitions/frozen-v1.json",
    )
    pit_fetch.add_argument("--temporal-role", default="development")

    pit_calendar_fetch = subparsers.add_parser("research-pit-fetch-calendars")
    pit_calendar_fetch.add_argument("--store-dir", required=True)
    pit_calendar_fetch.add_argument("--start-date", required=True)
    pit_calendar_fetch.add_argument("--end-date", required=True)
    pit_calendar_fetch.add_argument(
        "--source-profile", choices=("official", "jiaoch"), default="jiaoch"
    )
    pit_calendar_fetch.add_argument("--api-url")
    pit_calendar_fetch.add_argument("--allow-insecure-official-http", action="store_true")
    pit_calendar_fetch.add_argument("--max-attempts", type=int, default=3)
    pit_calendar_fetch.add_argument("--timeout-seconds", type=float, default=30.0)
    pit_calendar_fetch.add_argument("--workers", type=int, choices=range(1, 9), default=1)
    pit_calendar_fetch.add_argument("--no-resume", action="store_true")
    pit_calendar_fetch.add_argument(
        "--temporal-contract-path",
        default="data/research_partitions/frozen-v1.json",
    )
    pit_calendar_fetch.add_argument("--temporal-role", default="development")

    current_pool_market_fetch = subparsers.add_parser(
        "research-current-pool-fetch-market-jiaoch"
    )
    current_pool_market_fetch.add_argument("--store-dir", required=True)
    current_pool_market_fetch.add_argument("--start-date", required=True)
    current_pool_market_fetch.add_argument("--end-date", required=True)
    current_pool_market_fetch.set_defaults(
        source_profile="jiaoch",
        api_url=None,
        allow_insecure_official_http=False,
    )
    current_pool_market_fetch.add_argument(
        "--max-attempts", type=_positive_int_arg, default=3
    )
    current_pool_market_fetch.add_argument(
        "--timeout",
        "--timeout-seconds",
        dest="timeout_seconds",
        type=_positive_float_arg,
        default=30.0,
    )
    current_pool_market_fetch.add_argument(
        "--workers", type=int, choices=range(1, 3), default=2
    )
    current_pool_market_fetch.add_argument(
        "--batch-size", type=int, choices=range(1, 51), default=10
    )
    current_pool_market_fetch.add_argument("--progress-path")
    current_pool_market_fetch.add_argument("--no-resume", action="store_true")
    current_pool_market_fetch.add_argument(
        "--temporal-contract-path",
        default="data/research_partitions/frozen-v1.json",
    )
    current_pool_market_fetch.add_argument("--temporal-role", default="development")

    args = parser.parse_args(argv)
    if args.command in {"research-backtest", "research-sweep"}:
        raise ValueError(
            "legacy mutable research command is disabled; use audited historical or file validation commands"
        )
    if args.command in {
        "research-historical-sweep",
        "research-historical-hold-sweep",
        "research-sweep-file",
        "research-validate-file",
    }:
        correlation_threshold = getattr(args, "correlation_threshold", None)
        correlation_cache_dir = getattr(args, "correlation_cache_dir", None)
        if (
            correlation_threshold is not None and float(correlation_threshold) != 0.0
        ) or correlation_cache_dir is not None:
            raise ValueError("frozen research forbids legacy correlation cache or adapter usage")
    settings = (
        None
        if args.command
        in {
            "research-validate-file",
            "research-build-pit-universe",
            "research-pit-ingest-response",
            "research-pit-ingest-cninfo-suspension",
            "research-pit-audit-store",
            "research-current-pool-audit",
            "research-current-pool-fetch-jiaoch",
            "research-current-pool-fetch-risk-jiaoch",
            "research-current-pool-fetch-market-jiaoch",
            "research-current-pool-build-history-summary",
            "research-pit-publish-universe",
            "research-pit-fetch-tushare",
            "research-build-evidence-bundle",
            "research-build-artifact-native-evidence",
        }
        else get_settings()
    )
    service = (
        RecommendationService(settings, DATA_PROVIDER, DISCLAIMER)
        if args.command
        in {
            "generate-recommendations",
            "mootdx-l1-check",
            "monitor-recommendations",
            "monitor-planned-exits",
        }
        else None
    )

    if args.command == "generate-recommendations":
        payload = service.generate_daily_recommendations(
            force=args.force,
            max_deep=args.max_deep,
            run_slot=args.run_slot,
            target_trade_date=args.target_trade_date,
        )
        _print_json(
            {
                "generated_at": payload.get("generated_at"),
                "trade_date": payload.get("trade_date"),
                "target_trade_date": payload.get("target_trade_date"),
                "recommendation_status": payload.get("recommendation_status"),
                "evidence_scope": payload.get("evidence_scope"),
                "live_proof": payload.get("live_proof", False),
                "auto_order": payload.get("auto_order", False),
                "profile_gate": payload.get("profile_gate"),
                "strategy_profile": payload.get("strategy_profile"),
                "run_slot": payload.get("run_slot"),
                "run_slot_label": payload.get("run_slot_label"),
                "summary": payload.get("summary"),
                "top_symbols": [item.get("symbol") for item in payload.get("items", [])[:10]],
            }
        )
        return 0

    if args.command == "warm-market-cache":
        _print_json(_warm_market_cache(args))
        return 0

    if args.command == "mootdx-l1-check":
        symbols = _split_csv_arg(args.symbols) or []
        provider = service.l1_quotes
        if args.servers is not None or args.timeout_seconds is not None:
            from app.mootdx_l1 import MootdxL1QuoteProvider

            provider = MootdxL1QuoteProvider(
                servers=args.servers if args.servers is not None else settings.mootdx_servers,
                timeout_seconds=args.timeout_seconds
                if args.timeout_seconds is not None
                else settings.mootdx_timeout_seconds,
                enabled=True,
            )
        payload = provider.quotes(symbols)
        payload["symbols"] = symbols
        _print_json(payload)
        return 0

    if args.command == "monitor-recommendations":
        payload = service.monitor_recommendations(force=args.force)
        _print_json(payload)
        return 0

    if args.command == "monitor-planned-exits":
        payload = service.monitor_planned_exits(force=args.force)
        _print_json(payload)
        return 0

    if args.command == "production-check":
        payload = build_production_status(settings)
        if not args.no_alert:
            payload["alert"] = process_health_alert(settings, payload)
        _print_json(payload)
        return {"healthy": 0, "degraded": 1, "unhealthy": 2}.get(payload.get("status"), 2)

    if args.command == "research-current-pool-publish":
        publish_settings = settings or get_settings()
        max_age_hours = (
            args.max_age_hours
            if args.max_age_hours is not None
            else publish_settings.production_current_pool_max_age_hours
        )
        report = _publish_current_pool_audit(
            args.audit_path,
            args.target_path or publish_settings.current_pool_audit_path,
            now=datetime.now(ZoneInfo("Asia/Shanghai")),
            max_age_hours=max_age_hours,
        )
        _print_json(report)
        return 0

    if args.command == "research-current-pool-fetch-jiaoch":
        report = fetch_jiaoch_current_pool_descriptor(
            as_of=args.as_of,
            output_dir=args.output_dir,
            timeout_seconds=args.timeout_seconds,
        )
        _print_json(report)
        return 0

    if args.command == "research-current-pool-fetch-risk-jiaoch":
        report = fetch_jiaoch_current_pool_risk_descriptor(
            as_of=args.as_of,
            universe_path=args.universe_path,
            output_dir=args.output_dir,
            timeout_seconds=args.timeout_seconds,
        )
        _print_json(report)
        return 0

    if args.command == "research-current-pool-build-history-summary":
        report = build_current_pool_history_summary(
            universe_path=args.universe_path,
            store_dir=args.store_dir,
            history_start=args.start_date,
            history_end=args.end_date,
            as_of=args.as_of,
            output_dir=args.output_dir,
        )
        _print_json(report)
        return 0

    if args.command == "research-current-pool-audit":
        universe_descriptor = _load_current_pool_descriptor(
            args.universe_path, "current-pool-universe-input/v1"
        )
        history_descriptor = _load_current_pool_descriptor(
            args.history_summary_path, "current-pool-history-summary/v1"
        )
        risk_descriptor = _load_current_pool_descriptor(
            args.risk_path, "current-pool-risk-input/v1"
        )
        if "descriptor_sha256" in history_descriptor["payload"]:
            try:
                verified_history = verify_current_pool_history_descriptor(
                    history_descriptor["payload"], universe_descriptor["payload"]
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("current-pool input descriptor rejected") from exc
            if (
                verified_history["descriptor_sha256"]
                != history_descriptor["descriptor_sha256"]
            ):
                raise ValueError("current-pool input descriptor rejected")
        if not (
            universe_descriptor["source_as_of"]
            == history_descriptor["source_as_of"]
            == risk_descriptor["source_as_of"]
        ):
            raise ValueError("current-pool input descriptor rejected")
        universe_items = _load_current_pool_universe(universe_descriptor)
        try:
            verified_risk = verify_current_pool_risk_descriptor(
                risk_descriptor["payload"], universe_descriptor["payload"]
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("current-pool input descriptor rejected") from exc
        if (
            risk_descriptor["payload"].get("universe_descriptor_sha256")
            != universe_descriptor["descriptor_sha256"]
        ):
            raise ValueError("current-pool input descriptor rejected")
        risk_by_symbol = {
            item["ts_code"]: item for item in risk_descriptor["payload"]["items"]
        }
        if set(risk_by_symbol) != verified_risk["symbols"]:
            raise ValueError("current-pool input descriptor rejected")
        merged_universe_items = []
        for item in universe_items:
            risk = risk_by_symbol[item["ts_code"]]
            merged = dict(item)
            merged["original_name"] = item.get("name")
            if risk["active_name"] is not None:
                merged["name"] = risk["active_name"]
            merged["risk_flags"] = {
                "is_st": risk["is_st"],
                "is_suspended": risk["is_suspended"],
            }
            merged_universe_items.append(merged)
        histories = _load_current_pool_history_summary(history_descriptor)
        audit = build_current_pool_coverage(
            merged_universe_items,
            histories,
            min_signal_bars=args.min_signal_bars,
        )
        classifications = {
            row["symbol"]: row
            for row in map(classify_current_pool_item, merged_universe_items)
        }
        for status in audit["item_history_status"]:
            status["signal_allowed_today"] = classifications[status["symbol"]][
                "signal_allowed_today"
            ]
        universe_symbols = {row["symbol"] for row in audit["item_history_status"]}
        history_symbols = {str(symbol).strip().upper().split(".", 1)[0] for symbol in histories}
        if not history_symbols.issubset(universe_symbols):
            raise ValueError("current-pool input descriptor rejected")
        audit.pop("canonical_sha256")
        audit.update(
            {
                "evidence_scope": "development_only",
                "source_as_of": universe_descriptor["source_as_of"],
                "source_ids": {
                    "universe": "jiaoch",
                    "history_summary": "jiaoch",
                    "risk_snapshot": "jiaoch",
                },
                "input_descriptor_sha256": {
                    "universe": universe_descriptor["descriptor_sha256"],
                    "history_summary": history_descriptor["descriptor_sha256"],
                    "risk_snapshot": risk_descriptor["descriptor_sha256"],
                },
                "risk_snapshot_complete": True,
                "risk_gate_passed": True,
                "production_recommendation_eligible": False,
                "universe_items": sorted(
                    merged_universe_items, key=lambda item: item["ts_code"]
                ),
            }
        )
        canonical = json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        audit["canonical_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        _print_json(_write_current_pool_audit(args.output_dir, audit))
        return 0

    if args.command == "research-backtest":
        payload = run_candidate_research_backtest(
            settings=settings,
            provider=DATA_PROVIDER,
            start_date=args.start_date,
            end_date=args.end_date,
            max_deep=args.max_deep,
            top_n=args.top_n,
            hold_days=args.hold_days,
            lookback_days=args.lookback_days,
            use_live_snapshot=args.live_snapshot,
            cache_dir=args.cache_dir,
            progress_every=args.progress_every,
            buy_only=args.buy_only,
            min_score=args.min_score,
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
            trailing_stop_pct=args.trailing_stop_pct,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            use_announcement_context=args.announcement_context,
            announcement_lookback_days=args.announcement_lookback_days,
            require_announcement_events=args.require_announcement_event,
            require_all_announcement_events=args.require_all_announcement_events,
            exclude_announcement_events=args.exclude_announcement_event,
            require_market_levels=args.require_market_level,
            require_signal_tags=args.require_signal_tag,
            require_all_signal_tags=args.require_all_signal_tags,
            exclude_signal_tags=args.exclude_signal_tag,
            min_prior_win_rate=args.min_prior_win_rate,
            min_prior_avg_return=args.min_prior_avg_return,
            max_prior_avg_adverse=args.max_prior_avg_adverse,
            use_margin_eligibility_context=args.margin_eligibility_context,
            include_qualified_trades=bool(
                args.include_qualified_trades or args.qualified_trades_output
            ),
        )
        if args.qualified_trades_output:
            payload["qualified_trades_output"] = _write_qualified_trades_payload(
                args.qualified_trades_output,
                payload,
            )
            if not args.include_qualified_trades:
                payload.pop("qualified_trades", None)
        _print_json(payload)
        return 0

    if args.command == "industry-history-check":
        provider = IndustryHistoryProvider(settings.industry_history_cache_dir)
        payload = provider.source_check(
            start_date=args.start_date,
            end_date=args.end_date or date.today().isoformat(),
            max_boards=args.max_boards,
        )
        _print_json(payload)
        return 0

    if args.command == "margin-eligibility-check":
        provider = MarginEligibilityProvider(
            settings.margin_eligibility_cache_path,
            settings.enable_margin_eligibility_context,
        )
        payload = provider.source_check(as_of=args.as_of)
        _print_json(payload)
        return 0

    if args.command == "research-historical-universe":
        payload = run_historical_universe_research_backtest(
            settings=settings,
            provider=DATA_PROVIDER,
            start_date=args.start_date,
            end_date=args.end_date,
            max_deep=args.max_deep,
            top_n=args.top_n,
            hold_days=args.hold_days,
            lookback_days=args.lookback_days,
            max_universe_symbols=args.max_universe_symbols,
            use_live_snapshot=args.live_snapshot,
            cache_dir=args.cache_dir,
            progress_every=args.progress_every,
            buy_only=args.buy_only,
            min_score=args.min_score,
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
            trailing_stop_pct=args.trailing_stop_pct,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            use_announcement_context=args.announcement_context,
            announcement_lookback_days=args.announcement_lookback_days,
            require_announcement_events=args.require_announcement_event,
            require_all_announcement_events=args.require_all_announcement_events,
            exclude_announcement_events=args.exclude_announcement_event,
            require_market_levels=args.require_market_level,
            require_signal_tags=args.require_signal_tag,
            require_all_signal_tags=args.require_all_signal_tags,
            exclude_signal_tags=args.exclude_signal_tag,
            min_prior_win_rate=args.min_prior_win_rate,
            min_prior_avg_return=args.min_prior_avg_return,
            max_prior_avg_adverse=args.max_prior_avg_adverse,
            use_margin_eligibility_context=args.margin_eligibility_context,
            include_qualified_trades=bool(
                args.include_qualified_trades or args.qualified_trades_output
            ),
            pit_universe_path=args.pit_universe_path,
            audited_pit_universe_path=args.audited_pit_universe_path,
            composite_pit_descriptor_path=args.composite_pit_descriptor_path,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            expected_artifact_root_sha256=args.expected_artifact_root_sha256,
            expected_composite_root_sha256=args.expected_composite_root_sha256,
            temporal_contract_path=args.temporal_contract_path,
            expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
            expected_temporal_role=args.expected_temporal_role,
        )
        if args.qualified_trades_output:
            payload["qualified_trades_output"] = _write_qualified_trades_payload(
                args.qualified_trades_output,
                payload,
            )
            if not args.include_qualified_trades:
                payload.pop("qualified_trades", None)
        _print_json(payload)
        return 0

    if args.command == "research-historical-sweep":
        payload = run_historical_universe_research_backtest(
            settings=settings,
            provider=DATA_PROVIDER,
            start_date=args.start_date,
            end_date=args.end_date,
            max_deep=args.max_deep,
            top_n=args.top_n,
            hold_days=args.hold_days,
            lookback_days=args.lookback_days,
            max_universe_symbols=args.max_universe_symbols,
            use_live_snapshot=args.live_snapshot,
            cache_dir=args.cache_dir,
            progress_every=args.progress_every,
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
            trailing_stop_pct=args.trailing_stop_pct,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            use_industry_rotation_context=args.industry_rotation_context,
            industry_rotation_max_boards=args.industry_rotation_max_boards,
            use_margin_eligibility_context=args.margin_eligibility_context,
            use_dragon_tiger_context=args.dragon_tiger_context,
            include_qualified_trades=True,
            pit_universe_path=args.pit_universe_path,
            audited_pit_universe_path=args.audited_pit_universe_path,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            expected_artifact_root_sha256=args.expected_artifact_root_sha256,
            temporal_contract_path=args.temporal_contract_path,
            expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
            expected_temporal_role=args.expected_temporal_role,
        )
        qualified_trades_output = (
            _write_qualified_trades_payload(args.qualified_trades_output, payload)
            if args.qualified_trades_output
            else None
        )
        sweep_payload = sweep_qualified_trades(
            payload.get("qualified_trades") or [],
            hold_days=args.hold_days,
            top_n=args.top_n,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            min_trades=args.min_trades,
            max_filter_size=args.max_filter_size,
            target_win_rate_pct=args.target_win_rate_pct,
            target_drawdown_pct=args.target_drawdown_pct,
            target_one_year_return_pct=args.target_one_year_return_pct,
            exposure_multipliers=_exposure_multipliers(args),
            annual_financing_rate_pct=args.annual_financing_rate_pct,
            roundtrip_cost_bps=args.roundtrip_cost_bps,
            slippage_bps=args.slippage_bps,
            capital_model=args.capital_model,
            required_signal_tags=_split_csv_arg(args.required_signal_tags),
            excluded_signal_tags=_split_csv_arg(args.excluded_signal_tags),
            market_levels=_split_csv_arg(args.market_levels),
            force_exposure_multipliers=_force_exposure_multipliers(args),
            pre_exit_calendar_gap_days=args.pre_exit_calendar_gap_days,
            prior_high_trailing_stop_pct=args.prior_high_trailing_stop_pct,
            prior_high_trailing_activation_pct=args.prior_high_trailing_activation_pct,
            partial_profit_activation_pct=args.partial_profit_activation_pct,
            partial_profit_fraction=args.partial_profit_fraction,
            correlation_threshold=args.correlation_threshold,
            correlation_lookback_days=args.correlation_lookback_days,
            correlation_cache_dir=args.correlation_cache_dir,
            correlation_min_periods=args.correlation_min_periods,
            correlation_history_lookback_days=args.correlation_history_lookback_days,
        )
        if args.compact:
            compact_payload = _compact_research_sweep_payload(
                payload, sweep_payload, args.output_limit
            )
            if qualified_trades_output:
                compact_payload["qualified_trades_output"] = qualified_trades_output
            _print_json(compact_payload)
            return 0
        _print_json(
            {
                "source_summary": payload.get("summary"),
                "qualified_trades_output": qualified_trades_output,
                "sweep": sweep_payload,
            }
        )
        return 0

    if args.command == "research-historical-hold-sweep":
        results = []
        for hold_days in _parse_hold_days_list(args.hold_days_list):
            cooldown_days = args.symbol_cooldown_days
            if cooldown_days is None:
                cooldown_days = hold_days
            payload = run_historical_universe_research_backtest(
                settings=settings,
                provider=DATA_PROVIDER,
                start_date=args.start_date,
                end_date=args.end_date,
                max_deep=args.max_deep,
                top_n=args.top_n,
                hold_days=hold_days,
                lookback_days=args.lookback_days,
                max_universe_symbols=args.max_universe_symbols,
                use_live_snapshot=args.live_snapshot,
                cache_dir=args.cache_dir,
                progress_every=args.progress_every,
                stop_loss_pct=args.stop_loss_pct,
                take_profit_pct=args.take_profit_pct,
                trailing_stop_pct=args.trailing_stop_pct,
                symbol_cooldown_days=cooldown_days,
                max_active_positions=args.max_active_positions,
                use_margin_eligibility_context=args.margin_eligibility_context,
                use_dragon_tiger_context=args.dragon_tiger_context,
                include_qualified_trades=True,
                pit_universe_path=args.pit_universe_path,
                audited_pit_universe_path=args.audited_pit_universe_path,
                expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
                expected_artifact_root_sha256=args.expected_artifact_root_sha256,
                temporal_contract_path=args.temporal_contract_path,
                expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
                expected_temporal_role=args.expected_temporal_role,
            )
            sweep_payload = sweep_qualified_trades(
                payload.get("qualified_trades") or [],
                hold_days=hold_days,
                top_n=args.top_n,
                symbol_cooldown_days=cooldown_days,
                max_active_positions=args.max_active_positions,
                min_trades=args.min_trades,
                max_filter_size=args.max_filter_size,
                target_win_rate_pct=args.target_win_rate_pct,
                target_drawdown_pct=args.target_drawdown_pct,
                target_one_year_return_pct=args.target_one_year_return_pct,
                exposure_multipliers=_exposure_multipliers(args),
                annual_financing_rate_pct=args.annual_financing_rate_pct,
                roundtrip_cost_bps=args.roundtrip_cost_bps,
                slippage_bps=args.slippage_bps,
                capital_model=args.capital_model,
                required_signal_tags=_split_csv_arg(args.required_signal_tags),
                excluded_signal_tags=_split_csv_arg(args.excluded_signal_tags),
                market_levels=_split_csv_arg(args.market_levels),
                force_exposure_multipliers=_force_exposure_multipliers(args),
                pre_exit_calendar_gap_days=args.pre_exit_calendar_gap_days,
                prior_high_trailing_stop_pct=args.prior_high_trailing_stop_pct,
                prior_high_trailing_activation_pct=args.prior_high_trailing_activation_pct,
                partial_profit_activation_pct=args.partial_profit_activation_pct,
                partial_profit_fraction=args.partial_profit_fraction,
                correlation_threshold=args.correlation_threshold,
                correlation_lookback_days=args.correlation_lookback_days,
                correlation_cache_dir=args.correlation_cache_dir,
                correlation_min_periods=args.correlation_min_periods,
                correlation_history_lookback_days=args.correlation_history_lookback_days,
            )
            results.append(
                _compact_hold_sweep_result(
                    hold_days,
                    payload,
                    sweep_payload,
                    output_limit=args.output_limit,
                )
            )
        results.sort(
            key=lambda item: (
                ((item.get("diagnostics") or {}).get("best_target_win_drawdown") or {}).get(
                    "rolling_1y_latest_return_pct"
                )
                or -999
            ),
            reverse=True,
        )
        _print_json(
            {
                "hold_days": _parse_hold_days_list(args.hold_days_list),
                "result_count": len(results),
                "results": results,
            }
        )
        return 0

    if args.command == "research-sweep":
        payload = run_candidate_research_backtest(
            settings=settings,
            provider=DATA_PROVIDER,
            start_date=args.start_date,
            max_deep=args.max_deep,
            top_n=args.top_n,
            hold_days=args.hold_days,
            lookback_days=args.lookback_days,
            cache_dir=args.cache_dir,
            progress_every=args.progress_every,
            stop_loss_pct=args.stop_loss_pct,
            take_profit_pct=args.take_profit_pct,
            trailing_stop_pct=args.trailing_stop_pct,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            use_margin_eligibility_context=args.margin_eligibility_context,
            include_qualified_trades=True,
        )
        qualified_trades_output = (
            _write_qualified_trades_payload(args.qualified_trades_output, payload)
            if args.qualified_trades_output
            else None
        )
        sweep_payload = sweep_qualified_trades(
            payload.get("qualified_trades") or [],
            hold_days=args.hold_days,
            top_n=args.top_n,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            min_trades=args.min_trades,
            max_filter_size=args.max_filter_size,
            target_win_rate_pct=args.target_win_rate_pct,
            target_drawdown_pct=args.target_drawdown_pct,
            target_one_year_return_pct=args.target_one_year_return_pct,
            exposure_multipliers=_exposure_multipliers(args),
            annual_financing_rate_pct=args.annual_financing_rate_pct,
            roundtrip_cost_bps=args.roundtrip_cost_bps,
            slippage_bps=args.slippage_bps,
            capital_model=args.capital_model,
            required_signal_tags=_split_csv_arg(args.required_signal_tags),
            excluded_signal_tags=_split_csv_arg(args.excluded_signal_tags),
            market_levels=_split_csv_arg(args.market_levels),
            force_exposure_multipliers=_force_exposure_multipliers(args),
            pre_exit_calendar_gap_days=args.pre_exit_calendar_gap_days,
            prior_high_trailing_stop_pct=args.prior_high_trailing_stop_pct,
            prior_high_trailing_activation_pct=args.prior_high_trailing_activation_pct,
            partial_profit_activation_pct=args.partial_profit_activation_pct,
            partial_profit_fraction=args.partial_profit_fraction,
            correlation_threshold=args.correlation_threshold,
            correlation_lookback_days=args.correlation_lookback_days,
            correlation_cache_dir=args.correlation_cache_dir,
            correlation_min_periods=args.correlation_min_periods,
            correlation_history_lookback_days=args.correlation_history_lookback_days,
        )
        if args.compact:
            compact_payload = _compact_research_sweep_payload(
                payload, sweep_payload, args.output_limit
            )
            if qualified_trades_output:
                compact_payload["qualified_trades_output"] = qualified_trades_output
            _print_json(compact_payload)
            return 0
        _print_json(
            {
                "source_summary": payload.get("summary"),
                "qualified_trades_output": qualified_trades_output,
                "sweep": sweep_payload,
            }
        )
        return 0

    if args.command == "research-sweep-file":
        temporal_contract = load_temporal_partition_contract(args.temporal_contract_path)
        if temporal_contract["contract_sha256"] != args.expected_temporal_contract_sha256:
            raise ValueError("expected temporal contract hash mismatch")
        if args.expected_temporal_role != "development":
            raise ValueError("ordinary sweep requires temporal role development")
        assert_range_allowed(
            temporal_contract,
            args.expected_temporal_role,
            args.start_date,
            args.end_date,
            "backtest",
        )
        if args.correlation_threshold is not None and float(args.correlation_threshold) != 0.0:
            raise ValueError("frozen sweep forbids legacy correlation cache reads")
        audited_universe = AuditedPointInTimeUniverse.from_file(
            args.audited_pit_universe_path,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            expected_artifact_root_sha256=args.expected_artifact_root_sha256,
            expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
            expected_temporal_role=args.expected_temporal_role,
        )
        try:
            payload = _load_qualified_trades_payload(args.qualified_trades_path)
            source_summary = payload.get("summary") or {}
            source_contract = source_summary.get("research_data_contract") or {}
            if (
                source_summary.get("artifact_root_sha256")
                or source_contract.get("artifact_root_sha256")
            ) != audited_universe.artifact_root_sha256:
                raise ValueError("qualified payload artifact root mismatch")
            if (
                source_summary.get("coverage_audit_sha256")
                or source_contract.get("coverage_audit_sha256")
            ) != audited_universe.coverage_audit_sha256:
                raise ValueError("qualified payload coverage audit mismatch")
            qualified_trades = payload.get("qualified_trades") or []
            validate_point_in_time_contract(
                source_summary,
                qualified_trades,
                artifact_base_dir=str(Path(args.qualified_trades_path).resolve().parent),
                declared_start_date=args.start_date,
                declared_end_date=args.end_date,
                audited_universe=audited_universe,
            )
        finally:
            audited_universe.close()
        sweep_payload = sweep_qualified_trades(
            qualified_trades,
            hold_days=args.hold_days,
            top_n=args.top_n,
            symbol_cooldown_days=args.symbol_cooldown_days,
            max_active_positions=args.max_active_positions,
            min_trades=args.min_trades,
            max_filter_size=args.max_filter_size,
            target_win_rate_pct=args.target_win_rate_pct,
            target_drawdown_pct=args.target_drawdown_pct,
            target_one_year_return_pct=args.target_one_year_return_pct,
            exposure_multipliers=_exposure_multipliers(args),
            annual_financing_rate_pct=args.annual_financing_rate_pct,
            roundtrip_cost_bps=args.roundtrip_cost_bps,
            slippage_bps=args.slippage_bps,
            capital_model=args.capital_model,
            required_signal_tags=_split_csv_arg(args.required_signal_tags),
            excluded_signal_tags=_split_csv_arg(args.excluded_signal_tags),
            market_levels=_split_csv_arg(args.market_levels),
            force_exposure_multipliers=_force_exposure_multipliers(args),
            pre_exit_calendar_gap_days=args.pre_exit_calendar_gap_days,
            prior_high_trailing_stop_pct=args.prior_high_trailing_stop_pct,
            prior_high_trailing_activation_pct=args.prior_high_trailing_activation_pct,
            partial_profit_activation_pct=args.partial_profit_activation_pct,
            partial_profit_fraction=args.partial_profit_fraction,
            correlation_threshold=args.correlation_threshold,
            correlation_lookback_days=args.correlation_lookback_days,
            correlation_cache_dir=args.correlation_cache_dir,
            correlation_min_periods=args.correlation_min_periods,
            correlation_history_lookback_days=args.correlation_history_lookback_days,
        )
        source_payload = {"summary": payload.get("summary") or {}}
        if args.compact:
            compact_payload = _compact_research_sweep_payload(
                source_payload, sweep_payload, args.output_limit
            )
            compact_payload["qualified_trades_path"] = args.qualified_trades_path
            _print_json(compact_payload)
            return 0
        _print_json(
            {
                "source_summary": payload.get("summary") or {},
                "qualified_trades_path": args.qualified_trades_path,
                "sweep": sweep_payload,
            }
        )
        return 0

    if args.command == "research-validate-file":
        temporal_contract = load_temporal_partition_contract(args.temporal_contract_path)
        if temporal_contract["contract_sha256"] != args.expected_temporal_contract_sha256:
            raise ValueError("expected temporal contract hash mismatch")
        if args.expected_temporal_role != "development":
            raise ValueError("ordinary validation requires temporal role development")
        assert_range_allowed(
            temporal_contract,
            args.expected_temporal_role,
            args.start_date,
            args.end_date,
            "validate",
        )
        if args.final_oos_start != "2026-07-13":
            raise ValueError("final_oos_start must equal frozen boundary 2026-07-13")
        strategy = {
            "hold_days": args.hold_days,
            "top_n": args.top_n,
            "symbol_cooldown_days": args.symbol_cooldown_days,
            "max_active_positions": args.max_active_positions,
            "required_signal_tags": _split_csv_arg(args.required_signal_tags) or [],
            "excluded_signal_tags": _split_csv_arg(args.excluded_signal_tags) or [],
            "market_levels": _split_csv_arg(args.market_levels) or [],
            "exposure_multiplier": args.exposure_multiplier,
            "annual_financing_rate_pct": args.annual_financing_rate_pct,
            "roundtrip_cost_bps": args.roundtrip_cost_bps,
            "slippage_bps": args.slippage_bps,
            "capital_model": args.capital_model,
            "target_win_rate_pct": args.target_win_rate_pct,
            "target_drawdown_pct": args.target_drawdown_pct,
            "target_one_year_return_pct": args.target_one_year_return_pct,
            "pre_exit_calendar_gap_days": args.pre_exit_calendar_gap_days,
            "partial_profit_activation_pct": args.partial_profit_activation_pct,
            "partial_profit_fraction": args.partial_profit_fraction,
            "correlation_threshold": args.correlation_threshold,
            "correlation_lookback_days": args.correlation_lookback_days,
        }
        validation = {
            "train_days": args.train_days,
            "validation_days": args.validation_days,
            "step_days": args.step_days,
            "embargo_days": args.embargo_days,
            "final_oos_start": args.final_oos_start,
            "minimum_oos_trades": args.minimum_oos_trades,
        }
        qualified_descriptor = {
            "basename": Path(args.qualified_trades_path).name,
            "sha256": None,
        }
        registered_event = append_experiment_event(
            args.ledger_path,
            {
                "event_id": f"{args.experiment_id}:registered",
                "experiment_id": args.experiment_id,
                "event_type": "registered",
                "hypothesis": args.hypothesis,
                "expected_mechanism": args.expected_mechanism,
                "single_change": "strict_purged_walk_forward_validation",
                "falsification_criterion": args.falsification_criterion,
                "exit_criterion": args.exit_criterion,
                "qualified_trades_artifact": qualified_descriptor,
                "strategy": strategy,
                "validation": validation,
            },
        )
        audited_universe = None
        try:
            audited_universe = AuditedPointInTimeUniverse.from_file(
                args.audited_pit_universe_path,
                expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
                expected_artifact_root_sha256=args.expected_artifact_root_sha256,
                expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
                expected_temporal_role=args.expected_temporal_role,
            )
            qualified_path = Path(args.qualified_trades_path)
            if qualified_path.stat().st_size > 64 * 1024 * 1024:
                raise ValueError("qualified trades payload exceeds size limit")
            qualified_sha256 = hashlib.sha256(qualified_path.read_bytes()).hexdigest()
            payload = _load_qualified_trades_payload(args.qualified_trades_path)
            source_summary = payload.get("summary") or {}
            source_contract = source_summary.get("research_data_contract") or {}
            if (
                source_summary.get("artifact_root_sha256")
                or source_contract.get("artifact_root_sha256")
            ) != audited_universe.artifact_root_sha256:
                raise ValueError("expected artifact root hash mismatch")
            if (
                source_summary.get("coverage_audit_sha256")
                or source_contract.get("coverage_audit_sha256")
            ) != audited_universe.coverage_audit_sha256:
                raise ValueError("expected coverage audit hash mismatch")
            qualified_trades = payload.get("qualified_trades") or []
            data_contract = validate_point_in_time_contract(
                source_summary,
                qualified_trades,
                artifact_base_dir=str(Path(args.qualified_trades_path).resolve().parent),
                declared_start_date=args.start_date,
                declared_end_date=args.end_date,
                audited_universe=audited_universe,
            )
            verified_authority = data_contract["verified_authority"]
            if verified_authority["artifact_root_sha256"] != args.expected_artifact_root_sha256:
                raise ValueError("verified artifact root hash mismatch")
            if verified_authority["coverage_audit_sha256"] != args.expected_coverage_audit_sha256:
                raise ValueError("verified coverage audit hash mismatch")
            report = run_frozen_strategy_validation(
                qualified_trades,
                strategy=strategy,
                validation=validation,
            )
            report_output = {
                "experiment_id": args.experiment_id,
                "data_contract": data_contract,
                **report,
            }
            report_artifact = write_report_artifact(args.artifact_dir, report_output)
            native_evidence = None
            native_evidence_artifact = None
            native_authority_keys = {
                "artifact_root_sha256",
                "coverage_audit_sha256",
                "temporal_contract_sha256",
                "temporal_role",
                "artifact_manifest_sha256",
                "market_generation_root_sha256",
                "stock_generation_lineage_sha256",
            }
            if native_authority_keys.issubset(data_contract.get("verified_authority") or {}):
                native_evidence = build_artifact_native_evidence(
                    audited_universe=audited_universe,
                    qualified_trades=qualified_trades,
                    qualified_trades_path=str(qualified_path.resolve()),
                    artifact_root=str(qualified_path.resolve().parent),
                )
                native_evidence_artifact = write_artifact_native_evidence(
                    args.artifact_dir, native_evidence
                )
                native_evidence = verify_artifact_native_evidence(
                    native_evidence_artifact["path"],
                    audited_universe=audited_universe,
                    artifact_root=str(qualified_path.resolve().parent),
                )
            native_eligibility = (native_evidence or {}).get("eligibility") or {}
            selection_replay = report.get("strategy_selection_replay") or {}
            selection_replay_bound = (
                selection_replay.get("bound") is True
                and selection_replay.get("selection_receipt_bound") is True
                and selection_replay.get("candidate_pool_sha256") == report.get("dataset_sha256")
                and selection_replay.get("strategy_sha256") == report.get("strategy_sha256")
            )
            evidence = {
                "pit_contract": bool(data_contract.get("verified_authority")),
                "temporal_contract": bool(
                    data_contract.get("verified_authority", {}).get("temporal_contract_sha256")
                ),
                "cost_slippage": float(strategy.get("roundtrip_cost_bps", 0)) > 0
                and float(strategy.get("slippage_bps", 0)) >= 0,
                "artifact_execution": bool(native_eligibility.get("qualified_trade_lineage_bound")),
                "strategy_signal_replay": bool(
                    native_eligibility.get("strategy_signal_replay_bound")
                ),
                "strategy_entry_decision": bool(
                    native_eligibility.get("strategy_entry_decision_bound")
                ),
                "strategy_selection_replay": selection_replay_bound,
                "outcome_replay": bool(native_eligibility.get("outcome_replay_bound")),
                "double_cost": False,
                "regime": False,
                "final_oos": report.get("final_oos", {}).get("status") == "loaded",
                "shadow": False,
                "live_monitoring": False,
                "pit_verified": bool(data_contract.get("verified_authority")),
                "data_contract": data_contract,
                "artifact_native_evidence": native_evidence_artifact,
            }
            profile_receipt = build_profile_evidence_receipt(
                experiment_id=args.experiment_id,
                strategy=strategy,
                validation=validation,
                validation_report=report,
                rolling_12m=(report.get("aggregate_validation") or {}).get("rolling_1y_windows")
                or [],
                evidence=evidence,
                source_artifact={
                    "path": str(Path(args.qualified_trades_path).resolve()),
                    "sha256": qualified_sha256,
                    "bytes": qualified_path.stat().st_size,
                },
                report_artifact=report_artifact,
                ledger_anchor={
                    "event_id": registered_event.get("event_id"),
                    "sequence": registered_event.get("sequence"),
                    "record_hash": registered_event.get("record_hash"),
                },
            )
            profile_receipt_artifact = write_report_artifact(args.artifact_dir, profile_receipt)
        except BaseException as exc:
            terminal_event = "failed" if isinstance(exc, Exception) else "aborted"
            error_code = (
                "VALIDATION_ABORTED"
                if terminal_event == "aborted"
                else "VALIDATION_INPUT_REJECTED"
                if isinstance(exc, ValueError)
                else "VALIDATION_INTERNAL_ERROR"
            )
            append_experiment_event(
                args.ledger_path,
                {
                    "event_id": f"{args.experiment_id}:{terminal_event}",
                    "experiment_id": args.experiment_id,
                    "event_type": terminal_event,
                    "error_code": error_code,
                    "error_type": type(exc).__name__,
                    "message": "validation aborted"
                    if terminal_event == "aborted"
                    else "validation failed",
                },
            )
            raise
        finally:
            if audited_universe is not None:
                audited_universe.close()
        append_experiment_event(
            args.ledger_path,
            {
                "event_id": f"{args.experiment_id}:completed",
                "experiment_id": args.experiment_id,
                "event_type": "completed",
                "qualified_trades_artifact": {
                    "basename": qualified_descriptor["basename"],
                    "sha256": qualified_sha256,
                },
                "dataset_sha256": report["dataset_sha256"],
                "strategy_sha256": report["strategy_sha256"],
                "validation_sha256": report["validation_sha256"],
                "data_contract": data_contract,
                "report_artifact": report_artifact,
                "profile_evidence_receipt": profile_receipt_artifact,
                "qualification": report["qualification"],
                "aggregate_validation": report["aggregate_validation"],
                "final_oos": report["final_oos"],
            },
        )
        _print_json(
            {
                "ledger_path": args.ledger_path,
                "report_artifact": report_artifact,
                "profile_evidence_receipt": profile_receipt_artifact,
                **report_output,
            }
        )
        return 0

    if args.command == "research-build-pit-universe":
        source_manifest = read_json(args.source_manifest_path, {})
        if not isinstance(source_manifest, dict):
            raise ValueError("source manifest must be a JSON object")
        payload = build_pit_universe_payload(
            security_master=_load_json_rows(
                args.security_master_path, "items", "security_master", "data"
            ),
            trade_calendar=_load_json_rows(
                args.trade_calendar_path, "items", "sessions", "trade_calendar", "data"
            ),
            daily_universe=_load_json_rows(
                args.daily_universe_path, "items", "daily_universe", "data"
            ),
            source_manifest=source_manifest,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        artifact = write_pit_universe_artifact(args.output_dir, payload)
        _print_json(
            {
                "artifact": artifact,
                "coverage": payload["coverage"],
                "hashes": payload["hashes"],
                "quality": payload["quality"],
            }
        )
        return 0

    if args.command == "research-pit-ingest-response":
        try:
            params = json.loads(args.params_json)
        except json.JSONDecodeError as exc:
            raise ValueError("--params-json must be valid JSON") from exc
        if not isinstance(params, dict):
            raise ValueError("--params-json must decode to an object")
        raw_response_path = Path(args.raw_response_path)
        if raw_response_path.stat().st_size > MAX_RAW_BYTES[args.dataset]:
            raise ValueError("raw response file exceeds the dataset byte limit")
        receipt = PITReceiptStore(args.store_dir).ingest_tushare_response(
            dataset=args.dataset,
            partition_key=args.partition_key,
            endpoint=args.endpoint,
            params=params,
            raw_bytes=raw_response_path.read_bytes(),
            http_status=args.http_status,
            retrieved_at=args.retrieved_at,
            row_cap=args.row_cap,
        )
        _print_json(receipt)
        return 0

    if args.command == "research-pit-ingest-cninfo-suspension":

        def read_pdf(path_value, label):
            path = Path(path_value)
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"{label} PDF path is missing or unsafe")
            if path.stat().st_size > MAX_CNINFO_PDF_BYTES:
                raise ValueError(f"{label} PDF exceeds the official evidence byte limit")
            return path.read_bytes()

        interval = PITReceiptStore(args.store_dir).ingest_cninfo_suspension_interval(
            ts_code=args.ts_code,
            start_raw_bytes=read_pdf(args.start_pdf_path, "start"),
            start_source_url=args.start_source_url,
            start_published_at=args.start_published_at,
            start_retrieved_at=args.start_retrieved_at,
            resume_raw_bytes=read_pdf(args.resume_pdf_path, "resume"),
            resume_source_url=args.resume_source_url,
            resume_published_at=args.resume_published_at,
            resume_retrieved_at=args.resume_retrieved_at,
        )
        _print_json(interval)
        return 0

    if args.command == "research-pit-audit-store":
        exchanges = tuple(
            item.strip().upper() for item in str(args.calendar_exchanges).split(",") if item.strip()
        )
        audit = PITReceiptStore(args.store_dir).audit_coverage(
            start_date=args.start_date,
            end_date=args.end_date,
            calendar_exchanges=exchanges,
        )
        _print_json(audit)
        return 0

    if args.command == "research-pit-publish-universe":
        temporal_contract = load_temporal_partition_contract(args.temporal_contract_path)
        assert_range_allowed(
            temporal_contract,
            args.temporal_role,
            args.start_date,
            args.end_date,
            "publish",
        )
        artifact = PITReceiptStore(args.store_dir).publish_universe_artifact(
            args.output_dir,
            start_date=args.start_date,
            end_date=args.end_date,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            temporal_contract_sha256=temporal_contract["contract_sha256"],
            temporal_role=args.temporal_role,
            permitted_operation="publish",
            promotion_eligible=False,
        )
        _print_json(artifact)
        return 0

    if args.command == "research-build-evidence-bundle":
        output_root = Path(args.output_dir).resolve()
        pit_path = Path(args.pit_universe_path).resolve()
        market_path = Path(args.market_data_manifest_path).resolve()
        try:
            pit_path.relative_to(output_root)
            market_path.relative_to(output_root)
        except ValueError as exc:
            raise ValueError(
                "PIT universe and market manifest must be inside --output-dir"
            ) from exc
        audited_universe = AuditedPointInTimeUniverse.from_file(
            args.audited_pit_universe_path,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            expected_artifact_root_sha256=args.expected_artifact_root_sha256,
            expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
            expected_temporal_role=args.expected_temporal_role,
        )
        try:
            authority = audited_authority_from_universe(audited_universe)
            evidence = write_research_evidence_bundle(
                str(output_root),
                pit_universe_path=str(pit_path),
                market_data_manifest_path=str(market_path),
                audited_authority=authority,
            )
            verified = verify_research_evidence_bundle(evidence["path"])
        finally:
            audited_universe.close()
        _print_json(
            {
                "status": "development_integrity_only",
                "strict_validation_eligible": False,
                "reason": "evidence bundle still contains unresolved component and lineage eligibility reasons",
                "audited_authority": authority,
                "evidence": {
                    "path": evidence["path"],
                    "evidence_bundle_sha256": verified["evidence_bundle_sha256"],
                    "schema_version": verified["schema_version"],
                    "coverage": verified["coverage"],
                    "eligibility": verified["eligibility"],
                },
            }
        )
        return 0

    if args.command == "research-build-artifact-native-evidence":
        qualified_path = Path(args.qualified_trades_path).resolve()
        qualified_payload = _load_qualified_trades_payload(str(qualified_path))
        qualified_trades = qualified_payload.get("qualified_trades") or []
        audited_universe = AuditedPointInTimeUniverse.from_file(
            args.audited_pit_universe_path,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            expected_artifact_root_sha256=args.expected_artifact_root_sha256,
            expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
            expected_temporal_role=args.expected_temporal_role,
        )
        try:
            evidence_payload = build_artifact_native_evidence(
                audited_universe=audited_universe,
                qualified_trades=qualified_trades,
                qualified_trades_path=str(qualified_path),
                artifact_root=str(qualified_path.parent),
            )
            descriptor = write_artifact_native_evidence(args.output_dir, evidence_payload)
            verified = verify_artifact_native_evidence(
                descriptor["path"],
                audited_universe=audited_universe,
                artifact_root=str(qualified_path.parent),
            )
        finally:
            audited_universe.close()
        native_development_eligible = (verified.get("eligibility") or {}).get(
            "eligible_for_development_validation"
        ) is True
        _print_json(
            {
                "status": (
                    "development_native_evidence_complete"
                    if native_development_eligible
                    else "development_execution_integrity_only"
                ),
                "ready_for_strict_compilation": native_development_eligible,
                # Native evidence alone is intentionally not the data contract;
                # the next compiler step must bind it into a v3 strict bundle.
                "strict_validation_eligible": False,
                "artifact_root": str(qualified_path.parent),
                "evidence": descriptor,
                "verified": bool(verified.get("verified")),
                "eligibility": verified.get("eligibility"),
                "strategy_signal_replay": verified.get("strategy_signal_replay"),
                "trade_lineage_sha256": verified.get("trade_lineage_sha256"),
            }
        )
        return 0

    if args.command == "research-build-strict-evidence-bundle":
        output_root = Path(args.output_dir).resolve()
        native_path = Path(args.artifact_native_evidence_path).resolve()
        try:
            native_path.relative_to(output_root)
        except ValueError as exc:
            raise ValueError("artifact-native evidence must be inside --output-dir") from exc
        audited_universe = AuditedPointInTimeUniverse.from_file(
            args.audited_pit_universe_path,
            expected_coverage_audit_sha256=args.expected_coverage_audit_sha256,
            expected_artifact_root_sha256=args.expected_artifact_root_sha256,
            expected_temporal_contract_sha256=args.expected_temporal_contract_sha256,
            expected_temporal_role=args.expected_temporal_role,
        )
        try:
            descriptor = write_strict_research_evidence_bundle(
                str(output_root),
                audited_universe=audited_universe,
                artifact_native_evidence_path=str(native_path),
            )
            verified = verify_research_evidence_bundle(
                descriptor["path"],
                audited_universe=audited_universe,
                artifact_root=str(output_root),
            )
            native_qualified_path = (
                output_root / verified["artifact_native_evidence"]["qualified_trades"]["path"]
            )
            compiled_qualified = write_strict_qualified_trades_payload(
                str(output_root),
                source_payload_path=str(native_qualified_path),
                strict_evidence_bundle_path=descriptor["path"],
                audited_universe=audited_universe,
            )
        finally:
            audited_universe.close()
        _print_json(
            {
                "status": "development_validation_eligible",
                "eligible_for_development_validation": True,
                "eligible_for_final_validation": False,
                "final_oos_eligible": False,
                "live_proof": False,
                "automatic_order_submission": False,
                "evidence": descriptor,
                "compiled_qualified_trades": compiled_qualified,
                "validation_verified": compiled_qualified["validation_verified"],
                "eligibility": verified["eligibility"],
                "audited_authority": verified["audited_authority"],
                "qualified_trades_sha256": verified["qualified_trades_sha256"],
                "qualified_trade_lineage_sha256": verified["qualified_trade_lineage_sha256"],
            }
        )
        return 0

    if args.command in {
        "research-pit-fetch-tushare",
        "research-pit-fetch-calendars",
        "research-current-pool-fetch-market-jiaoch",
    }:
        temporal_contract = load_temporal_partition_contract(args.temporal_contract_path)
        assert_range_allowed(
            temporal_contract,
            args.temporal_role,
            args.start_date,
            args.end_date,
            "collect",
        )
        source = resolve_tushare_source(
            args.source_profile,
            api_url=args.api_url,
            allow_insecure_http=args.allow_insecure_official_http,
        )
        collector = ControlledTushareCollector(
            store=PITReceiptStore(args.store_dir),
            token=source.token,
            api_url=source.api_url,
            transport=UrllibTushareTransport(proxy_url=source.proxy_url),
            clock=SystemTrustedClock(),
            max_attempts=args.max_attempts,
            timeout_s=args.timeout_seconds,
            allow_insecure_http=args.allow_insecure_official_http,
            allowed_hosts=source.allowed_hosts,
            source_profile=source.name,
            request_protocol=source.request_protocol,
            row_cap_overrides=dict(source.row_cap_overrides),
            network_route=source.network_route,
            proxy_endpoint=source.proxy_url,
            temporal_contract=temporal_contract,
            temporal_role=args.temporal_role,
            temporal_contract_sha256=temporal_contract["contract_sha256"],
            temporal_start_date=args.start_date,
            temporal_end_date=args.end_date,
            workers=args.workers,
        )
        if args.command == "research-pit-fetch-calendars":
            report = collector.collect_trade_calendars(
                start_date=args.start_date,
                end_date=args.end_date,
                resume=not args.no_resume,
            )
        elif args.command == "research-current-pool-fetch-market-jiaoch":
            report = collector.collect_current_pool_market(
                start_date=args.start_date,
                end_date=args.end_date,
                resume=not args.no_resume,
                batch_size=args.batch_size,
                progress_path=(Path(args.progress_path) if args.progress_path else None),
            )
        else:
            report = collector.collect(
                start_date=args.start_date,
                end_date=args.end_date,
                resume=not args.no_resume,
                workers=args.workers,
            )
        _print_json(report)
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
