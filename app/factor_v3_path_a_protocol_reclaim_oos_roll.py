"""Daily roll of the frozen reclaim independent OOS.

Jiaoch only. Zero-refit. Does not unseal formal final-OOS. The 26/15
12-month gate cannot fire before TWELVE_MONTH_DUE_DATE (2027-07-04).
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app import factor_v3_path_a_protocol_reclaim_oos as oos
from app import research_goal_contract as goal
from app.jiaoch_live_market import (
    ADJ_FACTOR_FIELDS,
    JiaochHttpClient,
    _aggregate_daily_from_minutes,
    _apply_qfq,
    _daily_frame,
    _wire_date,
    is_jiaoch_stk_mins_v2_source,
)
from app.market_data import _tushare_ts_code
from app.storage import write_json

STAGE_GOAL_ID = "path-a-protocol-reclaim-oos-roll/v1"
REPORT_SCHEMA = "path-a-protocol-reclaim-oos-roll-report/v1"
REQUIRED_RUNTIME_ROLE = "local_research"
DEFAULT_TZ = "Asia/Shanghai"
DEFAULT_SCHEDULE_HINT_LOCAL = "18:30"
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_protocol_reclaim_oos_roll")
DEFAULT_PROBE_SYMBOL = "000001"
PRICE_COLS = ("open", "high", "low", "close")


class PathAReclaimOosRollError(ValueError):
    """Raised when the reclaim OOS daily roll cannot run."""


def today_in_tz(tz_name: str = DEFAULT_TZ) -> date:
    return datetime.now(ZoneInfo(tz_name)).date()


def resolve_as_of(*, as_of: str | None = None, tz_name: str = DEFAULT_TZ) -> str:
    if as_of:
        return date.fromisoformat(str(as_of)[:10]).isoformat()
    return today_in_tz(tz_name).isoformat()


def cache_last_date(records: list[dict[str, Any]]) -> str | None:
    dates = [
        str(row.get("date") or "")[:10]
        for row in records
        if str(row.get("date") or "")
    ]
    return max(dates) if dates else None


def cache_frontier_snapshot(cache_dir: Path) -> dict[str, Any]:
    latest: str | None = None
    by_last: dict[str, int] = {}
    files = 0
    if cache_dir.is_dir():
        for path in cache_dir.glob("a_*_1400_qfq.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            records = payload.get("records") or []
            if not records:
                continue
            day = str(records[-1].get("date") or "")[:10]
            if not day:
                continue
            files += 1
            by_last[day] = by_last.get(day, 0) + 1
            if latest is None or day > latest:
                latest = day
    return {"latest": latest, "files": files, "by_last": by_last}


def max_cache_last_date(cache_dir: Path) -> str | None:
    return cache_frontier_snapshot(cache_dir).get("latest")


def merge_extended_records(
    old_records: list[dict[str, Any]],
    new_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Append strictly newer bars. Scale new prices onto the last overlap close."""

    old_by_date = {
        str(row.get("date") or "")[:10]: dict(row)
        for row in old_records
        if str(row.get("date") or "")
    }
    last_old = cache_last_date(old_records)
    if last_old is None:
        return list(old_records), []
    cleaned: list[dict[str, Any]] = []
    for row in new_rows:
        day = str(row.get("date") or "")[:10]
        if not day:
            continue
        cleaned.append(
            {
                "date": day,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume") or 0.0),
                "amount": float(row.get("amount") or 0.0),
            }
        )
    overlap = [row for row in cleaned if row["date"] in old_by_date]
    if overlap:
        day = max(row["date"] for row in overlap)
        old_close = float(old_by_date[day]["close"])
        new_close = float(next(row["close"] for row in cleaned if row["date"] == day))
        if new_close > 0:
            scale = old_close / new_close
            for row in cleaned:
                if row["date"] > last_old:
                    for col in PRICE_COLS:
                        row[col] = float(row[col]) * scale
    added = [row for row in cleaned if row["date"] > last_old]
    added.sort(key=lambda row: row["date"])
    return list(old_records) + added, added


def probe_new_jiaoch_days(
    client: JiaochHttpClient,
    *,
    symbol: str,
    after_date: str,
    end_date: str,
) -> list[str]:
    start = date.fromisoformat(after_date) + timedelta(days=1)
    last = date.fromisoformat(end_date)
    if start > last:
        return []
    rows = client.fetch_stk_mins(
        ts_code=_tushare_ts_code(symbol),
        start_date=_wire_date(start.isoformat()),
        end_date=_wire_date(last.isoformat()),
        freq="5min",
    )
    daily = _aggregate_daily_from_minutes(rows)
    return sorted(
        {
            f"{str(row.get('trade_date') or '')[:4]}-"
            f"{str(row.get('trade_date') or '')[4:6]}-"
            f"{str(row.get('trade_date') or '')[6:8]}"
            for row in daily
            if len(str(row.get("trade_date") or "")) == 8
            and str(row.get("trade_date")) >= start.strftime("%Y%m%d")
        }
    )


