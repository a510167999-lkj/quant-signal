import sqlite3

import pandas as pd
import pytest

from app.audited_pit_development_replay import (
    AuditedPITDevelopmentReplayError,
    _exact_membership_by_date,
)
from app.config import Settings
from app.current_pool_development_replay import (
    SIMPLE_BREAKOUT_SPEC,
    _candidate_trades_from_bars,
)


def test_simple_current_pool_replay_uses_only_breakout_and_basic_stop():
    rows = []
    for index in range(27):
        close = 10.0 if index < 20 else 11.0
        rows.append(
            {
                "date": f"2025-01-{index + 1:02d}",
                "ts_code": "000001.SZ",
                "open": 10.4 if index == 21 else close,
                "high": 11.0 if index >= 20 else 10.0,
                "low": 9.0 if index == 21 else 10.0,
                "close": close,
                "pre_close": 10.0,
                "amount": 100_000_000.0,
                "adj_factor": 1.0,
                "suspended": False,
            }
        )

    trades = _candidate_trades_from_bars(
        pd.DataFrame(rows), {"000001": "测试股"}, Settings()
    )

    assert trades
    first = trades[0]
    assert first["signal_date"] == "2025-01-21"
    assert first["signal_tags"] == ["breakout_20d"]
    assert first["exit_reason"] == "stop_loss"
    assert first["current_universe_bias"] is True
    assert SIMPLE_BREAKOUT_SPEC["exposure_multiplier"] == 1.0
    assert SIMPLE_BREAKOUT_SPEC["capital_model"] == "slot-daily"
    assert SIMPLE_BREAKOUT_SPEC["entry_execution"]["max_gap_up_pct"] == 6.0


def test_simple_replay_uses_exact_signal_date_membership():
    rows = []
    for index in range(27):
        close = 10.0 if index < 20 else 11.0
        rows.append(
            {
                "date": f"2025-01-{index + 1:02d}",
                "ts_code": "000001.SZ",
                "open": close,
                "high": 11.0 if index >= 20 else 10.0,
                "low": close,
                "close": close,
                "pre_close": 10.0,
                "amount": 100_000_000.0,
                "adj_factor": 1.0,
                "suspended": False,
            }
        )

    absent = _candidate_trades_from_bars(
        pd.DataFrame(rows),
        {},
        Settings(max_entry_gap_up_pct=30.0),
        membership_by_date={},
        current_universe_bias=False,
    )
    present = _candidate_trades_from_bars(
        pd.DataFrame(rows),
        {},
        Settings(max_entry_gap_up_pct=30.0),
        membership_by_date={"2025-01-21": {"000001": "历史名称"}},
        current_universe_bias=False,
    )

    assert absent == []
    assert present
    assert present[0]["name"] == "历史名称"
    assert present[0]["current_universe_bias"] is False
    assert present[0]["market_level"] == "audited_pit_development"


class _MembershipUniverse:
    def __init__(self, connection):
        self._connection = connection

    def _require_open(self):
        return self._connection

    def open_sessions(self, start_date, end_date):
        return ["2025-01-02"]


def test_exact_membership_rejects_derived_session():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE receipts (dataset TEXT, partition_key TEXT);
        CREATE TABLE membership_session_head (trade_date TEXT);
        CREATE TABLE daily_universe (
            trade_date TEXT, ts_code TEXT, name TEXT
        );
        INSERT INTO receipts VALUES ('bak_basic', '2025-01-02');
        INSERT INTO membership_session_head VALUES ('2025-01-02');
        INSERT INTO daily_universe VALUES ('2025-01-02', '000001.SZ', '测试股');
        """
    )

    with pytest.raises(
        AuditedPITDevelopmentReplayError,
        match="derived or quarantined",
    ):
        _exact_membership_by_date(
            _MembershipUniverse(connection),
            start_date="2025-01-02",
            end_date="2025-01-02",
        )
