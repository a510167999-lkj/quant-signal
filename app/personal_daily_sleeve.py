"""Always-on personal sleeve: daily top-2 by -20d return.

Not bounce_dn2_negext. Not Path A dual-pass. Not 26/15. No auto orders.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app import personal_book as pbook
from app import personal_capital_contract as c
from app import research_goal_contract as goal
from app.factor_v3_path_a_protocol_signal import DEFAULT_CACHE_DIR
from app.storage import read_json, write_json

CANDIDATE_ID = "personal_daily_negext_m2"
REPORT_SCHEMA = "personal-daily-sleeve-report/v1"
STAGE_GOAL_ID = "personal-daily-sleeve/v1"
LOOKBACK = 20
TOP_N = 2
DEFAULT_TZ = "Asia/Shanghai"
DEFAULT_OUTPUT_ROOT = Path("data/personal_daily_sleeve")
DEFAULT_LATEST_PATH = DEFAULT_OUTPUT_ROOT / "LATEST.json"
EFFECTIVE_STRATEGY = False
AUTOMATIC_TRADING_ALLOWED = goal.AUTOMATIC_TRADING_ALLOWED


def eligible_symbol(symbol: str) -> bool:
    code = str(symbol or "").strip()
    if not code:
        return False
    if code.startswith(("688", "689", "8", "4", "9")):
        return False
    return code.startswith(("60", "00", "30"))


def _ret20(records: list[dict[str, Any]], *, as_of: str | None = None) -> dict[str, Any] | None:
    rows = []
    cutoff = str(as_of or "")[:10] or None
    for row in records:
        day = str(row.get("date") or "")[:10]
        if not day or (cutoff and day > cutoff):
            continue
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        rows.append((day, close))
    if len(rows) < LOOKBACK + 1:
        return None
    last_day, last = rows[-1]
    if cutoff and last_day != cutoff:
        return None
    prev = rows[-(LOOKBACK + 1)][1]
    ret = last / prev - 1.0
    return {
        "close": last,
        "as_of": last_day,
        "ret20": ret,
        "rank_score": -ret,
    }


def _frontier(rows: list[dict[str, Any]]) -> str | None:
    latest = None
    for row in rows:
        if not eligible_symbol(str(row.get("symbol") or "")):
            continue
        for rec in reversed(list(row.get("records") or [])):
            day = str(rec.get("date") or "")[:10]
            if day:
                if latest is None or day > latest:
                    latest = day
                break
    return latest


def rank_negext20(
    rows: list[dict[str, Any]],
    *,
    top_n: int = TOP_N,
    as_of: str | None = None,
) -> list[dict[str, Any]]:
    cutoff = str(as_of or "")[:10] or _frontier(rows)
    ranked: list[dict[str, Any]] = []
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if not eligible_symbol(symbol):
            continue
        stats = _ret20(list(row.get("records") or []), as_of=cutoff)
        if not stats:
            continue
        ranked.append({"symbol": symbol, "name": row.get("name"), **stats})
    ranked.sort(key=lambda item: float(item["rank_score"]), reverse=True)
    return ranked[: max(int(top_n), 0)]


def scan_cache(
    cache_dir: Path,
    *,
    top_n: int = TOP_N,
    as_of: str | None = None,
    max_files: int = 0,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    files = sorted(Path(cache_dir).glob("a_*_1400_qfq.json"))
    if max_files:
        files = files[: max_files]
    for path in files:
        parts = path.name.split("_")
        if len(parts) < 2:
            continue
        symbol = parts[1]
        if not eligible_symbol(symbol):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        rows.append({"symbol": symbol, "records": payload.get("records") or []})
    return rank_negext20(rows, top_n=top_n, as_of=as_of)


def build_daily_report(
    ranked: list[dict[str, Any]],
    *,
    previous_symbols: list[str] | None = None,
    capital_cny: float = c.DEFAULT_CAPITAL_CNY,
    as_of: str | None = None,
    data_through: str | None = None,
) -> dict[str, Any]:
    previous = [str(sym) for sym in (previous_symbols or []) if sym]
    names = [
        {"symbol": row["symbol"], "name": row.get("name"), "price": row["close"]}
        for row in ranked
    ]
    sized = pbook.size_open_m2(
        capital_cny=capital_cny,
        cash_cny=capital_cny,
        names=names,
    )
    size_by = {str(slot.get("symbol")): slot for slot in sized.get("slots") or []}
    picks = []
    current = []
    for row in ranked:
        symbol = str(row["symbol"])
        slot = size_by.get(symbol) or {}
        action = "hold" if symbol in previous else "enter"
        picks.append(
            {
                "symbol": symbol,
                "name": row.get("name"),
                "ret20": row.get("ret20"),
                "rank_score": row.get("rank_score"),
                "close": row.get("close"),
                "action": action,
                "lots": slot.get("lots") or 0,
                "shares": slot.get("shares") or 0,
                "notional": slot.get("notional") or 0.0,
                "commission": slot.get("commission") or 0.0,
            }
        )
        current.append(symbol)
    through = data_through or (ranked[0]["as_of"] if ranked else as_of)
    return {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "candidate_id": CANDIDATE_ID,
        "as_of": as_of or through,
        "data_through": through,
        "picks": picks,
        "exits": [sym for sym in previous if sym not in current],
        "automatic_trading_allowed": False,
        "effective_strategy": False,
        "production_profile": False,
        "auto_order": False,
        "development_only": True,
        "not_bounce": True,
        "capital": capital_cny,
    }


def public_view(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "available": False,
            "candidate_id": CANDIDATE_ID,
            "picks": [],
            "headline": "尚无每日持仓袖",
            "detail": "不是连跌反弹。非正式有效。不自动下单。",
            "automatic_trading_allowed": False,
            "effective_strategy": False,
            "auto_order": False,
        }
    picks = list(report.get("picks") or [])
    names = "、".join(
        f"{row.get('symbol')}" for row in picks if row.get("symbol")
    ) or "空"
    return {
        "available": True,
        "candidate_id": CANDIDATE_ID,
        "as_of": report.get("as_of"),
        "data_through": report.get("data_through"),
        "picks": picks,
        "exits": list(report.get("exits") or []),
        "headline": f"建议持有 {names}",
        "detail": (
            "不是连跌反弹。按 −20 日收益取 Top 2，各不超过本金 30%。"
            "非正式有效，不自动下单，不要与连跌反弹凭证同时打满本金。"
        ),
        "automatic_trading_allowed": False,
        "effective_strategy": False,
        "production_profile": False,
        "auto_order": False,
        "capital": report.get("capital"),
    }


def load_public_view(path: Path | None = None) -> dict[str, Any]:
    payload = read_json(str(path or DEFAULT_LATEST_PATH), None)
    if not isinstance(payload, dict):
        return public_view(None)
    return public_view(payload)


def build_and_store(
    *,
    cache_dir: Path | None = None,
    output_root: Path | None = None,
    capital_cny: float | None = None,
    as_of: str | None = None,
    max_files: int = 0,
) -> dict[str, Any]:
    root = output_root or DEFAULT_OUTPUT_ROOT
    previous = read_json(str(root / "LATEST.json"), {}) or {}
    prev_symbols = [
        str(row.get("symbol"))
        for row in (previous.get("picks") or [])
        if row.get("symbol")
    ]
    ranked = scan_cache(
        cache_dir or Path(DEFAULT_CACHE_DIR),
        as_of=as_of,
        max_files=max_files,
    )
    report = build_daily_report(
        ranked,
        previous_symbols=prev_symbols,
        capital_cny=capital_cny or c.DEFAULT_CAPITAL_CNY,
        as_of=as_of,
    )
    report["generated_at"] = datetime.now(ZoneInfo(DEFAULT_TZ)).isoformat(timespec="seconds")
    write_json(str(root / "LATEST.json"), report)
    lines = [
        f"candidate={CANDIDATE_ID}",
        "不是连跌反弹；非正式有效；不自动下单",
        f"as_of={report.get('as_of')} data_through={report.get('data_through')}",
    ]
    for row in report.get("picks") or []:
        lines.append(
            f"  {row.get('action')} {row.get('symbol')} ret20={row.get('ret20')} "
            f"lots={row.get('lots')} notional={row.get('notional')}"
        )
    if report.get("exits"):
        lines.append("exits " + ",".join(report["exits"]))
    (root / "TABLE.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
