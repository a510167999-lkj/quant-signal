import json
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_EVEN, ROUND_UP, localcontext

import pytest

from app.artifact_outcome_evidence import (
    OUTCOME_CLAIM_SCHEMA_VERSION,
    OUTCOME_ROUNDING_MODE,
    build_artifact_outcome_claim,
    replay_trade_outcome,
    verify_artifact_trade_outcome,
)


class _Audited:
    start_date = "2020-01-01"

    def open_sessions(self, start_date, end_date):
        return ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"]

    def causal_signal_bars(self, symbol, start_date, as_of_date):
        rows = [
            {
                "trade_date": "2020-01-01",
                "signal_open": 99.0,
                "signal_high": 100.0,
                "signal_low": 98.0,
                "signal_close": 99.0,
            },
            {
                "trade_date": "2020-01-02",
                "signal_open": 100.0,
                "signal_high": 105.0,
                "signal_low": 99.0,
                "signal_close": 103.0,
            },
            {
                "trade_date": "2020-01-03",
                "signal_open": 103.0,
                "signal_high": 108.0,
                "signal_low": 101.0,
                "signal_close": 106.0,
            },
            {
                "trade_date": "2020-01-04",
                "signal_open": 105.0,
                "signal_high": 107.0,
                "signal_low": 104.0,
                "signal_close": 106.0,
            },
        ]
        for row in rows:
            row["bar_adj_factor"] = 1.0
            for field in ("open", "high", "low", "close"):
                row[f"raw_{field}"] = row[f"signal_{field}"]
        return rows

    def next_open_execution_evidence(self, symbol, trade_date, side):
        if side == "buy":
            return {
                "fillable": True,
                "reason": "raw_open",
                "raw_price": 100.0,
                "generation_proof": {"trade_date": trade_date, "side": side},
            }
        return {
            "fillable": trade_date == "2020-01-04",
            "reason": "raw_open" if trade_date == "2020-01-04" else "suspended",
            "raw_price": 105.0 if trade_date == "2020-01-04" else None,
            "generation_proof": {"trade_date": trade_date, "side": side},
        }


def _trade():
    return {
        "symbol": "600001",
        "signal_date": "2020-01-01",
        "entry_date": "2020-01-02",
        "planned_exit_date": "2020-01-03",
        "exit_date": "2020-01-04",
        "holding_days": 1,
        "entry_raw_price": 100.0,
        "exit_raw_price": 105.0,
    }


def _claimed_trade():
    trade = _trade()
    trade.update(replay_trade_outcome(_Audited(), trade, compare_claim=False)["claim"])
    return trade


def test_outcome_replay_recomputes_return_path_and_blocked_sell_retry():
    result = verify_artifact_trade_outcome(_Audited(), [_claimed_trade()])

    assert result["bound"] is True
    assert result["replayed_count"] == 1
    assert result["missing_count"] == 0
    assert result["outcome_claims_sha256"]


@pytest.mark.parametrize(
    "field",
    [
        "outcome_claim_schema_version",
        "outcome_rounding_mode",
        "entry_raw_price",
        "exit_raw_price",
        "return_pct",
        "max_adverse_pct",
        "max_favorable_pct",
        "mark_to_market_path",
    ],
)
def test_outcome_replay_rejects_each_tampered_v2_claim_field(field):
    trade = _claimed_trade()
    if field in {"outcome_claim_schema_version", "outcome_rounding_mode"}:
        trade[field] = "tampered"
    elif field == "mark_to_market_path":
        path = deepcopy(trade[field])
        path[0]["close_return_pct"] += 0.0001
        trade[field] = path
    else:
        trade[field] += 0.0001

    with pytest.raises(ValueError, match="artifact outcome replay mismatch"):
        replay_trade_outcome(_Audited(), trade, compare_claim=True)


def test_outcome_replay_reports_missing_legacy_claims_as_blocker():
    result = verify_artifact_trade_outcome(_Audited(), [_trade()])

    assert result["bound"] is False
    assert result["missing_count"] == 1
    assert result["reasons"] == ["outcome_replay_not_bound"]


def test_outcome_replay_v2_declares_arithmetic_and_rounding_contract():
    result = verify_artifact_trade_outcome(_Audited(), [_claimed_trade()])

    assert result["schema_version"] == "artifact_outcome_replay/v2"
    assert result["method"] == "raw_ohlc_bar_factor_ratio_v2"
    assert result["outcome_claim_schema_version"] == OUTCOME_CLAIM_SCHEMA_VERSION
    assert result["rounding_mode"] == OUTCOME_ROUNDING_MODE


