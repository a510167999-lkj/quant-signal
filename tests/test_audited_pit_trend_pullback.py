from copy import deepcopy

import pandas as pd
import pytest

from app import audited_pit_trend_pullback as pullback
from app.audited_pit_development_replay import AuditedPITDevelopmentReplayError


def _signal_frame(closes, *, factors=None):
    factors = factors or [1.0] * len(closes)
    dates = pd.bdate_range("2025-01-02", periods=len(closes)).strftime(
        "%Y-%m-%d"
    )
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": closes,
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "amount": [1_000_000.0] * len(closes),
            "adj_factor": factors,
            "membership_name": ["测试股份"] * len(closes),
        }
    )


def _reclaim_prices():
    return [80.0] * 41 + [100.0] * 18 + [95.0, 100.0]


def test_fixed_signal_is_inclusive_at_prior_high_and_exclusive_with_breakout():
    frame = _signal_frame(_reclaim_prices())

    masks = pullback._fixed_signal_masks(frame)

    assert bool(masks.at[60, "pullback"]) is True
    assert bool(masks.at[60, "breakout"]) is False
    assert masks.at[60, "adjusted_close"] == masks.at[60, "prior_high20"]

    breakout_frame = frame.copy()
    breakout_frame.loc[60, ["open", "high", "close"]] = 101.0
    breakout_masks = pullback._fixed_signal_masks(breakout_frame)
    assert bool(breakout_masks.at[60, "pullback"]) is False
    assert bool(breakout_masks.at[60, "breakout"]) is True


def test_fixed_signal_requires_reclaim_and_sixty_complete_bars():
    frame = _signal_frame(_reclaim_prices())
    failed_reclaim = frame.copy()
    failed_reclaim.loc[60, ["open", "high", "close"]] = 99.0

    assert not bool(pullback._fixed_signal_masks(failed_reclaim).at[60, "pullback"])
    assert not pullback._fixed_signal_masks(frame.iloc[:59]).get(
        "pullback"
    ).any()


def test_future_bar_and_reverse_split_do_not_change_existing_signal():
    frame = _signal_frame(_reclaim_prices())
    original = pullback._fixed_signal_masks(frame)
    with_future = pd.concat(
        [
            frame,
            _signal_frame([1000.0]).assign(date="2026-01-05"),
        ],
        ignore_index=True,
    )
    split_frame = frame.copy()
    split_frame.loc[60, ["open", "high", "low", "close"]] /= 10.0
    split_frame.loc[60, "adj_factor"] = 10.0

    assert bool(original.at[60, "pullback"]) is True
    assert bool(pullback._fixed_signal_masks(with_future).at[60, "pullback"])
    assert bool(pullback._fixed_signal_masks(split_frame).at[60, "pullback"])


class _FakeAdapter:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.calls = []

    def next_open(self, symbol, trade_date, side="buy"):
        self.calls.append((symbol, trade_date, side))
        verdict = dict(self.verdicts[(trade_date, side)])
        verdict["generation_proof"] = {
            "trade_date": trade_date,
            "generation_id": f"generation-{trade_date}",
            "manifest_sha256": "a" * 64,
            "lineage_sha256": "b" * 64,
            "vintage": "2025-01-01T16:00:00+08:00",
        }
        return verdict


def _execution_frame(closes):
    dates = [f"2025-01-{day:02d}" for day in range(2, 2 + len(closes))]
    rows = []
    for trade_date, close in zip(dates, closes):
        rows.append(
            {
                "date": trade_date,
                "open": 100.0,
                "high": max(101.0, close),
                "low": min(99.0, close),
                "close": close,
                "amount": 1_000_000.0,
                "adj_factor": 1.0,
                "membership_name": "测试股份",
            }
        )
    return pd.DataFrame(rows)


def _fillable(raw_price):
    return {"fillable": True, "reason": "fillable", "raw_price": raw_price}


def _blocked(reason="down_limit"):
    return {"fillable": False, "reason": reason, "raw_price": None}


