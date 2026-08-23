"""Translate bounce_dn2_negext daily status into a personal ticket.

Paper blotter only. Fills must be real trades. Never sends broker orders.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app import personal_capital_contract as c
from app.factor_v3_path_a_protocol_bounce_daily import DEFAULT_LATEST_PATH as BOUNCE_LATEST
from app.factor_v3_path_a_protocol_signal import DEFAULT_CACHE_DIR
from app.storage import append_jsonl, read_json, write_json

DEFAULT_TZ = "Asia/Shanghai"
DEFAULT_OUTPUT_ROOT = Path("data/personal_book")
DEFAULT_LATEST_PATH = DEFAULT_OUTPUT_ROOT / "LATEST.json"
DEFAULT_STATE_PATH = DEFAULT_OUTPUT_ROOT / "STATE.json"
DEFAULT_FILLS_PATH = DEFAULT_OUTPUT_ROOT / "FILLS.jsonl"


def commission_cny(notional: float) -> float:
    return max(c.MIN_COMMISSION_CNY, abs(float(notional)) * c.COMMISSION_RATE)


def transfer_cny(notional: float, symbol: str) -> float:
    if str(symbol).startswith("60"):
        return abs(float(notional)) * c.SHANGHAI_TRANSFER_RATE
    return 0.0


def sell_costs_cny(notional: float, *, symbol: str) -> dict[str, float]:
    amount = abs(float(notional))
    return {
        "commission": commission_cny(amount),
        "stamp": amount * c.STAMP_TAX_SELL,
        "transfer": transfer_cny(amount, symbol),
    }


def size_open(*, capital_cny: float, price: float, cash_cny: float) -> dict[str, Any]:
    capital = c.clamp_capital(capital_cny)
    px = float(price)
    cash = float(cash_cny)
    if px <= 0:
        return {"action": "untradeable", "reason": "missing_price"}
    budget = min(cash, capital * c.POSITION_FRACTION)
    shares = int(budget // px // c.LOT_SIZE) * c.LOT_SIZE
    notional = shares * px
    if shares < c.LOT_SIZE or notional < c.MIN_NOTIONAL_CNY:
        return {
            "action": "untradeable",
            "reason": "notional_too_small",
            "shares": shares,
            "lots": shares // c.LOT_SIZE,
            "notional": notional,
        }
    return {
        "action": "open",
        "shares": shares,
        "lots": shares // c.LOT_SIZE,
        "notional": notional,
        "commission": commission_cny(notional),
        "transfer": transfer_cny(notional, ""),
    }


def empty_state(capital_cny: float = c.DEFAULT_CAPITAL_CNY) -> dict[str, Any]:
    capital = c.clamp_capital(capital_cny)
    return {
        "schema": c.PERSONAL_BOOK_SCHEMA,
        "capital": capital,
        "cash": capital,
        "positions": [],
        "equity": capital,
        "peak_equity": capital,
        "drawdown_pct": 0.0,
        "halt": "none",
        "fills": [],
    }


def refresh_halt(state: dict[str, Any]) -> dict[str, Any]:
    out = dict(state)
    peak = max(float(out.get("peak_equity") or 0.0), float(out.get("equity") or 0.0), 1e-9)
    equity = float(out.get("equity") or 0.0)
    out["peak_equity"] = peak
    dd = (peak - equity) / peak * 100.0 if peak else 0.0
    out["drawdown_pct"] = round(dd, 2)
    if dd >= c.FLATTEN_DRAWDOWN_PCT:
        out["halt"] = "flatten"
    elif dd >= c.PAUSE_DRAWDOWN_PCT:
        out["halt"] = "pause_entries"
    else:
        out["halt"] = "none"
    return out


def last_close_from_cache(cache_dir: Path, symbol: str) -> float | None:
    path = Path(cache_dir) / f"a_{symbol}_1400_qfq.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    records = payload.get("records") or []
    if not records:
        return None
    try:
        close = float(records[-1].get("close"))
    except (TypeError, ValueError):
        return None
    return close if close > 0 else None


def _focus(bounce: dict[str, Any]) -> dict[str, Any]:
    if bounce.get("status") == "buy":
        return dict(bounce.get("pick") or {})
    if bounce.get("status") == "holding":
        return dict(bounce.get("holding") or {})
    return {}


def _already_skipped(state: dict[str, Any], as_of: Any, symbol: str) -> bool:
    day = str(as_of or "")[:10]
    code = str(symbol or "")
    for row in state.get("fills") or []:
        if (
            row.get("kind") == "skip"
            and str(row.get("as_of") or "")[:10] == day
            and str(row.get("symbol") or "") == code
        ):
            return True
    return False


def _position(state: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    for row in state.get("positions") or []:
        if str(row.get("symbol")) == str(symbol):
            return row
    return None


def build_ticket(
    bounce: dict[str, Any],
    state: dict[str, Any],
    *,
    price: float | None,
) -> dict[str, Any]:
    state = refresh_halt(dict(state))
    status = str(bounce.get("status") or "cash")
    focus = _focus(bounce)
    symbol = str(focus.get("symbol") or "")
    held = _position(state, symbol) if symbol else (state.get("positions") or [None])[0]
    halt = str(state.get("halt") or "none")
    base = {
        "schema": c.PERSONAL_BOOK_SCHEMA,
        "stage_goal_id": c.STAGE_GOAL_ID,
        "candidate_id": c.CANDIDATE_ID,
        "as_of": bounce.get("as_of"),
        "data_through": bounce.get("data_through"),
        "source_status": status,
        "automatic_trading_allowed": False,
        "effective_strategy": False,
        "production_profile": False,
        "auto_order": False,
        "halt": halt,
        "cash": state.get("cash"),
        "equity": state.get("equity"),
        "drawdown_pct": state.get("drawdown_pct"),
        "capital": state.get("capital"),
        "positions_match": True,
        "headline": "今日不开",
        "detail": "非正式有效。不自动下单。个人账本与研究 26/15 分开。",
        "action": "cash",
        "symbol": None,
        "name": None,
        "lots": 0,
        "shares": 0,
        "notional": 0.0,
        "reference_price": price,
        "commission": 0.0,
    }
    if halt == "flatten" and held:
        return {
            **base,
            "action": "close",
            "symbol": held.get("symbol"),
            "name": held.get("name"),
            "lots": int(held.get("shares") or 0) // c.LOT_SIZE,
            "shares": int(held.get("shares") or 0),
            "headline": "停机平仓",
            "detail": "个人账本回撤达到 15%，只许按计划卖出，不再新开。",
        }
    if status == "buy" and halt in {"pause_entries", "flatten"}:
        return {**base, "action": "halted", "headline": "停机，不开新仓"}
    if status == "buy" and focus and _already_skipped(state, bounce.get("as_of"), symbol):
        return {
            **base,
            "action": "cash",
            "symbol": symbol,
            "name": focus.get("name"),
            "headline": "已跳过该信号",
            "detail": "个人账本保持空仓。研究账本仍按策略记账。",
        }
    if status == "buy" and focus:
        if state.get("positions"):
            return {
                **base,
                "action": "cash",
                "headline": "已有持仓，忽略新信号",
                "detail": "个人线 Phase 0 单槽。",
            }
        sized = size_open(
            capital_cny=float(state.get("capital") or c.DEFAULT_CAPITAL_CNY),
            price=float(price or 0.0),
            cash_cny=float(state.get("cash") or 0.0),
        )
        if sized.get("action") != "open":
            return {
                **base,
                **{k: sized.get(k) for k in ("shares", "lots", "notional")},
                "action": "untradeable",
                "symbol": symbol,
                "name": focus.get("name"),
                "headline": "金额不够一手",
                "detail": str(sized.get("reason") or "untradeable"),
            }
        return {
            **base,
            **sized,
            "symbol": symbol,
            "name": focus.get("name"),
            "signal_date": focus.get("signal_date"),
            "entry_date": focus.get("entry_date"),
            "exit_date": focus.get("exit_date"),
            "headline": f"计划买入 {symbol} {focus.get('name') or ''}".strip(),
            "detail": (
                f"次日开盘 {focus.get('entry_date')} 买 {sized['lots']} 手，"
                f"约 {sized['notional']:.0f} 元。不自动下单。"
            ),
        }
    if status == "holding" and focus:
        if not held or str(held.get("symbol")) != symbol:
            return {
                **base,
                "action": "mismatch",
                "positions_match": False,
                "symbol": symbol,
                "name": focus.get("name"),
                "headline": "策略持有，你未跟单",
                "detail": "研究账本仍记持仓；个人账本保持空仓。不要补买。",
            }
        as_of = str(bounce.get("as_of") or "")[:10]
        exit_date = str(focus.get("exit_date") or held.get("planned_exit") or "")[:10]
        shares = int(held.get("shares") or 0)
        action = "close" if exit_date and as_of >= exit_date else "hold"
        return {
            **base,
            "action": action,
            "symbol": symbol,
            "name": focus.get("name") or held.get("name"),
            "lots": shares // c.LOT_SIZE,
            "shares": shares,
            "exit_date": exit_date,
            "headline": "计划卖出" if action == "close" else f"持有 {symbol}",
            "detail": f"计划卖出日 {exit_date}。不自动下单。",
        }
    return base


def apply_event(state: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    out = dict(state)
    out["positions"] = [dict(row) for row in (state.get("positions") or [])]
    fills = list(state.get("fills") or [])
    kind = str(event.get("kind") or "")
    if kind == "skip":
        fills.append({**event, "kind": "skip"})
        out["fills"] = fills[-50:]
        return refresh_halt(out)
    if kind != "fill":
        raise ValueError("personal book event kind rejected")
    side = str(event.get("side") or "")
    symbol = str(event.get("symbol") or "")
    shares = int(event.get("shares") or 0)
    price = float(event.get("price") or 0.0)
    if shares <= 0 or shares % c.LOT_SIZE or price <= 0 or not symbol:
        raise ValueError("personal fill rejected")
    notional = shares * price
    commission = float(event.get("commission") or commission_cny(notional))
    if side == "buy":
        if out["positions"]:
            raise ValueError("personal book already in position")
        transfer = transfer_cny(notional, symbol)
        cost = notional + commission + transfer
        if cost > float(out["cash"]) + 1e-6:
            raise ValueError("personal cash is insufficient")
        out["cash"] = round(float(out["cash"]) - cost, 2)
        out["positions"] = [
            {
                "symbol": symbol,
                "name": event.get("name"),
                "shares": shares,
                "lots": shares // c.LOT_SIZE,
                "avg_price": price,
                "entry_date": event.get("as_of"),
                "planned_exit": event.get("planned_exit"),
            }
        ]
    elif side == "sell":
        held = _position(out, symbol)
        if not held or int(held.get("shares") or 0) != shares:
            raise ValueError("personal sell does not match position")
        costs = sell_costs_cny(notional, symbol=symbol)
        proceeds = notional - costs["commission"] - costs["stamp"] - costs["transfer"]
        if event.get("commission") is not None:
            proceeds = notional - commission - costs["stamp"] - costs["transfer"]
        out["cash"] = round(float(out["cash"]) + proceeds, 2)
        out["positions"] = [row for row in out["positions"] if str(row.get("symbol")) != symbol]
    else:
        raise ValueError("personal fill side rejected")
    mtm = 0.0
    for row in out["positions"]:
        mtm += int(row.get("shares") or 0) * float(row.get("avg_price") or 0.0)
    out["equity"] = round(float(out["cash"]) + mtm, 2)
    fills.append({**event, "kind": "fill", "notional": notional, "commission": commission})
    out["fills"] = fills[-50:]
    return refresh_halt(out)


def public_view(ticket: dict[str, Any] | None, state: dict[str, Any] | None) -> dict[str, Any]:
    if not ticket:
        return {
            "available": False,
            "action": "missing",
            "headline": "尚无个人凭证",
            "automatic_trading_allowed": False,
            "effective_strategy": False,
            "auto_order": False,
        }
    st = state or {}
    return {
        "available": True,
        "action": ticket.get("action"),
        "headline": ticket.get("headline"),
        "detail": ticket.get("detail"),
        "symbol": ticket.get("symbol"),
        "name": ticket.get("name"),
        "lots": ticket.get("lots"),
        "shares": ticket.get("shares"),
        "notional": ticket.get("notional"),
        "reference_price": ticket.get("reference_price"),
        "commission": ticket.get("commission"),
        "entry_date": ticket.get("entry_date"),
        "exit_date": ticket.get("exit_date"),
        "as_of": ticket.get("as_of"),
        "data_through": ticket.get("data_through"),
        "halt": ticket.get("halt") or st.get("halt"),
        "cash": st.get("cash"),
        "equity": st.get("equity"),
        "drawdown_pct": st.get("drawdown_pct"),
        "capital": st.get("capital"),
        "positions": st.get("positions") or [],
        "positions_match": ticket.get("positions_match"),
        "automatic_trading_allowed": False,
        "effective_strategy": False,
        "production_profile": False,
        "auto_order": False,
        "candidate_id": c.CANDIDATE_ID,
    }


def load_state(path: Path | None = None) -> dict[str, Any]:
    payload = read_json(str(path or DEFAULT_STATE_PATH), None)
    if isinstance(payload, dict) and payload.get("capital"):
        return refresh_halt(payload)
    return empty_state()


def build_and_store(
    *,
    bounce_path: Path | None = None,
    cache_dir: Path | None = None,
    output_root: Path | None = None,
    capital_cny: float | None = None,
) -> dict[str, Any]:
    root = output_root or DEFAULT_OUTPUT_ROOT
    bounce = read_json(str(bounce_path or BOUNCE_LATEST), None)
    if not isinstance(bounce, dict):
        bounce = {"status": "missing"}
    state_path = root / "STATE.json"
    state = load_state(state_path)
    if capital_cny is not None:
        state["capital"] = c.clamp_capital(capital_cny)
        if not state.get("positions") and abs(float(state.get("cash") or 0) - float(state.get("peak_equity") or 0)) < 1e-6:
            state["cash"] = state["capital"]
            state["equity"] = state["capital"]
            state["peak_equity"] = max(float(state.get("peak_equity") or 0), state["capital"])
    focus = _focus(bounce)
    symbol = str(focus.get("symbol") or "")
    price = last_close_from_cache(cache_dir or Path(DEFAULT_CACHE_DIR), symbol) if symbol else None
    ticket = build_ticket(bounce, state, price=price)
    ticket["generated_at"] = datetime.now(ZoneInfo(DEFAULT_TZ)).isoformat(timespec="seconds")
    write_json(str(root / "LATEST.json"), ticket)
    write_json(str(state_path), state)
    table = (
        f"action={ticket.get('action')}  {ticket.get('symbol') or ''}  "
        f"lots={ticket.get('lots')}  halt={ticket.get('halt')}\n"
        f"{ticket.get('headline')}\n"
        "非正式有效；不自动下单；个人线\n"
    )
    (root / "TABLE.txt").write_text(table, encoding="utf-8")
    return ticket


def record_event(event: dict[str, Any], *, output_root: Path | None = None) -> dict[str, Any]:
    root = output_root or DEFAULT_OUTPUT_ROOT
    payload = {key: value for key, value in dict(event).items() if value is not None}
    if not payload.get("as_of"):
        payload["as_of"] = datetime.now(ZoneInfo(DEFAULT_TZ)).date().isoformat()
    state = apply_event(load_state(root / "STATE.json"), payload)
    write_json(str(root / "STATE.json"), state)
    append_jsonl(str(root / "FILLS.jsonl"), payload)
    bounce = read_json(str(root / "LATEST.json"), {}) or {}
    if isinstance(bounce, dict):
        price = bounce.get("reference_price")
        latest_bounce = read_json(str(BOUNCE_LATEST), bounce)
        if isinstance(latest_bounce, dict):
            ticket = build_ticket(latest_bounce, state, price=price)
            ticket["generated_at"] = datetime.now(ZoneInfo(DEFAULT_TZ)).isoformat(timespec="seconds")
            write_json(str(root / "LATEST.json"), ticket)
    return state