def fetch_tail_bars(
    client: JiaochHttpClient,
    *,
    symbol: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    ts_code = _tushare_ts_code(symbol)
    minutes = client.fetch_stk_mins(
        ts_code=ts_code,
        start_date=_wire_date(start_date),
        end_date=_wire_date(end_date),
        freq="5min",
    )
    daily_rows = _aggregate_daily_from_minutes(minutes)
    if not daily_rows:
        return []
    frame = _daily_frame(daily_rows, symbol=symbol, market="a")
    factors = client.fetch(
        "adj_factor",
        params={
            "ts_code": ts_code,
            "start_date": _wire_date(start_date),
            "end_date": _wire_date(end_date),
        },
        fields=ADJ_FACTOR_FIELDS,
    )
    if factors:
        frame = _apply_qfq(frame, factors, expected_code=ts_code)
    return frame.to_dict(orient="records")


def extend_cache_file(
    path: Path,
    client: JiaochHttpClient,
    *,
    symbol: str,
    end_date: str,
    overlap_days: int = 5,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    source = str(payload.get("source") or "")
    if not is_jiaoch_stk_mins_v2_source(source):
        return {"symbol": symbol, "status": "refuse_non_v2"}
    records = list(payload.get("records") or [])
    last = cache_last_date(records)
    if last is None:
        return {"symbol": symbol, "status": "empty"}
    if last >= end_date:
        return {"symbol": symbol, "status": "skip", "last": last}
    start = (
        date.fromisoformat(last) - timedelta(days=max(1, overlap_days))
    ).isoformat()
    new_rows = fetch_tail_bars(
        client, symbol=symbol, start_date=start, end_date=end_date
    )
    merged, added = merge_extended_records(records, new_rows)
    if not added:
        return {"symbol": symbol, "status": "no_new", "last": last}
    payload["records"] = merged
    write_json(str(path), payload)
    return {
        "symbol": symbol,
        "status": "extended",
        "last_before": last,
        "last_after": cache_last_date(merged),
        "added_days": [row["date"] for row in added],
    }


def append_roll_history(output_root: Path, row: dict[str, Any]) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / "roll_history.jsonl"
    line = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return path


def _require_local_research() -> str:
    role = os.getenv("VPS_RUNTIME_ROLE", "")
    if role.strip().casefold() != REQUIRED_RUNTIME_ROLE:
        raise PathAReclaimOosRollError(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def build_path_a_protocol_reclaim_oos_roll(
    *,
    repo_root: Path | None = None,
    as_of: str | None = None,
    tz_name: str = DEFAULT_TZ,
    extend: bool = True,
    rebuild_qt: bool = True,
    require_local_research: bool = True,
    max_symbols: int = 0,
    probe_symbol: str = DEFAULT_PROBE_SYMBOL,
) -> dict[str, Any]:
    role = _require_local_research() if require_local_research else os.getenv(
        "VPS_RUNTIME_ROLE", ""
    )
    root = (repo_root or Path.cwd()).resolve()
    as_of_day = resolve_as_of(as_of=as_of, tz_name=tz_name)
    cache_dir = (root / oos.DEFAULT_CACHE_DIR_PATH).resolve()
    snapshot = cache_frontier_snapshot(cache_dir)
    frontier = snapshot.get("latest")
    client = JiaochHttpClient()
    new_days: list[str] = []
    extend_summary = {
        "attempted": False,
        "extended": 0,
        "no_new": 0,
        "skip": 0,
        "error": 0,
    }
    skipped = False
    skip_reason = None
    if frontier:
        new_days = probe_new_jiaoch_days(
            client,
            symbol=probe_symbol,
            after_date=frontier,
            end_date=as_of_day,
        )
    target_end = frontier or oos.DEFAULT_OOS_END
    if new_days:
        target_end = max([target_end, *new_days])
    lagging = sum(
        count
        for day, count in (snapshot.get("by_last") or {}).items()
        if str(day) < target_end
    )
    need_extend = bool(extend and (new_days or lagging))
    if extend and not need_extend:
        skipped = True
        skip_reason = (
            f"cache frontier {frontier} is aligned; no newer Jiaoch bars "
            f"through {as_of_day} (probe {probe_symbol})"
        )
    elif need_extend:
        from scripts.run_path_a_3y_clean_replay import (
            cache_ok,
            cache_path,
            load_eligible_universe,
            load_traded_symbols,
            select_targets,
        )

        targets = select_targets(
            load_eligible_universe(root),
            slice_name="holdout",
            max_symbols=max_symbols,
            traded=load_traded_symbols(root),
        )
        extend_summary["attempted"] = True
        done = 0
        for item in targets:
            symbol = str(item["symbol"])
            path = cache_path(cache_dir, "a", symbol)
            if cache_ok(path) is None:
                continue
            try:
                result = extend_cache_file(
                    path, client, symbol=symbol, end_date=target_end
                )
            except Exception as exc:
                extend_summary["error"] += 1
                print(f"extend {symbol} error {type(exc).__name__}: {exc}", flush=True)
                continue
            status = str(result.get("status") or "error")
            if status == "extended":
                extend_summary["extended"] += 1
            elif status == "no_new":
                extend_summary["no_new"] += 1
            else:
                extend_summary["skip"] += 1
            done += 1
            if done % 50 == 0:
                print(
                    f"extend {done} extended={extend_summary['extended']} "
                    f"no_new={extend_summary['no_new']} "
                    f"skip={extend_summary['skip']}",
                    flush=True,
                )
    oos_end = target_end
    report = oos.build_path_a_protocol_reclaim_oos(
        repo_root=root,
        cache_dir=cache_dir,
        oos_end=oos_end,
        max_symbols=max_symbols,
        rebuild_qt=bool(
            rebuild_qt and (new_days or extend_summary["extended"] > 0)
        ),
        require_local_research=require_local_research,
    )
    roll = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "as_of": as_of_day,
        "timezone": tz_name,
        "schedule_hint_local": DEFAULT_SCHEDULE_HINT_LOCAL,
        "probe_symbol": probe_symbol,
        "cache_frontier": frontier,
        "cache_snapshot": snapshot,
        "lagging_files": lagging,
        "target_end": target_end,
        "new_jiaoch_days": new_days,
        "skipped": skipped,
        "skip_reason": skip_reason,
        "extend": extend_summary,
        "oos_end": oos_end,
        "twelve_month_due_date": oos.TWELVE_MONTH_DUE_DATE,
        "days_until_twelve_month_evaluable": oos.days_until_twelve_month_evaluable(
            as_of_day
        ),
        "twelve_month_evaluable": report.get("twelve_month_evaluable"),
        "independent_oos_dual_pass_26_15": report.get(
            "independent_oos_dual_pass_26_15"
        ),
        "effective_strategy": False,
        "formal_final_oos": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "vps_runtime_role": role,
        "oos": report,
    }
    return roll


def format_reclaim_oos_roll_table(report: dict[str, Any]) -> str:
    inner = report.get("oos") or {}
    score = inner.get("score") or {}
    return "\n".join(
        [
            f"stage={report.get('stage_goal_id')}",
            f"as_of={report.get('as_of')} oos_end={report.get('oos_end')}",
            f"skipped={report.get('skipped')} new_days={report.get('new_jiaoch_days')}",
            f"due={report.get('twelve_month_due_date')} "
            f"days_left={report.get('days_until_twelve_month_evaluable')}",
            f"twelve_month_evaluable={report.get('twelve_month_evaluable')}",
            f"effective_strategy={report.get('effective_strategy')}",
            f"selected={score.get('selected_trade_count')} "
            f"raw={inner.get('raw_trade_count')}",
            "",
        ]
    )


def write_path_a_protocol_reclaim_oos_roll(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    write_json(str(output_root / "LATEST.json"), report)
    table = format_reclaim_oos_roll_table(report)
    (output_root / "TABLE.txt").write_text(table, encoding="utf-8")
    history = {
        "as_of": report.get("as_of"),
        "oos_end": report.get("oos_end"),
        "skipped": report.get("skipped"),
        "new_jiaoch_days": report.get("new_jiaoch_days"),
        "days_until_twelve_month_evaluable": report.get(
            "days_until_twelve_month_evaluable"
        ),
        "selected": ((report.get("oos") or {}).get("score") or {}).get(
            "selected_trade_count"
        ),
        "effective_strategy": False,
    }
    history_path = append_roll_history(output_root, history)
    return {
        "ok": True,
        "stage_goal_id": STAGE_GOAL_ID,
        "effective_strategy": False,
        "table_path": str(output_root / "TABLE.txt"),
        "report_path": str(output_root / "LATEST.json"),
        "history_path": str(history_path),
    }
