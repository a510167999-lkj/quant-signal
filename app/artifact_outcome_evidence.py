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
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from numbers import Real
from typing import Any, Dict, List, Mapping, Sequence


SCHEMA_VERSION = "artifact_outcome_replay/v2"
OUTCOME_CLAIM_SCHEMA_VERSION = "artifact_outcome_claim/v2"
OUTCOME_ROUNDING_MODE = "decimal_string_half_even_pct4"
_PCT4_QUANTUM = Decimal("0.0001")
_CLAIM_FIELDS = (
    "outcome_claim_schema_version",
    "outcome_rounding_mode",
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


def _canonical_date(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"artifact outcome replay {label} is not a canonical ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"artifact outcome replay {label} is not a canonical ISO date"
        ) from exc
    if parsed.isoformat() != value:
        raise ValueError(f"artifact outcome replay {label} is not a canonical ISO date")
    return value


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
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError("artifact outcome replay %s is invalid" % label)
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("artifact outcome replay %s is invalid" % label)
    return number


def _decimal_positive(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal)):
        raise ValueError("artifact outcome claim %s is invalid" % label)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("artifact outcome claim %s is invalid" % label) from exc
    if not number.is_finite() or number <= 0:
        raise ValueError("artifact outcome claim %s is invalid" % label)
    return number


def _pct4_ratio(
    *,
    numerator_price: Decimal,
    numerator_factor: Decimal,
    denominator_price: Decimal,
    denominator_factor: Decimal,
) -> float:
    try:
        with localcontext() as context:
            context.prec = 50
            context.rounding = ROUND_HALF_EVEN
            numerator = numerator_price * numerator_factor
            denominator = denominator_price * denominator_factor
            quantized = (
                ((numerator / denominator) - Decimal(1)) * Decimal(100)
            ).quantize(_PCT4_QUANTUM, rounding=ROUND_HALF_EVEN)
    except (InvalidOperation, ZeroDivisionError) as exc:
        raise ValueError("artifact outcome claim return is invalid") from exc
    return 0.0 if quantized.is_zero() else float(quantized)


def _canonical_claim_bar(
    value: Any, *, expected_date: str
) -> Dict[str, Decimal]:
    if not isinstance(value, Mapping):
        raise ValueError("artifact outcome claim bar is invalid")
    trade_date = _canonical_date(value.get("trade_date"), "bar trade_date")
    if trade_date != expected_date:
        raise ValueError("artifact outcome claim bar date mismatch")
    factor = _decimal_positive(value.get("bar_adj_factor"), "bar adjustment factor")
    prices = {
        field: _decimal_positive(value.get(f"raw_{field}"), f"raw {field}")
        for field in ("open", "high", "low", "close")
    }
    if prices["high"] < max(prices["open"], prices["close"]):
        raise ValueError("artifact outcome claim raw high is invalid")
    if prices["low"] > min(prices["open"], prices["close"]):
        raise ValueError("artifact outcome claim raw low is invalid")
    return {"factor": factor, **prices}


def build_artifact_outcome_claim(
    *,
    bars_by_date: Mapping[str, Mapping[str, Any]],
    held_dates: Sequence[str],
    entry_date: str,
    exit_date: str,
    entry_raw_price: Any,
    exit_raw_price: Any,
) -> Dict[str, Any]:
    """Build one versioned claim from raw OHLC and causal bar factors.

    The global as-of factor cancels from every held return.  Computing the
    numerator as ``raw_price * bar_adj_factor`` gives producer and verifier one
    arithmetic path and avoids a second floating-point rebase through the
    analysis-end frame.
    """

    canonical_entry = _canonical_date(entry_date, "entry_date")
    canonical_exit = _canonical_date(exit_date, "exit_date")
    dates = [
        _canonical_date(value, "held date")
        for value in held_dates
    ]
    if (
        not dates
        or dates != sorted(set(dates))
        or dates[0] != canonical_entry
        or dates[-1] != canonical_exit
    ):
        raise ValueError("artifact outcome claim held dates are invalid")
    if any(value not in bars_by_date for value in dates):
        raise ValueError("artifact outcome claim held bar is missing")

    bars = {
        value: _canonical_claim_bar(bars_by_date[value], expected_date=value)
        for value in dates
    }
    entry_execution = _decimal_positive(entry_raw_price, "entry raw price")
    exit_execution = _decimal_positive(exit_raw_price, "exit raw price")
    if entry_execution != bars[canonical_entry]["open"]:
        raise ValueError("artifact outcome claim entry raw price mismatch")
    if exit_execution != bars[canonical_exit]["open"]:
        raise ValueError("artifact outcome claim exit raw price mismatch")

    entry_bar = bars[canonical_entry]
    mark_to_market_path: List[Dict[str, Any]] = []
    adverse = 0.0
    favorable = 0.0
    for trade_date in dates:
        bar = bars[trade_date]
        is_exit = trade_date == canonical_exit
        marks = {}
        for field in ("open", "high", "close", "low"):
            raw_price = bar["open"] if is_exit else bar[field]
            marks[f"{field}_return_pct"] = _pct4_ratio(
                numerator_price=raw_price,
                numerator_factor=bar["factor"],
                denominator_price=entry_bar["open"],
                denominator_factor=entry_bar["factor"],
            )
        row = {"date": trade_date, **marks}
        mark_to_market_path.append(row)
        adverse = min(adverse, row["low_return_pct"])
        favorable = max(favorable, row["high_return_pct"])

    exit_bar = bars[canonical_exit]
    return {
        "outcome_claim_schema_version": OUTCOME_CLAIM_SCHEMA_VERSION,
        "outcome_rounding_mode": OUTCOME_ROUNDING_MODE,
        "entry_raw_price": float(entry_execution),
        "exit_raw_price": float(exit_execution),
        "return_pct": _pct4_ratio(
            numerator_price=exit_bar["open"],
            numerator_factor=exit_bar["factor"],
            denominator_price=entry_bar["open"],
            denominator_factor=entry_bar["factor"],
        ),
        "max_adverse_pct": adverse,
        "max_favorable_pct": favorable,
        "mark_to_market_path": mark_to_market_path,
    }