def test_close_stop_is_decided_at_close_and_retries_blocked_next_open():
    frame = _execution_frame([100.0, 94.0, 93.0, 92.0, 95.0, 96.0, 97.0, 98.0])
    frame.loc[1, "open"] = 100.0
    frame.loc[2, "open"] = 93.0
    frame.loc[3, "open"] = 92.0
    sessions = frame["date"].tolist()
    adapter = _FakeAdapter(
        {
            (sessions[1], "buy"): _fillable(100.0),
            (sessions[2], "sell"): _blocked(),
            (sessions[3], "sell"): _fillable(92.0),
        }
    )

    trade, censored, event = pullback._strict_close_stop_trade(
        adapter=adapter,
        verdict_cache={},
        frame=frame,
        symbol="000001",
        signal_index=0,
        sessions=sessions,
        session_positions={date: index for index, date in enumerate(sessions)},
        suspension_evidence={},
        hold_days=5,
        stop_loss_pct=5.0,
    )

    assert trade is not None
    assert censored is None
    assert trade["stop_trigger_date"] == sessions[1]
    assert trade["exit_date"] == sessions[3]
    assert trade["exit_reason"] == "close_stop_next_open"
    assert [attempt["fillable"] for attempt in trade["exit_execution_attempts"]] == [
        False,
        True,
    ]
    assert event["status"] == "candidate_built"
    assert adapter.calls == [
        ("000001", sessions[1], "buy"),
        ("000001", sessions[2], "sell"),
        ("000001", sessions[3], "sell"),
        ("000001", sessions[1], "buy"),
        ("000001", sessions[2], "sell"),
        ("000001", sessions[3], "sell"),
    ]


def test_no_close_trigger_exits_at_fifth_session_next_open():
    frame = _execution_frame([100.0] * 8)
    sessions = frame["date"].tolist()
    adapter = _FakeAdapter(
        {
            (sessions[1], "buy"): _fillable(100.0),
            (sessions[6], "sell"): _fillable(100.0),
        }
    )

    trade, censored, event = pullback._strict_close_stop_trade(
        adapter=adapter,
        verdict_cache={},
        frame=frame,
        symbol="000001",
        signal_index=0,
        sessions=sessions,
        session_positions={date: index for index, date in enumerate(sessions)},
        suspension_evidence={},
        hold_days=5,
        stop_loss_pct=5.0,
    )

    assert trade is not None
    assert censored is None
    assert trade["planned_exit_date"] == sessions[6]
    assert trade["exit_date"] == sessions[6]
    assert trade["exit_reason"] == "time_exit_next_open"
    assert trade["stop_trigger_date"] is None
    assert event["status"] == "candidate_built"


def test_close_stop_verification_rejects_tampered_trigger():
    frame = _execution_frame([100.0, 94.0, 93.0, 92.0, 95.0, 96.0, 97.0, 98.0])
    frame.loc[1, "open"] = 100.0
    frame.loc[2, "open"] = 93.0
    sessions = frame["date"].tolist()
    adapter = _FakeAdapter(
        {
            (sessions[1], "buy"): _fillable(100.0),
            (sessions[2], "sell"): _fillable(93.0),
        }
    )
    trade, _, _ = pullback._strict_close_stop_trade(
        adapter=adapter,
        verdict_cache={},
        frame=frame,
        symbol="000001",
        signal_index=0,
        sessions=sessions,
        session_positions={date: index for index, date in enumerate(sessions)},
        suspension_evidence={},
        hold_days=5,
        stop_loss_pct=5.0,
    )
    tampered = deepcopy(trade)
    tampered["stop_trigger_date"] = sessions[2]

    with pytest.raises(AuditedPITDevelopmentReplayError):
        pullback._verify_completed_close_stop_trade(
            frame=frame,
            sessions=sessions,
            session_positions={
                date: index for index, date in enumerate(sessions)
            },
            signal_index=0,
            symbol="000001",
            trade=tampered,
            suspension_evidence={},
            hold_days=5,
            stop_loss_pct=5.0,
        )


