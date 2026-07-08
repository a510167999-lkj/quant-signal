from types import SimpleNamespace

import pandas as pd

from app.performance import evaluate_recommendation_performance
from app.storage import append_jsonl
from tests.test_signals import sample_frame


def _strategy_settings():
    return SimpleNamespace(
        performance_strategy_hold_days=5,
        monitor_profit_lock_activation_pct=18.0,
        monitor_pre_exit_calendar_gap_days=7,
    )


class FakeProvider:
    def history(self, symbol, market, lookback_days=900, adjust="qfq"):
        return sample_frame("up", periods=80), "fake-provider"


def test_evaluate_recommendation_performance_tracks_next_bar_open(tmp_path):
    history_path = tmp_path / "recommendations_history.jsonl"
    append_jsonl(
        str(history_path),
        {
            "generated_at": "2025-01-10T09:00:00+08:00",
            "trade_date": "2025-01-10",
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "as_of": "2025-01-10",
                    "action": "BUY",
                    "score": 3.5,
                }
            ],
        },
    )

    result = evaluate_recommendation_performance(
        str(history_path), FakeProvider(), _strategy_settings(), limit=100
    )

    assert result["summary"]["total_recommendations"] == 1
    assert result["summary"]["matured_10d_count"] == 1
    assert result["summary"]["win_rate_10d_pct"] == 100
    item = result["items"][0]
    assert item["entry_date"] > "2025-01-10"
    assert item["return_10d_pct"] > 0
    assert item["source"] == "fake-provider"


class _FrameProvider:
    def __init__(self, frame):
        self._frame = frame

    def history(self, symbol, market, lookback_days=900, adjust="qfq"):
        return self._frame, "fake-provider"


def _write_history(history_path, as_of="2025-01-10"):
    append_jsonl(
        str(history_path),
        {
            "generated_at": as_of + "T09:00:00+08:00",
            "trade_date": as_of,
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "as_of": as_of,
                    "action": "BUY",
                    "score": 3.5,
                }
            ],
        },
    )


def test_strategy_exit_profit_lock(tmp_path):
    history_path = tmp_path / "recommendations_history.jsonl"
    _write_history(history_path)
    # entry_index=2 (2025-01-13, open=100); idx3 high=120 触发 18% profit-lock，idx4 开盘 118 退出
    frame = pd.DataFrame(
        [
            {"date": "2025-01-09", "open": 100, "high": 100, "low": 99, "close": 100},
            {"date": "2025-01-10", "open": 100, "high": 100, "low": 99, "close": 100},
            {"date": "2025-01-13", "open": 100, "high": 101, "low": 99, "close": 100},
            {"date": "2025-01-14", "open": 101, "high": 120, "low": 100, "close": 119},
            {"date": "2025-01-15", "open": 118, "high": 119, "low": 117, "close": 118},
            {"date": "2025-01-16", "open": 118, "high": 119, "low": 117, "close": 118},
            {"date": "2025-01-17", "open": 118, "high": 119, "low": 117, "close": 118},
            {"date": "2025-01-20", "open": 118, "high": 119, "low": 117, "close": 118},
        ]
    )
    result = evaluate_recommendation_performance(
        str(history_path), _FrameProvider(frame), _strategy_settings(), limit=100
    )
    strategy = result["items"][0]["strategy_exit"]
    assert strategy is not None
    assert strategy["exit_reason"] == "profit_lock_exit"
    assert strategy["return_pct"] == 18.0
    assert strategy["exit_date"] == "2025-01-15"
    assert result["summary"]["strategy_exit_reason_counts"]["profit_lock_exit"] == 1


def test_strategy_exit_time_exit_when_no_trigger(tmp_path):
    history_path = tmp_path / "recommendations_history.jsonl"
    _write_history(history_path)
    # high 全程 < 118（不触发 profit-lock），日期连续无长假 → time_exit 兜底
    frame = pd.DataFrame(
        [
            {"date": "2025-01-09", "open": 100, "high": 100, "low": 99, "close": 100},
            {"date": "2025-01-10", "open": 100, "high": 100, "low": 99, "close": 100},
            {"date": "2025-01-13", "open": 100, "high": 101, "low": 99, "close": 100},
            {"date": "2025-01-14", "open": 100, "high": 105, "low": 99, "close": 104},
            {"date": "2025-01-15", "open": 104, "high": 106, "low": 103, "close": 105},
            {"date": "2025-01-16", "open": 105, "high": 106, "low": 104, "close": 105},
            {"date": "2025-01-17", "open": 105, "high": 106, "low": 104, "close": 105},
            {"date": "2025-01-20", "open": 105, "high": 106, "low": 104, "close": 105},
        ]
    )
    result = evaluate_recommendation_performance(
        str(history_path), _FrameProvider(frame), _strategy_settings(), limit=100
    )
    strategy = result["items"][0]["strategy_exit"]
    assert strategy is not None
    assert strategy["exit_reason"] == "time_exit"
    assert strategy["holding_days"] == 5
    assert strategy["exit_date"] == "2025-01-20"


def test_strategy_exit_calendar_gap(tmp_path):
    history_path = tmp_path / "recommendations_history.jsonl"
    _write_history(history_path)
    # 01-14 → 01-23 gap 9 天（≥7），high<118 不触发 profit-lock → 长假前退出
    frame = pd.DataFrame(
        [
            {"date": "2025-01-09", "open": 100, "high": 100, "low": 99, "close": 100},
            {"date": "2025-01-10", "open": 100, "high": 100, "low": 99, "close": 100},
            {"date": "2025-01-13", "open": 100, "high": 101, "low": 99, "close": 100},
            {"date": "2025-01-14", "open": 100, "high": 105, "low": 99, "close": 104},
            {"date": "2025-01-23", "open": 104, "high": 106, "low": 103, "close": 105},
            {"date": "2025-01-24", "open": 105, "high": 106, "low": 104, "close": 105},
            {"date": "2025-01-27", "open": 105, "high": 106, "low": 104, "close": 105},
            {"date": "2025-01-28", "open": 105, "high": 106, "low": 104, "close": 105},
        ]
    )
    result = evaluate_recommendation_performance(
        str(history_path), _FrameProvider(frame), _strategy_settings(), limit=100
    )
    strategy = result["items"][0]["strategy_exit"]
    assert strategy is not None
    assert strategy["exit_reason"] == "pre_calendar_gap_exit"
    assert strategy["exit_date"] == "2025-01-14"


def test_strategy_exit_pending_when_history_too_short(tmp_path):
    history_path = tmp_path / "recommendations_history.jsonl"
    _write_history(history_path)
    # entry_index=2，hold_days=5 → 需 index 7；frame 只到 index 4 → pending
    frame = pd.DataFrame(
        [
            {"date": "2025-01-09", "open": 100, "high": 100, "low": 99, "close": 100},
            {"date": "2025-01-10", "open": 100, "high": 100, "low": 99, "close": 100},
            {"date": "2025-01-13", "open": 100, "high": 101, "low": 99, "close": 100},
            {"date": "2025-01-14", "open": 100, "high": 105, "low": 99, "close": 104},
            {"date": "2025-01-15", "open": 104, "high": 106, "low": 103, "close": 105},
        ]
    )
    result = evaluate_recommendation_performance(
        str(history_path), _FrameProvider(frame), _strategy_settings(), limit=100
    )
    assert result["items"][0]["strategy_exit"] is None
    assert result["summary"]["strategy_pending_count"] >= 1
