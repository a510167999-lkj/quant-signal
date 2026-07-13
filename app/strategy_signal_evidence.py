"""Deterministic replay evidence for the strategy signal itself.

The PIT artifact already exposes causal bars and execution receipts.  This
module binds the qualified-trade signal claim to the exact ``evaluate_signal``
implementation that produced it, then replays that implementation from the
artifact-only causal bars.  Missing claims remain an explicit blocker; this
module never upgrades an old cache into strict evidence.
"""

from __future__ import annotations

import hashlib
import inspect
import json
from typing import Any, Dict, List, Mapping

import pandas as pd

from app.indicators import add_indicators
from app.signal_tags import build_signal_tags
from app.signals import evaluate_signal


SCHEMA_VERSION = "strategy_signal_replay/v1"


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


def signal_evaluator_identity() -> Dict[str, str]:
    """Return a stable identity for the signal evaluator, without a path."""

    source = inspect.getsource(evaluate_signal).encode("utf-8")
    return {
        "module": str(evaluate_signal.__module__),
        "qualname": str(evaluate_signal.__qualname__),
        "source_sha256": hashlib.sha256(source).hexdigest(),
    }


def build_signal_snapshot(signal: Mapping[str, Any]) -> Dict[str, Any]:
    """Keep only deterministic strategy claims needed for an artifact replay."""

    if not isinstance(signal, Mapping):
        raise ValueError("strategy signal claim must be an object")
    as_of = str(signal.get("as_of") or "")[:10]
    if not as_of or not signal.get("action"):
        raise ValueError("strategy signal claim lacks as_of/action")
    indicators = signal.get("indicators")
    if not isinstance(indicators, Mapping):
        raise ValueError("strategy signal claim lacks indicators")
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "evaluator": signal_evaluator_identity(),
        "as_of": as_of,
        "action": str(signal.get("action")),
        "score": float(signal.get("score")),
        "confidence": int(signal.get("confidence")),
        "signal_tags": build_signal_tags(dict(signal)),
        "indicators": dict(indicators),
    }
    # These text claims are part of the displayed operation advice.  They are
    # replayed as well, but are kept bounded to avoid unbounded trade artifacts.
    for field in ("reasons", "risks", "confirmations"):
        value = signal.get(field) or []
        if not isinstance(value, list):
            raise ValueError("strategy signal %s must be a list" % field)
        snapshot[field] = [str(item) for item in value[:5]]
    return snapshot


def _signal_frame(audited_universe: Any, symbol: str, signal_date: str) -> pd.DataFrame:
    start_date = str(getattr(audited_universe, "start_date", signal_date))[:10]
    rows = audited_universe.causal_signal_bars(symbol, start_date, signal_date)
    if not isinstance(rows, list) or not rows:
        raise ValueError("strategy signal replay causal bars are missing")
    frame_rows = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("strategy signal replay bar is invalid")
        required = ("trade_date", "signal_open", "signal_high", "signal_low", "signal_close", "volume_shares")
        if any(row.get(field) is None for field in required):
            raise ValueError("strategy signal replay bar is incomplete")
        frame_rows.append(
            {
                "date": str(row["trade_date"])[:10],
                "open": float(row["signal_open"]),
                "high": float(row["signal_high"]),
                "low": float(row["signal_low"]),
                "close": float(row["signal_close"]),
                "volume": float(row["volume_shares"]),
            }
        )
    frame = pd.DataFrame(frame_rows).sort_values("date", kind="mergesort").reset_index(drop=True)
    if frame.empty or str(frame.iloc[-1]["date"]) != signal_date:
        raise ValueError("strategy signal replay causal bars do not end on signal date")
    return add_indicators(frame)


def replay_signal_snapshot(
    audited_universe: Any,
    symbol: str,
    signal_date: str,
    claimed: Mapping[str, Any],
) -> Dict[str, Any]:
    """Replay one claimed signal from causal artifact bars and compare exactly."""

    if not isinstance(claimed, Mapping) or claimed.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("strategy signal replay claim is missing or unsupported")
    expected_identity = signal_evaluator_identity()
    if claimed.get("evaluator") != expected_identity:
        raise ValueError("strategy signal replay evaluator identity mismatch")
    normalized_date = str(signal_date or "")[:10]
    if claimed.get("as_of") != normalized_date:
        raise ValueError("strategy signal replay signal date mismatch")
    frame = _signal_frame(audited_universe, str(symbol), normalized_date)
    replayed = build_signal_snapshot(evaluate_signal(frame))
    if _canonical(replayed) != _canonical(dict(claimed)):
        raise ValueError("strategy signal replay mismatch")
    return {
        "verified": True,
        "evaluator": expected_identity,
        "signal_sha256": _sha256(replayed),
    }


def _trade_key(trade: Mapping[str, Any]) -> str:
    symbol = str(trade.get("symbol") or "")
    signal_date = str(trade.get("signal_date") or "")[:10]
    entry_date = str(trade.get("entry_date") or "")[:10]
    exit_date = str(trade.get("exit_date") or "")[:10]
    if not all((symbol, signal_date, entry_date, exit_date)):
        raise ValueError("strategy signal replay trade key is incomplete")
    return "|".join((symbol, signal_date, entry_date, exit_date))


def verify_strategy_signal_replay(
    audited_universe: Any, trades: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """Replay all present signal claims and report missing claims as a blocker."""

    if not isinstance(trades, list):
        raise ValueError("strategy signal replay trades must be a list")
    claims = []
    missing = 0
    for trade in trades:
        key = _trade_key(trade)
        claimed = trade.get("strategy_signal")
        if claimed is None:
            missing += 1
            continue
        replayed = replay_signal_snapshot(
            audited_universe,
            str(trade.get("symbol")),
            str(trade.get("signal_date"))[:10],
            claimed,
        )
        claims.append({"trade_key": key, "signal_sha256": replayed["signal_sha256"]})
    claims.sort(key=lambda item: item["trade_key"])
    bound = bool(trades) and missing == 0 and len(claims) == len(trades)
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluator": signal_evaluator_identity(),
        "trade_count": len(trades),
        "replayed_count": len(claims),
        "missing_count": missing,
        "signal_claims_sha256": _sha256(claims),
        "bound": bound,
        "reasons": [] if bound else ["strategy_signal_replay_not_bound"],
    }