def test_source_requery_rejects_tampered_execution_proof():
    frame = _execution_frame([100.0, 94.0, 93.0, 92.0, 95.0, 96.0, 97.0, 98.0])
    frame.loc[1, "open"] = 100.0
    frame.loc[2, "open"] = 93.0
    sessions = frame["date"].tolist()
    verdicts = {
        (sessions[1], "buy"): _fillable(100.0),
        (sessions[2], "sell"): _fillable(93.0),
    }
    trade, _, _ = pullback._strict_close_stop_trade(
        adapter=_FakeAdapter(verdicts),
        verdict_cache={},
        frame=frame,
        symbol="000001",
        signal_index=0,
        sessions=sessions,
        session_positions={date: index for index, date in enumerate(sessions)},
        suspension_evidence={},
        hold_days=5,
        stop_loss_pct=5.0,
    )
    tampered = deepcopy(trade)
    tampered["exit_execution_attempts"][0]["generation_proof"][
        "manifest_sha256"
    ] = "f" * 64

    with pytest.raises(AuditedPITDevelopmentReplayError):
        pullback._verify_execution_evidence_against_adapter(
            adapter=_FakeAdapter(verdicts),
            symbol="000001",
            trade=tampered,
        )


def test_missing_held_bar_requires_official_suspension_and_carries_value():
    full_frame = _execution_frame(
        [100.0, 94.0, 93.0, 92.0, 95.0, 96.0, 97.0, 98.0]
    )
    full_frame.loc[1, "open"] = 100.0
    full_frame.loc[3, "open"] = 92.0
    sessions = full_frame["date"].tolist()
    frame = full_frame.drop(index=2).reset_index(drop=True)
    adapter = _FakeAdapter(
        {
            (sessions[1], "buy"): _fillable(100.0),
            (sessions[2], "sell"): _blocked("missing_raw_bar"),
            (sessions[3], "sell"): _fillable(92.0),
        }
    )
    suspension = {
        ("000001", sessions[2]): [
            {
                "symbol": "000001",
                "trade_date": sessions[2],
                "suspend_type": "S",
                "suspend_timing": None,
                "generation_id": "generation",
                "manifest_sha256": "a" * 64,
                "published_at": "2025-01-01T16:00:00+08:00",
            }
        ]
    }

    trade, censored, _ = pullback._strict_close_stop_trade(
        adapter=adapter,
        verdict_cache={},
        frame=frame,
        symbol="000001",
        signal_index=0,
        sessions=sessions,
        session_positions={date: index for index, date in enumerate(sessions)},
        suspension_evidence=suspension,
        hold_days=5,
        stop_loss_pct=5.0,
    )

    assert censored is None
    assert trade is not None
    assert len(trade["mark_to_market_path"]) == 3
    carried = trade["mark_to_market_path"][1]
    assert carried["date"] == sessions[2]
    assert carried["valuation_source"] == "official_suspension_carry_forward"
    assert carried["close_return_pct"] == trade["mark_to_market_path"][0][
        "close_return_pct"
    ]
    assert len(trade["close_stop_verification"]["receipt_sha256"]) == 64


def test_unfilled_sell_through_coverage_preserves_open_position():
    frame = _execution_frame([100.0, 94.0, 93.0, 92.0, 91.0, 90.0, 89.0, 88.0])
    sessions = frame["date"].tolist()
    verdicts = {(sessions[1], "buy"): _fillable(100.0)}
    verdicts.update(
        {(date, "sell"): _blocked("suspended") for date in sessions[2:]}
    )
    adapter = _FakeAdapter(verdicts)

    trade, censored, event = pullback._strict_close_stop_trade(
        adapter=adapter,
        verdict_cache={},
        frame=frame,
        symbol="000001",
        signal_index=0,
        sessions=sessions,
        session_positions={date: index for index, date in enumerate(sessions)},
        suspension_evidence={},
        hold_days=5,
        stop_loss_pct=5.0,
    )

    assert trade is None
    assert censored is not None
    assert censored["right_censored"] is True
    assert censored["exit_date"] == sessions[-1]
    assert censored["selection_exit_date_semantics"] == (
        "coverage_end_position_still_open"
    )
    assert event["status"] == "entered_unresolved_exit"
    assert event["planned_exit_date"] == sessions[6]


