"""Replay realized trade outcomes from a read-only PIT artifact.

The qualified-trade return and mark-to-market fields are claims, not proof.
This module re-reads causal adjusted bars and next-open sell attempts, then
compares the claim against a deterministic open-to-open time-exit projection.
Missing legacy fields remain an explicit blocker; a present but altered claim
fails closed.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Dict, List, Mapping


SCHEMA_VERSION = "artifact_outcome_replay/v1"
_CLAIM_FIELDS = (
    "entry_raw_price",
    "exit_raw_price",
    "return_pct",
    "max_adverse_pct",
    "max_favorable_pct",
    "mark_to_market_path",
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _trade_key(trade: Mapping[str, Any]) -> str:
    values = (
        str(trade.get("symbol") or ""),
        str(trade.get("signal_date") or "")[:10],
        str(trade.get("entry_date") or "")[:10],
        str(trade.get("exit_date") or "")[:10],
    )
    if not all(values):
        raise ValueError("artifact outcome replay trade key is incomplete")
    return "|".join(values)


def _finite_positive(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("artifact outcome replay %s is invalid" % label) from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError("artifact outcome replay %s is invalid" % label)
    return number


def _execution_attempt(
    value: Any, label: str, *, trade_date: str, side: str
) -> Dict[str, Any]:
    if not isinstance(value, Mapping) or type(value.get("fillable")) is not bool:
        raise ValueError("artifact outcome replay %s verdict is invalid" % label)
    proof = value.get("generation_proof")
    if not isinstance(proof, Mapping) or not proof:
        raise ValueError("artifact outcome replay %s generation proof is missing" % label)
    if str(proof.get("trade_date") or "")[:10] != str(trade_date)[:10]:
        raise ValueError("artifact outcome replay %s generation date mismatch" % label)
    if proof.get("side") not in (None, side):
        raise ValueError("artifact outcome replay %s generation side mismatch" % label)
    raw_price = None
    if value["fillable"]:
        raw_price = _finite_positive(value.get("raw_price"), "%s raw price" % label)
    elif value.get("raw_price") is not None:
        raise ValueError("artifact outcome replay blocked %s has a raw price" % label)
    return {
        "fillable": value["fillable"],
        "reason": value.get("reason"),
        "raw_price": raw_price,
        "generation_proof": dict(proof),
    }


def _bars_by_date(audited_universe: Any, symbol: str, end_date: str) -> Dict[str, Mapping[str, Any]]:
    start_date = str(getattr(audited_universe, "start_date", end_date))[:10]
    rows = audited_universe.causal_signal_bars(symbol, start_date, end_date)
    if not isinstance(rows, list) or not rows:
        raise ValueError("artifact outcome replay causal bars are missing")
    result: Dict[str, Mapping[str, Any]] = {}
    required = ("trade_date", "signal_open", "signal_high", "signal_low", "signal_close")
    for row in rows:
        if not isinstance(row, Mapping) or any(row.get(field) is None for field in required):
            raise ValueError("artifact outcome replay causal bar is incomplete")
        trade_date = str(row["trade_date"])[:10]
        if trade_date in result:
            raise ValueError("artifact outcome replay causal bars contain duplicates")
        result[trade_date] = row
    return result


def _compare_claim(claimed: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    for field in _CLAIM_FIELDS:
        if field not in claimed or _canonical(claimed[field]) != _canonical(expected[field]):
            raise ValueError("artifact outcome replay mismatch: %s" % field)


def replay_trade_outcome(
    audited_universe: Any,
    trade: Mapping[str, Any],
    *,
    compare_claim: bool = True,
) -> Dict[str, Any]:
    """Recompute one time-exit outcome and optionally compare its claims."""

    symbol = str(trade.get("symbol") or "")
    signal_date = str(trade.get("signal_date") or "")[:10]
    entry_date = str(trade.get("entry_date") or "")[:10]
    planned_exit_date = str(trade.get("planned_exit_date") or "")[:10]
    exit_date = str(trade.get("exit_date") or "")[:10]
    if not all((symbol, signal_date, entry_date, planned_exit_date, exit_date)):
        raise ValueError("artifact outcome replay dates are incomplete")

    sessions = list(audited_universe.open_sessions(signal_date, exit_date))
    positions = {str(session)[:10]: index for index, session in enumerate(sessions)}
    if any(value not in positions for value in (signal_date, entry_date, planned_exit_date, exit_date)):
        raise ValueError("artifact outcome replay session is missing")
    signal_position = positions[signal_date]
    entry_position = positions[entry_date]
    planned_position = positions[planned_exit_date]
    exit_position = positions[exit_date]
    if entry_position != signal_position + 1 or planned_position < entry_position or exit_position < planned_position:
        raise ValueError("artifact outcome replay session positions are invalid")

    try:
        holding_days = int(trade.get("holding_days"))
    except (TypeError, ValueError) as exc:
        raise ValueError("artifact outcome replay holding period is invalid") from exc
    if holding_days <= 0 or planned_position != entry_position + holding_days:
        raise ValueError("artifact outcome replay holding period is invalid")

    bars = _bars_by_date(audited_universe, symbol, exit_date)
    held_dates = [str(session)[:10] for session in sessions[entry_position : exit_position + 1]]
    if any(trade_date not in bars for trade_date in held_dates):
        raise ValueError("artifact outcome replay held bar is missing")
    entry_row = bars[entry_date]
    exit_row = bars[exit_date]
    entry_total_return_price = _finite_positive(entry_row.get("signal_open"), "entry signal open")
    exit_total_return_price = _finite_positive(exit_row.get("signal_open"), "exit signal open")

    mark_to_market_path: List[Dict[str, Any]] = []
    adverse = 0.0
    favorable = 0.0
    for trade_date in held_dates:
        row = bars[trade_date]
        is_exit = trade_date == exit_date
        open_price = exit_total_return_price if is_exit else _finite_positive(row.get("signal_open"), "open")
        high_price = open_price if is_exit else _finite_positive(row.get("signal_high"), "high")
        low_price = open_price if is_exit else _finite_positive(row.get("signal_low"), "low")
        close_price = open_price if is_exit else _finite_positive(row.get("signal_close"), "close")
        marks = {
            "date": trade_date,
            "open_return_pct": round((open_price / entry_total_return_price - 1) * 100, 4),
            "high_return_pct": round((high_price / entry_total_return_price - 1) * 100, 4),
            "close_return_pct": round((close_price / entry_total_return_price - 1) * 100, 4),
            "low_return_pct": round((low_price / entry_total_return_price - 1) * 100, 4),
        }
        mark_to_market_path.append(marks)
        adverse = min(adverse, marks["low_return_pct"])
        favorable = max(favorable, marks["high_return_pct"])

    exit_search: List[Dict[str, Any]] = []
    for candidate_date in sessions[planned_position : exit_position + 1]:
        candidate_date = str(candidate_date)[:10]
        attempt = _execution_attempt(
            audited_universe.next_open_execution_evidence(symbol, candidate_date, "sell"),
            "sell/%s" % candidate_date,
            trade_date=candidate_date,
            side="sell",
        )
        attempt = {"trade_date": candidate_date, **attempt}
        exit_search.append(attempt)
        if candidate_date < exit_date and attempt["fillable"]:
            raise ValueError("artifact outcome replay mismatch: earlier sell fill")
        if candidate_date == exit_date and not attempt["fillable"]:
            raise ValueError("artifact outcome replay mismatch: exit is unfillable")

    entry_attempt = _execution_attempt(
        audited_universe.next_open_execution_evidence(symbol, entry_date, "buy"),
        "buy/%s" % entry_date,
        trade_date=entry_date,
        side="buy",
    )
    if not entry_attempt["fillable"]:
        raise ValueError("artifact outcome replay mismatch: entry is unfillable")

    expected = {
        "entry_raw_price": entry_attempt["raw_price"],
        "exit_raw_price": exit_search[-1]["raw_price"],
        "return_pct": round((exit_total_return_price / entry_total_return_price - 1) * 100, 4),
        "max_adverse_pct": round(adverse, 4),
        "max_favorable_pct": round(favorable, 4),
        "mark_to_market_path": mark_to_market_path,
    }
    if compare_claim:
        _compare_claim(trade, expected)
    return {
        "claim": expected,
        "exit_search": exit_search,
        "outcome_sha256": _sha256(expected),
        "exit_search_sha256": _sha256(exit_search),
    }


def verify_artifact_trade_outcome(
    audited_universe: Any, trades: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Replay all complete outcome claims and report missing legacy claims."""

    if not isinstance(trades, list):
        raise ValueError("artifact outcome replay trades must be a list")
    claims = []
    missing = 0
    for trade in trades:
        _trade_key(trade)
        if any(field not in trade for field in _CLAIM_FIELDS):
            missing += 1
            continue
        replayed = replay_trade_outcome(audited_universe, trade, compare_claim=True)
        claims.append(
            {
                "trade_key": _trade_key(trade),
                "outcome_sha256": replayed["outcome_sha256"],
                "exit_search_sha256": replayed["exit_search_sha256"],
            }
        )
    claims.sort(key=lambda item: item["trade_key"])
    bound = bool(trades) and missing == 0 and len(claims) == len(trades)
    return {
        "schema_version": SCHEMA_VERSION,
        "method": "causal_signal_bars_exit_asof_v1",
        "exit_search": "first_fillable_sell_next_open_v1",
        "trade_count": len(trades),
        "replayed_count": len(claims),
        "missing_count": missing,
        "outcome_claims_sha256": _sha256(claims),
        "bound": bound,
        "reasons": [] if bound else ["outcome_replay_not_bound"],
    }