def test_outcome_claim_normalizes_signed_zero():
    bars = {
        trade_date: {
            "trade_date": trade_date,
            "raw_open": 10.0,
            "raw_high": 10.0,
            "raw_low": 10.0,
            "raw_close": 10.0,
            "bar_adj_factor": 1.0,
        }
        for trade_date in ("2020-01-02", "2020-01-03")
    }

    claim = build_artifact_outcome_claim(
        bars_by_date=bars,
        held_dates=["2020-01-02", "2020-01-03"],
        entry_date="2020-01-02",
        exit_date="2020-01-03",
        entry_raw_price=10.0,
        exit_raw_price=10.0,
    )

    assert "-0.0" not in json.dumps(claim, sort_keys=True)
    assert claim["return_pct"] == 0.0
    assert claim["mark_to_market_path"][0]["low_return_pct"] == 0.0


def test_outcome_claim_has_independent_half_even_pct4_oracle():
    bars = {
        "2020-01-02": {
            "trade_date": "2020-01-02",
            "raw_open": Decimal("10000"),
            "raw_high": Decimal("10000"),
            "raw_low": Decimal("10000"),
            "raw_close": Decimal("10000"),
            "bar_adj_factor": Decimal("1"),
        },
        "2020-01-03": {
            "trade_date": "2020-01-03",
            "raw_open": Decimal("10010.145"),
            "raw_high": Decimal("10010.145"),
            "raw_low": Decimal("10010.145"),
            "raw_close": Decimal("10010.145"),
            "bar_adj_factor": Decimal("1"),
        },
    }

    claim = build_artifact_outcome_claim(
        bars_by_date=bars,
        held_dates=["2020-01-02", "2020-01-03"],
        entry_date="2020-01-02",
        exit_date="2020-01-03",
        entry_raw_price=Decimal("10000"),
        exit_raw_price=Decimal("10010.145"),
    )

    assert claim["return_pct"] == 0.1014
    assert claim["max_adverse_pct"] == 0.0
    assert claim["max_favorable_pct"] == 0.1014
    assert set(claim["mark_to_market_path"][1].values()) == {
        "2020-01-03",
        0.1014,
    }


def test_outcome_claim_is_independent_of_process_decimal_precision():
    bars = {
        trade_date: {
            "trade_date": trade_date,
            "raw_open": raw_open,
            "raw_high": raw_open,
            "raw_low": raw_open,
            "raw_close": raw_open,
            "bar_adj_factor": factor,
        }
        for trade_date, raw_open, factor in (
            ("2020-01-02", Decimal("49.83642340716134"), Decimal("0.8982917896085058")),
            ("2020-01-03", Decimal("32.28072976306658"), Decimal("1.3726677763186674")),
        )
    }
    kwargs = {
        "bars_by_date": bars,
        "held_dates": ["2020-01-02", "2020-01-03"],
        "entry_date": "2020-01-02",
        "exit_date": "2020-01-03",
        "entry_raw_price": Decimal("49.83642340716134"),
        "exit_raw_price": Decimal("32.28072976306658"),
    }

    claims = []
    for precision, rounding in (
        (6, ROUND_DOWN),
        (28, ROUND_HALF_EVEN),
        (42, ROUND_UP),
    ):
        with localcontext() as context:
            context.prec = precision
            context.rounding = rounding
            claims.append(build_artifact_outcome_claim(**kwargs))

    assert claims[0] == claims[1] == claims[2]


@pytest.mark.parametrize("invalid", [None, True, "1.0", float("nan"), float("inf")])
def test_outcome_claim_rejects_invalid_adjustment_factor(invalid):
    bars = {
        "2020-01-02": {
            "trade_date": "2020-01-02",
            "raw_open": 10.0,
            "raw_high": 10.0,
            "raw_low": 10.0,
            "raw_close": 10.0,
            "bar_adj_factor": invalid,
        }
    }

    with pytest.raises(ValueError, match="bar adjustment factor"):
        build_artifact_outcome_claim(
            bars_by_date=bars,
            held_dates=["2020-01-02"],
            entry_date="2020-01-02",
            exit_date="2020-01-02",
            entry_raw_price=10.0,
            exit_raw_price=10.0,
        )


@pytest.mark.parametrize("invalid", [True, "1", 1.9])
def test_outcome_replay_rejects_coerced_holding_period(invalid):
    trade = _claimed_trade()
    trade["holding_days"] = invalid

    with pytest.raises(ValueError, match="holding period"):
        replay_trade_outcome(_Audited(), trade, compare_claim=True)


def test_outcome_replay_rejects_partial_v2_claim():
    trade = _claimed_trade()
    trade.pop("outcome_rounding_mode")

    with pytest.raises(ValueError, match="claim is incomplete"):
        verify_artifact_trade_outcome(_Audited(), [trade])


def test_outcome_replay_rejects_rounding_contract_without_claim_schema():
    trade = _trade()
    trade["outcome_rounding_mode"] = OUTCOME_ROUNDING_MODE

    with pytest.raises(ValueError, match="claim schema is missing"):
        verify_artifact_trade_outcome(_Audited(), [trade])
