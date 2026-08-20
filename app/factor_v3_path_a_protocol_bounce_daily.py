"""Daily zero-refit picks for bounce_dn2_negext.

Paper ledger only. Not a production profile. Not automatic trading.
Independent OOS still cannot judge 26/15 until 2027-07-04.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app import factor_v3_path_a_p0_drawdown_overlay as p0
from app.factor_v3_path_a_3y_book_round_specs import merged_kernel
from app.factor_v3_path_a_protocol_baseline_specs import apply_rank_key
from app.factor_v3_path_a_protocol_reclaim_oos import (
    INDEPENDENT_OOS_START,
    TWELVE_MONTH_DUE_DATE,
    days_until_twelve_month_evaluable,
)
from app.factor_v3_path_a_protocol_signal import (
    DEFAULT_CACHE_DIR,
    DEFAULT_BOUNCE_QT_PATH,
    START_DATE,
    build_path_a_protocol_signal_qt,
)
from app.factor_v3_path_a_protocol_signal_specs import (
    DN2_BOUNCE_TAG,
    SIGNAL_BOUNCE_FAMILY,
    iter_protocol_signal_bounce_variants,
)
from app import research_goal_contract as goal
from app.research_equity import _equity_points_from_slot_daily_returns
from app.storage import read_json, write_json

STAGE_GOAL_ID = "path-a-protocol-bounce-daily/v1"
REPORT_SCHEMA = "path-a-protocol-bounce-daily-report/v1"
CANDIDATE_ID = "bounce_dn2_negext"
DEFAULT_TZ = "Asia/Shanghai"
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_protocol_bounce_daily")
DEFAULT_LATEST_PATH = DEFAULT_OUTPUT_ROOT / "LATEST.json"
DEFAULT_CACHE_DIR_PATH = DEFAULT_CACHE_DIR


class PathABounceDailyError(ValueError):
    """Raised when the daily bounce pick cannot be built."""


def bounce_dn2_negext_variant() -> dict[str, Any]:
    for row in iter_protocol_signal_bounce_variants():
        if row["candidate_id"] == CANDIDATE_ID:
            return dict(row)
    raise PathABounceDailyError("bounce_dn2_negext is not registered")


def today_in_tz(tz_name: str = DEFAULT_TZ) -> date:
    return datetime.now(ZoneInfo(tz_name)).date()


def resolve_as_of(*, as_of: str | None = None, tz_name: str = DEFAULT_TZ) -> str:
    if as_of:
        return date.fromisoformat(str(as_of)[:10]).isoformat()
    return today_in_tz(tz_name).isoformat()


def _kernel() -> dict[str, Any]:
    return merged_kernel(bounce_dn2_negext_variant())


def filter_bounce_candidates(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kernel = _kernel()
    req = set(kernel["required_signal_tags"])
    excl = set(kernel["excluded_signal_tags"])
    levels = set(kernel["market_levels"])
    kept: list[dict[str, Any]] = []
    for trade in trades:
        tags = set(trade.get("signal_tags") or [])
        if req and not req.issubset(tags):
            continue
        if excl and tags & excl:
            continue
        if levels and str(trade.get("market_level") or "") not in levels:
            continue
        kept.append(trade)
    return kept


def select_bounce_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    variant = bounce_dn2_negext_variant()
    ranked = apply_rank_key(trades, variant.get("rank_key"))
    if not ranked:
        return []
    try:
        return p0._select_kernel_trades(ranked, kernel_override=_kernel())
    except p0.PathAP0Error:
        return []


def latest_signal_date(trades: list[dict[str, Any]]) -> str | None:
    days = [str(row.get("signal_date") or "")[:10] for row in trades if row.get("signal_date")]
    return max(days) if days else None


def pick_on_date(selected: list[dict[str, Any]], signal_date: str) -> dict[str, Any] | None:
    day = str(signal_date)[:10]
    hits = [row for row in selected if str(row.get("signal_date") or "")[:10] == day]
    if not hits:
        return None
    hits.sort(key=lambda row: float(row.get("rank_score") or 0.0), reverse=True)
    return hits[0]


def _open_position(selected: list[dict[str, Any]], signal_date: str) -> dict[str, Any] | None:
    day = str(signal_date)[:10]
    open_rows = [
        row
        for row in selected
        if str(row.get("signal_date") or "")[:10] < day
        and str(row.get("exit_date") or "")[:10] > day
    ]
    if not open_rows:
        return None
    open_rows.sort(key=lambda row: str(row.get("signal_date") or ""), reverse=True)
    return open_rows[0]


def _compact_trade(trade: dict[str, Any] | None) -> dict[str, Any] | None:
    if trade is None:
        return None
    relative = trade.get("relative_strength") or {}
    return {
        "symbol": trade.get("symbol"),
        "name": trade.get("name"),
        "signal_date": str(trade.get("signal_date") or "")[:10],
        "entry_date": str(trade.get("entry_date") or "")[:10],
        "exit_date": str(trade.get("exit_date") or "")[:10],
        "return_pct": trade.get("return_pct"),
        "rank_score": trade.get("rank_score"),
        "stock_return_20d_pct": relative.get("stock_return_20d_pct"),
        "market_level": trade.get("market_level"),
        "signal_tags": list(trade.get("signal_tags") or []),
    }


def summarize_oos_book(selected: list[dict[str, Any]], *, as_of: str) -> dict[str, Any]:
    kernel = _kernel()
    cutoff = str(as_of)[:10]
    rows = [
        row
        for row in selected
        if INDEPENDENT_OOS_START <= str(row.get("signal_date") or "")[:10] <= cutoff
    ]
    points = (
        _equity_points_from_slot_daily_returns(
            rows,
            max_active_positions=int(kernel["max_active_positions"]),
            exposure_multiplier=float(kernel.get("exposure_multiplier") or 1.0),
            roundtrip_cost_bps=float(kernel["roundtrip_cost_bps"]),
            slippage_bps=float(kernel["slippage_bps"]),
        )
        if rows
        else []
    )
    equity = float(points[-1]["equity"]) if points else 1.0
    closed = sum(
        1 for row in rows if str(row.get("exit_date") or "")[:10] <= cutoff
    )
    return {
        "window_start": INDEPENDENT_OOS_START,
        "as_of": cutoff,
        "trade_count": len(rows),
        "closed_count": closed,
        "equity": round(equity, 6),
        "net_return_pct": round((equity - 1.0) * 100, 2),
        "roundtrip_cost_bps": float(kernel["roundtrip_cost_bps"]),
        "slippage_bps": float(kernel["slippage_bps"]),
        "capital_model": str(kernel.get("capital_model") or "slot-daily"),
        "cash_return_pct": 0.0,
    }


def build_bounce_daily_report(
    trades: list[dict[str, Any]],
    *,
    as_of: str,
    cache_end: str | None = None,
) -> dict[str, Any]:
    selected = select_bounce_trades(trades)
    candidates = filter_bounce_candidates(trades)
    last_fire = latest_signal_date(trades)
    last_ok = latest_signal_date(candidates)
    look_date = str(as_of)[:10]
    data_through = cache_end or last_fire
    pick = pick_on_date(selected, look_date)
    if pick is None and data_through and data_through < look_date:
        pick = pick_on_date(selected, data_through)
        if pick:
            look_date = data_through
    held = None if pick else _open_position(selected, look_date)
    if pick:
        status = "buy"
        empty_reason = None
    elif held:
        status = "holding"
        empty_reason = "already_in_position"
    elif last_ok is None:
        status = "cash"
        empty_reason = "no_qualified_bounce"
    else:
        status = "cash"
        empty_reason = "no_selected_name"
    return {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "candidate_id": CANDIDATE_ID,
        "required_signal_tag": DN2_BOUNCE_TAG,
        "as_of": str(as_of)[:10],
        "look_date": look_date,
        "cache_end": cache_end,
        "data_through": data_through,
        "qualified_signal_count": len(candidates),
        "status": status,
        "empty_reason": empty_reason,
        "pick": _compact_trade(pick),
        "holding": _compact_trade(held),
        "selected_trade_count": len(selected),
        "raw_trade_count": len(trades),
        "latest_raw_signal_date": last_fire,
        "oos_start": INDEPENDENT_OOS_START,
        "twelve_month_due": TWELVE_MONTH_DUE_DATE,
        "days_until_twelve_month": days_until_twelve_month_evaluable(as_of),
        "development_only": True,
        "promotable": False,
        "effective_strategy": False,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "refit": False,
        "generated_at": datetime.now(ZoneInfo(DEFAULT_TZ)).isoformat(timespec="seconds"),
        "oos_book": summarize_oos_book(selected, as_of=str(as_of)[:10]),
        "ledger": [
            _compact_trade(row)
            for row in selected
            if str(row.get("signal_date") or "") >= INDEPENDENT_OOS_START
        ],
    }


def format_bounce_daily_table(report: dict[str, Any]) -> str:
    pick = report.get("pick") or {}
    held = report.get("holding") or {}
    lines = [
        f"策略={report.get('candidate_id')}",
        "非正式有效；不自动交易；只记账",
        f"日期={report.get('look_date')}  状态={report.get('status')}  数据截至={report.get('data_through')}",
        f"合格信号={report.get('qualified_signal_count')}  已选={report.get('selected_trade_count')}",
    ]
    if report.get("status") == "buy":
        lines.append(
            "今日选择={symbol} {name}  次日开盘买入={entry}  20日涨幅={ret}".format(
                symbol=pick.get("symbol"),
                name=pick.get("name") or "",
                entry=pick.get("entry_date"),
                ret=pick.get("stock_return_20d_pct"),
            )
        )
    elif report.get("status") == "holding":
        lines.append(
            "仍在持有={symbol} {name}  计划卖出={exit}".format(
                symbol=held.get("symbol"),
                name=held.get("name") or "",
                exit=held.get("exit_date"),
            )
        )
    else:
        lines.append(f"今日空仓 原因={report.get('empty_reason')}")
    book = report.get("oos_book") or {}
    lines.append(
        "独立OOS账本 净收益={net}%  笔数={n}  含{rt}+{slip}bps  现金不计息".format(
            net=book.get("net_return_pct"),
            n=book.get("trade_count"),
            rt=book.get("roundtrip_cost_bps"),
            slip=book.get("slippage_bps"),
        )
    )
    lines.append(
        f"独立OOS起={report.get('oos_start')}  满12月={report.get('twelve_month_due')}  还差={report.get('days_until_twelve_month')}天"
    )
    ledger = report.get("ledger") or []
    if ledger:
        lines.append("独立OOS已选:")
        for row in ledger:
            lines.append(
                f"  {row.get('signal_date')}  {row.get('symbol')} {row.get('name') or ''}  入={row.get('entry_date')}"
            )
    return "\n".join(lines) + "\n"


def public_bounce_daily_view(report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Website-safe view. Never claims a live order or production profile."""

    if not report:
        return {
            "available": False,
            "candidate_id": CANDIDATE_ID,
            "status": "missing",
            "headline": "尚未扫描",
            "detail": "还没有连跌反弹日账本。跑完盘后扫描后这里会显示买谁或空仓。",
            "pick": None,
            "holding": None,
            "ledger": [],
            "oos_book": None,
            "generated_at": None,
            "automatic_trading_allowed": False,
            "effective_strategy": False,
            "development_only": True,
            "auto_order": False,
        }
    pick = report.get("pick")
    held = report.get("holding")
    status = str(report.get("status") or "cash")
    book = report.get("oos_book") or {}
    if status == "buy" and pick:
        headline = f"计划买入 {pick.get('symbol')} {pick.get('name') or ''}".strip()
        detail = (
            f"信号日 {pick.get('signal_date')}，次日开盘 {pick.get('entry_date')} 买入，"
            f"持有约 5 日。独立OOS账本 {book.get('net_return_pct')}%。"
            "非正式有效，不自动下单。"
        )
    elif status == "holding" and held:
        headline = f"仍持有 {held.get('symbol')} {held.get('name') or ''}".strip()
        detail = (
            f"计划卖出日 {held.get('exit_date')}。"
            f"独立OOS账本 {book.get('net_return_pct')}%。非正式有效，不自动下单。"
        )
    else:
        net = book.get("net_return_pct")
        headline = "今日空仓" if net is None else f"账本 {net:+.2f}%"
        detail = (
            f"原因 {report.get('empty_reason') or 'no_pick'}。"
            f"独立OOS {book.get('trade_count') or 0} 笔，"
            f"净收益 {net if net is not None else '--'}%。"
            f"数据截至 {report.get('data_through') or report.get('look_date') or '--'}。"
            "非正式有效，不自动下单。满12月前不能评 26/15。"
        )
    return {
        "available": True,
        "candidate_id": CANDIDATE_ID,
        "status": status,
        "as_of": report.get("as_of"),
        "look_date": report.get("look_date"),
        "data_through": report.get("data_through"),
        "empty_reason": report.get("empty_reason"),
        "headline": headline,
        "detail": detail,
        "pick": pick,
        "holding": held,
        "ledger": list(report.get("ledger") or [])[-12:],
        "oos_book": report.get("oos_book"),
        "qualified_signal_count": report.get("qualified_signal_count"),
        "selected_trade_count": report.get("selected_trade_count"),
        "days_until_twelve_month": report.get("days_until_twelve_month"),
        "twelve_month_due": report.get("twelve_month_due"),
        "oos_start": report.get("oos_start"),
        "generated_at": report.get("generated_at"),
        "automatic_trading_allowed": False,
        "effective_strategy": False,
        "development_only": True,
        "auto_order": False,
    }


