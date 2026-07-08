import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

from app.a_share_universe import select_deep_scan_candidates
from app.config import get_settings
from app.industry_history import IndustryHistoryProvider
from app.logging_setup import configure_logging
from app.main import DATA_PROVIDER, DISCLAIMER
from app.margin_eligibility import MarginEligibilityProvider
from app.recommendations import RUN_SLOT_AUTO, RUN_SLOT_CONTEXTS, RecommendationService
from app.research_backtest import run_candidate_research_backtest, run_historical_universe_research_backtest
from app.research_sweep import sweep_qualified_trades
from app.storage import read_json, write_json


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


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
        "signal_days",
        "trade_win_rate_pct",
        "trade_avg_return_pct",
        "portfolio_compounded_return_pct",
        "portfolio_max_drawdown_pct",
        "rolling_1y_latest_return_pct",
        "rolling_1y_latest_trade_count",
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
        "industry_count": len({item.get("industry") for item in candidates if item.get("industry")}),
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


def main() -> int:
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
    historical.add_argument("--max-deep", type=int, default=80)
    historical.add_argument("--top-n", type=int, default=10)
    historical.add_argument("--hold-days", type=int, default=10)
    historical.add_argument("--lookback-days", type=int, default=620)
    historical.add_argument("--max-universe-symbols", type=int, default=300)
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
    historical_sweep.add_argument("--max-deep", type=int, default=80)
    historical_sweep.add_argument("--top-n", type=int, default=10)
    historical_sweep.add_argument("--hold-days", type=int, default=10)
    historical_sweep.add_argument("--lookback-days", type=int, default=620)
    historical_sweep.add_argument("--max-universe-symbols", type=int, default=300)
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
    historical_sweep.add_argument("--target-win-rate-pct", type=float, default=70.0)
    historical_sweep.add_argument("--target-drawdown-pct", type=float, default=5.0)
    historical_sweep.add_argument("--target-one-year-return-pct", type=float, default=200.0)
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
    historical_sweep.add_argument("--correlation-cache-dir", default="data/research_cache")
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
    historical_hold_sweep.add_argument("--max-deep", type=int, default=80)
    historical_hold_sweep.add_argument("--top-n", type=int, default=10)
    historical_hold_sweep.add_argument("--hold-days-list", default="3,5,7,10")
    historical_hold_sweep.add_argument("--lookback-days", type=int, default=620)
    historical_hold_sweep.add_argument("--max-universe-symbols", type=int, default=300)
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
    historical_hold_sweep.add_argument("--target-win-rate-pct", type=float, default=70.0)
    historical_hold_sweep.add_argument("--target-drawdown-pct", type=float, default=5.0)
    historical_hold_sweep.add_argument("--target-one-year-return-pct", type=float, default=200.0)
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
    historical_hold_sweep.add_argument("--prior-high-trailing-activation-pct", type=float, default=0.0)
    historical_hold_sweep.add_argument("--partial-profit-activation-pct", type=float, default=None)
    historical_hold_sweep.add_argument("--partial-profit-fraction", type=float, default=0.0)
    historical_hold_sweep.add_argument("--correlation-threshold", type=float, default=None)
    historical_hold_sweep.add_argument("--correlation-lookback-days", type=int, default=60)
    historical_hold_sweep.add_argument("--correlation-min-periods", type=int, default=20)
    historical_hold_sweep.add_argument("--correlation-history-lookback-days", type=int, default=620)
    historical_hold_sweep.add_argument("--correlation-cache-dir", default="data/research_cache")
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
    sweep.add_argument("--target-win-rate-pct", type=float, default=70.0)
    sweep.add_argument("--target-drawdown-pct", type=float, default=5.0)
    sweep.add_argument("--target-one-year-return-pct", type=float, default=200.0)
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
    sweep_file.add_argument("--top-n", type=int, default=10)
    sweep_file.add_argument("--hold-days", type=int, default=10)
    sweep_file.add_argument("--symbol-cooldown-days", type=int, default=10)
    sweep_file.add_argument("--max-active-positions", type=int, default=10)
    sweep_file.add_argument("--min-trades", type=int, default=20)
    sweep_file.add_argument("--max-filter-size", type=int, default=3)
    sweep_file.add_argument("--required-signal-tags", default=None)
    sweep_file.add_argument("--excluded-signal-tags", default=None)
    sweep_file.add_argument("--market-levels", default=None)
    sweep_file.add_argument("--target-win-rate-pct", type=float, default=70.0)
    sweep_file.add_argument("--target-drawdown-pct", type=float, default=5.0)
    sweep_file.add_argument("--target-one-year-return-pct", type=float, default=200.0)
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
    sweep_file.add_argument("--correlation-cache-dir", default="data/research_cache")
    sweep_file.add_argument(
        "--capital-model",
        choices=["signal-day", "slot-exit", "slot-daily"],
        default="signal-day",
    )
    sweep_file.add_argument("--output-limit", type=int, default=12)
    sweep_file.add_argument("--compact", action="store_true")

    args = parser.parse_args()
    settings = get_settings()
    service = RecommendationService(settings, DATA_PROVIDER, DISCLAIMER)

    if args.command == "generate-recommendations":
        payload = service.generate_daily_recommendations(
            force=args.force,
            max_deep=args.max_deep,
            run_slot=args.run_slot,
        )
        _print_json(
            {
                "generated_at": payload.get("generated_at"),
                "trade_date": payload.get("trade_date"),
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

    if args.command == "research-backtest":
        payload = run_candidate_research_backtest(
            settings=settings,
            provider=DATA_PROVIDER,
            start_date=args.start_date,
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
            include_qualified_trades=bool(args.include_qualified_trades or args.qualified_trades_output),
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
            include_qualified_trades=bool(args.include_qualified_trades or args.qualified_trades_output),
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
            compact_payload = _compact_research_sweep_payload(payload, sweep_payload, args.output_limit)
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
            compact_payload = _compact_research_sweep_payload(payload, sweep_payload, args.output_limit)
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
        payload = _load_qualified_trades_payload(args.qualified_trades_path)
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
        source_payload = {"summary": payload.get("summary") or {}}
        if args.compact:
            compact_payload = _compact_research_sweep_payload(source_payload, sweep_payload, args.output_limit)
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

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
