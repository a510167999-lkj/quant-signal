import pandas as pd
import pytest

from app.indicators import add_indicators
from app.signals import evaluate_signal
from app.strategy_signal_evidence import (
    build_signal_snapshot,
    replay_signal_snapshot,
    signal_evaluator_identity,
)


def _bars(periods=100):
    dates = pd.date_range("2020-01-01", periods=periods, freq="B")
    close = []
    price = 10.0
    for index in range(periods):
        price *= 1.0015 if index < periods - 15 else 1.004
        close.append(price)
    return [
        {
            "trade_date": date.strftime("%Y-%m-%d"),
            "signal_open": value * 0.995,
            "signal_high": value * 1.01,
            "signal_low": value * 0.99,
            "signal_close": value,
            "volume_shares": 1_000_000 + index * 1_000,
        }
        for index, (date, value) in enumerate(zip(dates, close))
    ]


class _Audited:
    start_date = "2020-01-01"

    def __init__(self):
        self.bars = _bars()

    def causal_signal_bars(self, symbol, start_date, as_of_date):
        return [row for row in self.bars if row["trade_date"] <= str(as_of_date)[:10]]


def _snapshot(audited):
    bars = audited.causal_signal_bars("600001", audited.start_date, audited.bars[-1]["trade_date"])
    frame = pd.DataFrame(
        [
            {
                "date": row["trade_date"],
                "open": row["signal_open"],
                "high": row["signal_high"],
                "low": row["signal_low"],
                "close": row["signal_close"],
                "volume": row["volume_shares"],
            }
            for row in bars
        ]
    )
    return build_signal_snapshot(evaluate_signal(add_indicators(frame)))


def test_strategy_signal_snapshot_replays_against_causal_artifact():
    audited = _Audited()
    snapshot = _snapshot(audited)

    result = replay_signal_snapshot(
        audited,
        "600001",
        audited.bars[-1]["trade_date"],
        snapshot,
    )

    assert result["verified"] is True
    assert result["evaluator"] == signal_evaluator_identity()
    assert result["signal_sha256"]


def test_strategy_signal_snapshot_rejects_claim_tampering():
    audited = _Audited()
    snapshot = _snapshot(audited)
    snapshot["score"] = float(snapshot["score"]) + 1

    with pytest.raises(ValueError, match="strategy signal replay mismatch"):
        replay_signal_snapshot(
            audited,
            "600001",
            audited.bars[-1]["trade_date"],
            snapshot,
        )