def load_public_bounce_daily_view(path: Path | None = None) -> dict[str, Any]:
    report_path = Path(path or DEFAULT_LATEST_PATH)
    payload = read_json(str(report_path), None)
    if not isinstance(payload, dict):
        return public_bounce_daily_view(None)
    view = public_bounce_daily_view(payload)
    if not view.get("generated_at") and report_path.is_file():
        view["generated_at"] = datetime.fromtimestamp(
            report_path.stat().st_mtime, ZoneInfo(DEFAULT_TZ)
        ).isoformat(timespec="seconds")
    return view


def write_bounce_daily_report(report: dict[str, Any], *, output_root: Path) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    report_path = output_root / "LATEST.json"
    ledger_path = output_root / "LEDGER.json"
    write_json(str(report_path), report)
    write_json(str(ledger_path), report.get("ledger") or [])
    table_path = output_root / "TABLE.txt"
    table_path.write_text(format_bounce_daily_table(report), encoding="utf-8")
    return {
        "ok": True,
        "report_path": str(report_path),
        "ledger_path": str(ledger_path),
        "table_path": str(table_path),
    }


def build_path_a_protocol_bounce_daily(
    *,
    repo_root: Path | None = None,
    cache_dir: Path | None = None,
    as_of: str | None = None,
    tz_name: str = DEFAULT_TZ,
    require_local_research: bool = True,
    max_symbols: int = 0,
    start_date: str = INDEPENDENT_OOS_START,
) -> dict[str, Any]:
    root = (repo_root or Path.cwd()).resolve()
    day = resolve_as_of(as_of=as_of, tz_name=tz_name)
    payload = build_path_a_protocol_signal_qt(
        repo_root=root,
        cache_dir=cache_dir or (root / DEFAULT_CACHE_DIR_PATH),
        max_symbols=max_symbols,
        require_local_research=require_local_research,
        hold_horizons=(5,),
        signal_family=SIGNAL_BOUNCE_FAMILY,
        book="bounce",
        start_date=start_date or START_DATE,
        end_date=day,
        slice_name="bounce-daily",
    )
    trades = list(payload.get("qualified_trades") or [])
    from app.factor_v3_path_a_protocol_reclaim_oos_roll import cache_frontier_snapshot

    cache_dir_path = Path(cache_dir or (root / DEFAULT_CACHE_DIR_PATH))
    if not cache_dir_path.is_absolute():
        cache_dir_path = root / cache_dir_path
    frontier = cache_frontier_snapshot(cache_dir_path).get("latest")
    cache_end = frontier or latest_signal_date(trades)
    meta = (payload.get("summary") or {}).get("path_a_3y_clean_replay") or {}
    report = build_bounce_daily_report(trades, as_of=day, cache_end=cache_end)
    report["source_version"] = meta.get("source_version")
    report["name_count"] = meta.get("name_count")
    report["bounce_qt_path"] = str(DEFAULT_BOUNCE_QT_PATH)
    return report
