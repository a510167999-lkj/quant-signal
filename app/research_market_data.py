"""Causal market-evidence contracts for strict A-share research replay.

Execution prices always remain raw and unadjusted. Adjustment factors may only
create signal inputs normalized to the signal-date factor; later factors are
excluded by construction. Tradability gates fail closed when a raw bar, daily
price limit, or suspension state is not proven.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence


class MarketEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class MarketSessionSpec:
    dataset: str
    partition_key: str
    api_name: str
    wire_params: Mapping[str, str]
    fields: tuple[str, ...]
    row_cap: int
    price_basis: str


MARKET_SESSION_CONTRACTS = (
    (
        "daily",
        (
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "change",
            "pct_chg",
            "vol",
            "amount",
        ),
        6000,
        "raw_unadjusted_execution",
    ),
    (
        "adj_factor",
        ("ts_code", "trade_date", "adj_factor"),
        6000,
        "causal_signal_input_only",
    ),
    (
        "stk_limit",
        ("trade_date", "ts_code", "pre_close", "up_limit", "down_limit"),
        5800,
        "tradability_constraint",
    ),
    (
        "suspend_d",
        ("ts_code", "trade_date", "suspend_timing", "suspend_type"),
        5000,
        "tradability_constraint",
    ),
)


def _iso_date(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise MarketEvidenceError(f"invalid {field}: {value!r}") from exc


def _positive_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketEvidenceError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise MarketEvidenceError(f"{field} must be positive and finite")
    return number


def build_market_session_specs(trade_date: Any) -> list[MarketSessionSpec]:
    session = _iso_date(trade_date, "trade_date")
    wire_date = session.replace("-", "")
    return [
        MarketSessionSpec(
            dataset=dataset,
            partition_key=session,
            api_name=dataset,
            wire_params={"trade_date": wire_date},
            fields=fields,
            row_cap=row_cap,
            price_basis=price_basis,
        )
        for dataset, fields, row_cap, price_basis in MARKET_SESSION_CONTRACTS
    ]


def causal_adjusted_bars(
    raw_bars: Sequence[Mapping[str, Any]],
    adjustment_factors: Sequence[Mapping[str, Any]],
    *,
    as_of_date: Any,
) -> list[dict[str, Any]]:
    """Return signal-price bars normalized only with factors known by ``as_of``.

    Raw OHLC values are copied to explicit ``raw_*`` fields and never replaced.
    The adjusted series uses ``raw_price * factor_on_bar / latest_factor_on_or_before_as_of``.
    """

    as_of = _iso_date(as_of_date, "as_of_date")
    factor_by_key: dict[tuple[str, str], float] = {}
    latest_factor_by_symbol: dict[str, tuple[str, float]] = {}
    for row in adjustment_factors:
        symbol = str(row.get("ts_code") or "").strip()
        session = _iso_date(row.get("trade_date"), "adjustment trade_date")
        if not symbol:
            raise MarketEvidenceError("adjustment factor symbol is missing")
        key = (symbol, session)
        if key in factor_by_key:
            raise MarketEvidenceError("duplicate adjustment factor row")
        factor = _positive_number(row.get("adj_factor"), "adjustment factor")
        factor_by_key[key] = factor
        latest = latest_factor_by_symbol.get(symbol)
        if session <= as_of and (latest is None or session > latest[0]):
            latest_factor_by_symbol[symbol] = (session, factor)

    normalized = []
    seen_bars = set()
    for row in raw_bars:
        symbol = str(row.get("ts_code") or "").strip()
        session = _iso_date(row.get("trade_date"), "raw bar trade_date")
        if not symbol:
            raise MarketEvidenceError("raw bar symbol is missing")
        if session > as_of:
            continue
        key = (symbol, session)
        if key in seen_bars:
            raise MarketEvidenceError("duplicate raw bar row")
        seen_bars.add(key)
        bar_factor = factor_by_key.get(key)
        as_of_factor_row = latest_factor_by_symbol.get(symbol)
        if bar_factor is None or as_of_factor_row is None:
            raise MarketEvidenceError("missing causal adjustment factor")
        adjustment_as_of_date, as_of_factor = as_of_factor_row
        raw_prices = {
            name: _positive_number(row.get(name), f"raw {name}")
            for name in ("open", "high", "low", "close")
        }
        if raw_prices["high"] < max(raw_prices["open"], raw_prices["close"]):
            raise MarketEvidenceError("raw high is below open or close")
        if raw_prices["low"] > min(raw_prices["open"], raw_prices["close"]):
            raise MarketEvidenceError("raw low is above open or close")
        ratio = bar_factor / as_of_factor
        normalized.append(
            {
                "ts_code": symbol,
                "trade_date": session,
                **{f"raw_{name}": value for name, value in raw_prices.items()},
                **{
                    f"signal_{name}": value * ratio
                    for name, value in raw_prices.items()
                },
                "bar_adj_factor": bar_factor,
                "as_of_adj_factor": as_of_factor,
                "adjustment_as_of_date": adjustment_as_of_date,
            }
        )
    normalized.sort(key=lambda row: (row["ts_code"], row["trade_date"]))
    return normalized


def next_open_fill_gate(
    *,
    side: str,
    raw_bar: Mapping[str, Any] | None,
    price_limit: Mapping[str, Any] | None,
    suspension: Mapping[str, Any] | None,
) -> dict[str, Any]:
    normalized_side = str(side or "").strip().lower()
    if normalized_side not in {"buy", "sell"}:
        raise MarketEvidenceError("fill side must be buy or sell")
    def rejected(reason: str) -> dict[str, Any]:
        return {"fillable": False, "reason": reason, "raw_price": None}
    if raw_bar is None:
        return rejected("missing_raw_bar")
    if price_limit is None:
        return rejected("missing_price_limit")
    if suspension is not None and str(
        suspension.get("suspend_type") or ""
    ).strip().upper() == "S":
        return rejected("suspended")

    raw_open = _positive_number(raw_bar.get("open"), "raw open")
    up_limit = _positive_number(price_limit.get("up_limit"), "up limit")
    down_limit = _positive_number(price_limit.get("down_limit"), "down limit")
    if down_limit >= up_limit:
        raise MarketEvidenceError("price limit interval is invalid")
    tolerance = max(1.0, abs(raw_open), abs(up_limit), abs(down_limit)) * 1e-9
    if normalized_side == "buy" and raw_open >= up_limit - tolerance:
        return rejected("buy_open_locked_limit")
    if normalized_side == "sell" and raw_open <= down_limit + tolerance:
        return rejected("sell_open_locked_limit")
    return {"fillable": True, "reason": "raw_open", "raw_price": raw_open}
