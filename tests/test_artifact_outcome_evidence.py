import pytest

from app.artifact_outcome_evidence import (
    replay_trade_outcome,
    verify_artifact_trade_outcome,
)


class _Audited:
    start_date = "2020-01-01"

    def open_sessions(self, start_date, end_date):
        return ["2020-01-01", "2020-01-02", "2020-01-03", "2020-01-04"]

    def causal_signal_bars(self, symbol, start_date, as_of_date):
        return [
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


def test_outcome_replay_rejects_tampered_return_or_path():
    trade = _claimed_trade()
    trade["return_pct"] = 99.0

    with pytest.raises(ValueError, match="artifact outcome replay mismatch"):
        verify_artifact_trade_outcome(_Audited(), [trade])


def test_outcome_replay_reports_missing_legacy_claims_as_blocker():
    result = verify_artifact_trade_outcome(_Audited(), [_trade()])

    assert result["bound"] is False
    assert result["missing_count"] == 1
    assert result["reasons"] == ["outcome_replay_not_bound"]

