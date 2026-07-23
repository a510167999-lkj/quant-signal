import json
import sqlite3
from pathlib import Path

import pandas as pd

import app.audited_pit_development_replay as replay
from app.config import Settings


class _AuditedUniverse:
    start_date = "2024-07-05"
    end_date = "2026-07-03"
    coverage_audit_sha256 = "a" * 64
    artifact_root_sha256 = "b" * 64
    temporal_contract_sha256 = (
        "30242ba7bae313ff369d4c90bf21060ced93f17a5b5a9f8e7e5e7573301f6934"
    )
    temporal_role = "development"

    def __init__(self):
        self.closed = False
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE receipts (dataset TEXT, partition_key TEXT);
            CREATE TABLE membership_session_head (trade_date TEXT);
            CREATE TABLE daily_universe (
                trade_date TEXT, ts_code TEXT, name TEXT
            );
            INSERT INTO receipts VALUES ('bak_basic', '2025-01-21');
            INSERT INTO daily_universe
            VALUES ('2025-01-21', '000001.SZ', '历史测试股');
            """
        )

    def _require_open(self):
        return self.connection

    def open_sessions(self, start_date, end_date):
        assert (start_date, end_date) == (self.start_date, self.end_date)
        return ["2025-01-21"]

    def close(self):
        self.closed = True
        self.connection.close()


def _bars():
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
    return pd.DataFrame(rows)


def test_audited_replay_end_to_end_binds_exact_membership(monkeypatch, tmp_path):
    contract_path = Path("data/research_partitions/frozen-v2.json")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    universe = _AuditedUniverse()
    captured_loader = {}

    def open_universe(path, **kwargs):
        captured_loader.update({"path": path, **kwargs})
        return universe

    monkeypatch.setattr(
        replay.AuditedPointInTimeUniverse,
        "from_file",
        open_universe,
    )
    monkeypatch.setattr(
        replay,
        "_load_exact_membership_bars",
        lambda *_args, **_kwargs: _bars().assign(membership_name="历史测试股"),
    )
    monkeypatch.setattr(
        replay,
        "sweep_qualified_trades",
        lambda trades, **kwargs: {
            "qualified_trade_count": len(trades),
            "target_all_pass_count": 0,
            "settings": kwargs,
        },
    )
    monkeypatch.setattr(
        replay,
        "_write_content_addressed",
        lambda output_dir, payload: {
            "path": str(Path(output_dir) / "result.json"),
            "artifact_sha256": replay._sha256(payload),
        },
    )

    result = replay.run_audited_pit_development_replay(
        settings=Settings(max_entry_gap_up_pct=30.0),
        audited_pit_universe_path=tmp_path / "audited.sqlite3",
        expected_coverage_audit_sha256="a" * 64,
        expected_artifact_root_sha256="b" * 64,
        temporal_contract_path=contract_path,
        expected_temporal_contract_sha256=contract["contract_sha256"],
        start_date="2024-07-05",
        end_date="2026-07-03",
        output_dir=tmp_path,
    )

    assert universe.closed is True
    assert captured_loader["expected_coverage_audit_sha256"] == "a" * 64
    assert captured_loader["expected_artifact_root_sha256"] == "b" * 64
    assert result["qualified_trade_count"] == 1
    assert result["scope"] == {
        "point_in_time": True,
        "current_universe_bias": False,
        "exact_membership_required": True,
        "development_only": True,
        "final_oos_consumed": False,
        "eligible_for_profile_registration": False,
        "production_recommendation_eligible": False,
    }
    assert result["source"]["exact_membership_session_count"] == 1
    assert result["source"]["producer_code"]["root_sha256"]
    assert result["sweep"]["settings"]["fixed_spec"] is True


def test_exact_membership_bar_query_keeps_post_signal_market_path():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE market_session_generation_head (
            trade_date TEXT, generation_id TEXT
        );
        CREATE TABLE market_session_generation_rows_daily (
            generation_id TEXT, trade_date TEXT, ts_code TEXT,
            open REAL, high REAL, low REAL, close REAL, pre_close REAL,
            amount REAL
        );
        CREATE TABLE market_session_generation_rows_adj_factor (
            generation_id TEXT, trade_date TEXT, ts_code TEXT, adj_factor REAL
        );
        CREATE TABLE market_session_generation_rows_suspend_d (
            generation_id TEXT, trade_date TEXT, ts_code TEXT,
            suspend_type TEXT
        );
        CREATE TABLE daily_universe (
            trade_date TEXT, ts_code TEXT, name TEXT
        );
        INSERT INTO market_session_generation_head VALUES ('2025-01-02', 'g1');
        INSERT INTO market_session_generation_rows_daily VALUES
            ('g1', '2025-01-02', '000001.SZ', 10, 11, 9, 10.5, 10, 100),
            ('g1', '2025-01-02', '000002.SZ', 10, 11, 9, 10.5, 10, 100),
            ('g1', '2025-01-02', '000003.SZ', 10, 11, 9, 10.5, 10, 100),
            ('g1', '2025-01-02', '688001.SH', 10, 11, 9, 10.5, 10, 100);
        INSERT INTO market_session_generation_rows_adj_factor VALUES
            ('g1', '2025-01-02', '000001.SZ', 1),
            ('g1', '2025-01-02', '000002.SZ', 1),
            ('g1', '2025-01-02', '000003.SZ', 1),
            ('g1', '2025-01-02', '688001.SH', 1);
        INSERT INTO daily_universe VALUES
            ('2025-01-02', '000001.SZ', '历史正常股'),
            ('2025-01-02', '000002.SZ', '*ST历史股'),
            ('2025-01-02', '688001.SH', '科创样本');
        """
    )

    bars = replay._load_exact_membership_bars(
        connection,
        start_date="2025-01-02",
        end_date="2025-01-02",
    )

    assert bars["ts_code"].tolist() == ["000001.SZ", "000002.SZ", "000003.SZ"]
    assert bars.loc[bars["ts_code"] == "000001.SZ", "membership_name"].notna().all()
    assert bars.loc[bars["ts_code"] == "000002.SZ", "membership_name"].notna().all()
    assert bars.loc[bars["ts_code"] == "000003.SZ", "membership_name"].isna().all()