def test_tail_signal_is_uniformly_cut_off_before_entry_or_outcome():
    frame = _execution_frame([100.0] * 4)
    sessions = frame["date"].tolist()
    adapter = _FakeAdapter(
        {(sessions[1], "buy"): _fillable(100.0)}
    )

    trade, censored, event = pullback._strict_close_stop_trade(
        adapter=adapter,
        verdict_cache={},
        frame=frame,
        symbol="000001",
        signal_index=0,
        sessions=sessions,
        session_positions={date: index for index, date in enumerate(sessions)},
        suspension_evidence={},
        hold_days=5,
        stop_loss_pct=5.0,
    )

    assert trade is None
    assert censored is None
    assert event["status"] == "administrative_signal_cutoff"
    assert adapter.calls == []


def test_signal_date_name_gate_rejects_st_and_missing_membership():
    assert pullback._eligible_signal_name("正常股份") == "正常股份"
    assert pullback._eligible_signal_name("ST风险") is None
    assert pullback._eligible_signal_name(None) is None


def test_unresolved_censor_blocks_evidence_and_unknown_censor_fails_closed():
    base = {
        "symbol": "000001",
        "signal_date": "2026-06-30",
        "entry_date": "2026-07-01",
        "exit_date": "2026-07-03",
        "rank_score": 100.0,
        "signal_tags": ["trend_pullback_ma20_reclaim"],
        "right_censored": True,
    }
    unresolved = {
        **base,
        "censor_reason": "no_strict_sell_fill_through_coverage_end",
    }
    administrative = {
        **base,
        "censor_reason": "planned_exit_beyond_coverage",
    }

    unresolved_sweep, _ = pullback._evaluate(
        [],
        [unresolved],
        ["trend_pullback_ma20_reclaim"],
        evaluation_session_dates=[
            "2026-06-30",
            "2026-07-01",
            "2026-07-02",
            "2026-07-03",
        ],
    )
    assert unresolved_sweep["top"][0]["evidence_complete"] is False
    assert unresolved_sweep["top"][0][
        "selected_right_censored_position_count"
    ] == 1
    with pytest.raises(
        AuditedPITDevelopmentReplayError,
        match="unknown censor reason",
    ):
        pullback._evaluate(
            [],
            [administrative],
            ["trend_pullback_ma20_reclaim"],
            evaluation_session_dates=[
                "2026-06-30",
                "2026-07-01",
                "2026-07-02",
                "2026-07-03",
            ],
        )


def test_strict_evaluation_requires_audited_session_grid():
    with pytest.raises(
        AuditedPITDevelopmentReplayError,
        match="evaluation session grid",
    ):
        pullback._evaluate(
            [],
            [],
            ["trend_pullback_ma20_reclaim"],
        )


def test_stable_sidecar_reference_does_not_bind_output_directory():
    digest = "a" * 64

    first = pullback._stable_sidecar_reference(
        {"path": "C:/first/output/a.json", "artifact_sha256": digest}
    )
    second = pullback._stable_sidecar_reference(
        {"path": "D:/other/output/a.json", "artifact_sha256": digest}
    )

    assert first == second == {
        "artifact_sha256": digest,
        "relative_path": f"sidecars/{digest}.json",
    }


def test_fixed_sweep_shape_fails_closed():
    for invalid in ({"top": []}, {"top": {}}, {"top": [{}, {}]}):
        with pytest.raises(AuditedPITDevelopmentReplayError):
            pullback._single_fixed_spec_row(invalid)
