#!/usr/bin/env python3
"""Path A 3y Jiaoch-only clean replay: diagnose, refill, rebuild QT, replay P0.

Network-unstable safe: refill is resumable, per-symbol retried, and never
writes into the mixed legacy research_cache JSON directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.current_pool import classify_current_pool_item
from app.factor_v3_path_a_p0_drawdown_overlay import (
    build_path_a_p0_drawdown_overlay,
    write_path_a_p0_drawdown_overlay,
)
from app.jiaoch_live_market import (
    JIAOCH_DAILY_CACHE_SOURCE_VERSION,
    JiaochMarketDataProvider,
    is_jiaoch_stk_mins_v2_source,
)
from app.research_backtest import MARKET_PROXY_SYMBOLS, run_candidate_research_backtest
from app.research_goal_contract import (
    DOWNSTREAM_ELIGIBLE_SEGMENTS,
    name_is_downstream_excluded,
)
from app.config import Settings
from app.storage import write_json

STAGE_GOAL_ID = "path-a-3y-clean-replay/v1"
START_DATE = "2023-07-03"
END_DATE = "2026-07-03"
LOOKBACK_DAYS = 1400
DEFAULT_CACHE_DIR = Path("data/research_cache/jiaoch_stk_mins_3y_v2")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_3y_clean_replay")
DEFAULT_QT_PATH = Path("data/research_cache/qualified_hold5_stop5_3y_jiaoch.json")
LEGACY_QT_PATH = Path("data/research_cache/qualified_hold5_stop5.json")
AUDIT_PATH = Path("data/current_pool_audit.json")


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _segment_from_ts_code(ts_code: str) -> str | None:
    ts = str(ts_code or "")
    if ts.endswith(".BJ"):
        return "BSE"
    if ts.startswith("688") or ts.startswith("689"):
        return "SSE_STAR"
    if ts.startswith("60") and ts.endswith(".SH"):
        return "SSE_MAIN"
    if ts.startswith("30") and ts.endswith(".SZ"):
        return "SZSE_CHINEXT"
    if ts.startswith("00") and ts.endswith(".SZ"):
        return "SZSE_MAIN"
    return None


def load_eligible_universe(repo: Path) -> list[dict[str, Any]]:
    payload = json.loads((repo / AUDIT_PATH).read_text(encoding="utf-8"))
    status = {row["symbol"]: row for row in payload.get("item_history_status") or []}
    out: list[dict[str, Any]] = []
    for item in payload.get("universe_items") or []:
        classified = classify_current_pool_item(item)
        symbol = str(classified.get("symbol") or item.get("symbol") or "")
        name = str(item.get("name") or "")
        ts_code = str(item.get("ts_code") or "")
        if name_is_downstream_excluded(name):
            continue
        segment = _segment_from_ts_code(ts_code)
        if segment not in DOWNSTREAM_ELIGIBLE_SEGMENTS:
            continue
        row_status = status.get(symbol) or {}
        if not row_status.get("eligible") or not row_status.get("signal_ready"):
            continue
        out.append(
            {
                "symbol": symbol,
                "ts_code": ts_code,
                "name": name,
                "market": "a",
                "segment": segment,
            }
        )
    return out


def load_traded_symbols(repo: Path) -> list[str]:
    payload = json.loads((repo / LEGACY_QT_PATH).read_text(encoding="utf-8"))
    symbols = sorted(
        {
            str(trade.get("symbol") or "")
            for trade in payload.get("qualified_trades") or []
            if trade.get("symbol")
        }
    )
    return symbols


def select_targets(
    eligible: list[dict[str, Any]],
    *,
    slice_name: str,
    max_symbols: int,
    traded: list[str],
) -> list[dict[str, Any]]:
    by_symbol = {row["symbol"]: row for row in eligible}
    if slice_name == "traded":
        selected = [by_symbol[symbol] for symbol in traded if symbol in by_symbol]
        missing = [symbol for symbol in traded if symbol not in by_symbol]
        if missing:
            print("WARN traded symbols dropped by goal filter:", len(missing), flush=True)
    elif slice_name == "holdout":
        traded_set = set(traded)
        selected = [row for row in eligible if row["symbol"] not in traded_set]
    else:
        selected = list(eligible)
    if max_symbols > 0:
        selected = selected[:max_symbols]
    return selected


def cache_path(cache_dir: Path, market: str, symbol: str) -> Path:
    return cache_dir / f"{market}_{symbol}_{LOOKBACK_DAYS}_qfq.json"


def cache_ok(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    records = payload.get("records") or []
    source = str(payload.get("source") or "")
    if not records or not is_jiaoch_stk_mins_v2_source(source):
        return None
    dates = sorted(
        str(row.get("date") or "")[:10]
        for row in records
        if str(row.get("date") or "")
    )
    if not dates or dates[0] > START_DATE or dates[-1] < END_DATE:
        return None
    return {"source": source, "first": dates[0], "last": dates[-1], "bars": len(records)}


def diagnose(repo: Path) -> dict[str, Any]:
    from collections import Counter

    legacy = repo / "data/research_cache"
    files = list(legacy.glob("a_*_*_qfq.json"))
    sources: Counter[str] = Counter()
    v2 = cover_2023 = 0
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            sources["UNREADABLE"] += 1
            continue
        source = str(payload.get("source") or "MISSING")
        sources[source] += 1
        records = payload.get("records") or []
        dates = [
            str(row.get("date") or "")[:10]
            for row in records
            if str(row.get("date") or "")
        ]
        if dates and min(dates) <= START_DATE:
            cover_2023 += 1
        if is_jiaoch_stk_mins_v2_source(source):
            v2 += 1
    eligible = load_eligible_universe(repo)
    traded = load_traded_symbols(repo)
    report = {
        "stage": STAGE_GOAL_ID,
        "legacy_cache_files": len(files),
        "legacy_jiaoch_stk_mins_v2": v2,
        "legacy_cover_to_2023_07_03": cover_2023,
        "legacy_sources": dict(sources),
        "eligible_current_pool": len(eligible),
        "legacy_qt_traded_symbols": len(traded),
        "note": "legacy JSON is mixed/old-unit; 3y replay uses a new isolated v2 cache",
    }
    return report


def refill_one(
    provider: JiaochMarketDataProvider,
    cache_dir: Path,
    item: dict[str, Any],
    *,
    market: str = "a",
    retries: int = 3,
) -> dict[str, Any]:
    symbol = item["symbol"]
    path = cache_path(cache_dir, market, symbol)
    existing = cache_ok(path)
    if existing is not None:
        return {"symbol": symbol, "status": "skip", **existing}
    last_error = ""
    for attempt in range(1, retries + 1):
        try:
            frame, source = provider.history(
                symbol, market, lookback_days=LOOKBACK_DAYS, adjust="qfq"
            )
            source_text = str(source)
            if "akshare" in source_text.casefold():
                raise RuntimeError(f"AKShare is forbidden: {source_text}")
            if not is_jiaoch_stk_mins_v2_source(source_text):
                raise RuntimeError(f"refill produced non-v2 source: {source_text}")
            write_json(
                str(path),
                {"source": source, "records": frame.to_dict(orient="records")},
            )
            checked = cache_ok(path)
            if checked is None:
                raise RuntimeError("refill wrote a cache that failed coverage check")
            return {"symbol": symbol, "status": "ok", "attempt": attempt, **checked}
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(min(8, 2 * attempt))
    return {"symbol": symbol, "status": "error", "error": last_error}


def cmd_diagnose(repo: Path, output_root: Path) -> int:
    report = diagnose(repo)
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(str(output_root / "DIAGNOSE.json"), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def cmd_refill(repo: Path, output_root: Path, args: argparse.Namespace) -> int:
    _load_dotenv(repo / ".env")
    cache_dir = (repo / args.cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)
    eligible = load_eligible_universe(repo)
    traded = load_traded_symbols(repo)
    targets = select_targets(
        eligible,
        slice_name=args.slice,
        max_symbols=args.max_symbols,
        traded=traded,
    )
    proxies = [
        {"symbol": row["symbol"], "market": row["market"], "name": row["name"]}
        for row in MARKET_PROXY_SYMBOLS
    ]
    work = proxies + targets
    provider = JiaochMarketDataProvider()
    log_path = output_root / "REFILL.jsonl"
    summary = {
        "stage": STAGE_GOAL_ID,
        "slice": args.slice,
        "max_symbols": args.max_symbols,
        "lookback_days": LOOKBACK_DAYS,
        "cache_dir": str(cache_dir),
        "planned": len(work),
        "ok": 0,
        "skip": 0,
        "error": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    print(
        f"REFILL planned={len(work)} slice={args.slice} cache={cache_dir}",
        flush=True,
    )
    with log_path.open("a", encoding="utf-8") as log:
        for index, item in enumerate(work, 1):
            market = str(item.get("market") or "a")
            result = refill_one(provider, cache_dir, item, market=market)
            summary[result["status"]] = int(summary.get(result["status"], 0)) + 1
            log.write(json.dumps(result, ensure_ascii=False) + "\n")
            log.flush()
            print(
                f"[{index}/{len(work)}] {item['symbol']} {result['status']}",
                flush=True,
            )
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    write_json(str(output_root / "REFILL_SUMMARY.json"), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["error"] == 0 else 2


def cmd_qualified(repo: Path, output_root: Path, args: argparse.Namespace) -> int:
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")
    os.environ["RESEARCH_REQUIRE_JIAOCHI_STK_MINS_V2"] = "1"
    _load_dotenv(repo / ".env")
    cache_dir = (repo / args.cache_dir).resolve()
    eligible = load_eligible_universe(repo)
    traded = load_traded_symbols(repo)
    targets = select_targets(
        eligible,
        slice_name=args.slice,
        max_symbols=args.max_symbols,
        traded=traded,
    )
    missing = [
        row["symbol"]
        for row in targets
        if cache_ok(cache_path(cache_dir, "a", row["symbol"])) is None
    ]
    if missing and not args.skip_missing:
        print("REFILL incomplete, missing", len(missing), "e.g.", missing[:8], flush=True)
        return 3
    if missing:
        print("WARN skip missing Jiaoch v2 cache:", len(missing), missing, flush=True)
        targets = [
            row
            for row in targets
            if cache_ok(cache_path(cache_dir, "a", row["symbol"])) is not None
        ]
    if not targets:
        print("no cache_ok targets", flush=True)
        return 3
    provider = JiaochMarketDataProvider()
    settings = Settings()
    payload = run_candidate_research_backtest(
        settings=settings,
        provider=provider,
        start_date=START_DATE,
        max_deep=max(1, len(targets)),
        top_n=3,
        hold_days=5,
        lookback_days=LOOKBACK_DAYS,
        cache_dir=str(cache_dir),
        stop_loss_pct=5.0,
        symbol_cooldown_days=5,
        max_active_positions=3,
        include_qualified_trades=True,
        universe_items=targets,
        end_date=END_DATE,
        require_jiaoch_stk_mins_v2=True,
        market_context_mode="stock_breadth",
    )
    payload["summary"]["path_a_3y_clean_replay"] = {
        "stage": STAGE_GOAL_ID,
        "slice": args.slice,
        "development_only": True,
        "promotable": False,
        "source_policy": "jiaoch_stk_mins_v2",
        "source_version": JIAOCH_DAILY_CACHE_SOURCE_VERSION,
        "window": {"start": START_DATE, "end": END_DATE},
    }
    qt_path = (repo / args.qualified_trades_output).resolve()
    write_json(str(qt_path), payload)
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(
        str(output_root / "QUALIFIED_SUMMARY.json"),
        {
            "ok": True,
            "path": str(qt_path),
            "summary": payload.get("summary"),
        },
    )
    print(json.dumps(payload.get("summary"), ensure_ascii=False, indent=2))
    print("wrote", qt_path)
    return 0


def cmd_replay_p0(repo: Path, output_root: Path, args: argparse.Namespace) -> int:
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")
    qt_path = (repo / args.qualified_trades_path).resolve()
    report = build_path_a_p0_drawdown_overlay(
        repo_root=repo,
        require_local_research=not args.allow_any_role,
        qualified_trades_path=qt_path,
    )
    pointer = write_path_a_p0_drawdown_overlay(report, output_root=output_root)
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print("ranking:", report.get("ranking_by_mdd_then_stability"))
    print("best_by_mdd:", report.get("best_by_mdd"))
    for variant in report.get("variants") or []:
        metrics = variant.get("metrics") or {}
        print(
            f"- {variant.get('candidate_id')}: "
            f"ret={metrics.get('portfolio_compounded_return_pct')} "
            f"mdd={metrics.get('portfolio_max_drawdown_pct')} "
            f"both={metrics.get('rolling_both_pass_rate')}"
        )
    return 0 if pointer.get("ok") is True else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=STAGE_GOAL_ID)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--slice", choices=("traded", "eligible", "holdout"), default="traded"
    )
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--qualified-trades-output", type=Path, default=DEFAULT_QT_PATH)
    parser.add_argument("--qualified-trades-path", type=Path, default=DEFAULT_QT_PATH)
    parser.add_argument("--allow-any-role", action="store_true")
    parser.add_argument(
        "--skip-missing",
        action="store_true",
        help="Drop names without Jiaoch v2 cache instead of failing qualified.",
    )
    parser.add_argument(
        "command",
        choices=("diagnose", "refill", "qualified", "replay-p0"),
    )
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    output_root = (repo / args.output_root).resolve()
    if args.command == "diagnose":
        return cmd_diagnose(repo, output_root)
    if args.command == "refill":
        return cmd_refill(repo, output_root, args)
    if args.command == "qualified":
        return cmd_qualified(repo, output_root, args)
    return cmd_replay_p0(repo, output_root, args)


if __name__ == "__main__":
    raise SystemExit(main())