def _execution_attempt(
    value: Any, label: str, *, trade_date: str, side: str
) -> Dict[str, Any]:
    if not isinstance(value, Mapping) or type(value.get("fillable")) is not bool:
        raise ValueError("artifact outcome replay %s verdict is invalid" % label)
    proof = value.get("generation_proof")
    if not isinstance(proof, Mapping) or not proof:
        raise ValueError("artifact outcome replay %s generation proof is missing" % label)
    proof_date = _canonical_date(
        proof.get("trade_date"), "%s generation trade_date" % label
    )
    if proof_date != trade_date:
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
    required = (
        "trade_date",
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "bar_adj_factor",
    )
    for row in rows:
        if not isinstance(row, Mapping) or any(row.get(field) is None for field in required):
            raise ValueError("artifact outcome replay causal bar is incomplete")
        trade_date = _canonical_date(row["trade_date"], "bar trade_date")
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
    signal_date = _canonical_date(trade.get("signal_date"), "signal_date")
    entry_date = _canonical_date(trade.get("entry_date"), "entry_date")
    planned_exit_date = _canonical_date(
        trade.get("planned_exit_date"), "planned_exit_date"
    )
    exit_date = _canonical_date(trade.get("exit_date"), "exit_date")
    if not all((symbol, signal_date, entry_date, planned_exit_date, exit_date)):
        raise ValueError("artifact outcome replay dates are incomplete")

    sessions = [
        _canonical_date(value, "open session")
        for value in audited_universe.open_sessions(signal_date, exit_date)
    ]
    if sessions != sorted(set(sessions)):
        raise ValueError("artifact outcome replay sessions are duplicated or unsorted")
    positions = {session: index for index, session in enumerate(sessions)}
    if any(value not in positions for value in (signal_date, entry_date, planned_exit_date, exit_date)):
        raise ValueError("artifact outcome replay session is missing")
    signal_position = positions[signal_date]
    entry_position = positions[entry_date]
    planned_position = positions[planned_exit_date]
    exit_position = positions[exit_date]
    if entry_position != signal_position + 1 or planned_position < entry_position or exit_position < planned_position:
        raise ValueError("artifact outcome replay session positions are invalid")

    holding_days = trade.get("holding_days")
    if type(holding_days) is not int or holding_days <= 0:
        raise ValueError("artifact outcome replay holding period is invalid")
    actual_holding_sessions = exit_position - entry_position
    planned_holding_sessions = planned_position - entry_position
    claimed_planned = trade.get("planned_holding_sessions")
    claimed_actual = trade.get("actual_holding_sessions")
    explicit_holding_semantics = claimed_planned is not None or claimed_actual is not None
    if explicit_holding_semantics and (
        type(claimed_planned) is not int or type(claimed_actual) is not int
    ):
        raise ValueError("artifact outcome replay holding period is invalid")
    if (
        planned_holding_sessions <= 0
        or (
            not explicit_holding_semantics
            and holding_days not in {planned_holding_sessions, actual_holding_sessions}
        )
        or (
            explicit_holding_semantics
            and (
                holding_days != actual_holding_sessions
                or claimed_planned != planned_holding_sessions
                or claimed_actual != actual_holding_sessions
            )
        )
    ):
        raise ValueError("artifact outcome replay holding period is invalid")

    bars = _bars_by_date(audited_universe, symbol, exit_date)
    held_dates = sessions[entry_position : exit_position + 1]
    if any(trade_date not in bars for trade_date in held_dates):
        raise ValueError("artifact outcome replay held bar is missing")

    exit_search: List[Dict[str, Any]] = []
    for candidate_date in sessions[planned_position : exit_position + 1]:
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

    expected = build_artifact_outcome_claim(
        bars_by_date=bars,
        held_dates=held_dates,
        entry_date=entry_date,
        exit_date=exit_date,
        entry_raw_price=entry_attempt["raw_price"],
        exit_raw_price=exit_search[-1]["raw_price"],
    )
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
        claim_schema = trade.get("outcome_claim_schema_version")
        legacy_outcome_fields = (
            "return_pct",
            "max_adverse_pct",
            "max_favorable_pct",
            "mark_to_market_path",
        )
        if claim_schema is None:
            if trade.get("outcome_rounding_mode") is not None or any(
                field in trade for field in legacy_outcome_fields
            ):
                raise ValueError("artifact outcome replay claim schema is missing")
            missing += 1
            continue
        if claim_schema != OUTCOME_CLAIM_SCHEMA_VERSION:
            raise ValueError("artifact outcome replay claim schema is unsupported")
        if any(field not in trade for field in _CLAIM_FIELDS):
            raise ValueError("artifact outcome replay claim is incomplete")
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
        "method": "raw_ohlc_bar_factor_ratio_v2",
        "outcome_claim_schema_version": OUTCOME_CLAIM_SCHEMA_VERSION,
        "rounding_mode": OUTCOME_ROUNDING_MODE,
        "exit_search": "first_fillable_sell_next_open_v1",
        "trade_count": len(trades),
        "replayed_count": len(claims),
        "missing_count": missing,
        "outcome_claims_sha256": _sha256(claims),
        "bound": bound,
        "reasons": [] if bound else ["outcome_replay_not_bound"],
    }
