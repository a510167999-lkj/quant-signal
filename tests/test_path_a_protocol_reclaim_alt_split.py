from __future__ import annotations

from app import factor_v3_path_a_protocol_reclaim_alt_split as alt
from app import factor_v3_path_a_protocol_reclaim_freeze as freeze
from app import factor_v3_path_a_research_protocol as proto
from app import research_goal_contract as goal
from app.factor_v3_path_a_protocol_signal_specs import (
    FROZEN_RECLAIM_CANDIDATE_ID,
    HOLD5_TAG,
    PULLBACK_TAG,
)


def _trade(symbol: str, signal_date: str, return_pct: float = 4.0) -> dict:
    entry = signal_date
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "entry_date": entry,
        "exit_date": signal_date,
        "return_pct": return_pct,
        "rank_score": -4.0,
        "relative_strength": {"stock_return_20d_pct": 4.0},
        "max_adverse_pct": -1.0,
        "market_level": "favorable",
        "signal_tags": [PULLBACK_TAG, HOLD5_TAG],
        "mark_to_market_path": [
            {"date": entry, "close_return_pct": 0.0, "low_return_pct": 0.0},
            {"date": signal_date, "close_return_pct": return_pct, "low_return_pct": -1.0},
        ],
    }


def test_alt_cut_is_2025_new_year_and_does_not_replace_locked_split() -> None:
    assert alt.ALT_EVAL_START == "2025-01-01"
    assert alt.partition_for_alt_signal_date("2024-12-31") == "train"
    assert alt.partition_for_alt_signal_date("2025-01-01") == "eval"
    assert alt.partition_for_alt_signal_date("2026-08-14") == "eval"
    assert alt.partition_for_alt_signal_date("2026-08-15") is None
    assert proto.HOLDOUT_START == "2025-07-01"
    assert proto.partition_for_signal_date("2025-01-02") == "train"
    assert proto.partition_for_signal_date("2025-01-02") != alt.partition_for_alt_signal_date(
        "2025-01-02"
    )


def test_alt_filter_and_overlap_are_diagnostic_only() -> None:
    trades = [
        _trade("000001", "2024-06-03"),
        _trade("000002", "2025-03-03"),
        _trade("000003", "2025-08-04"),
        _trade("000004", "2026-08-14"),
        _trade("000005", "2026-08-20"),
    ]
    train = alt.filter_alt_trades(trades, "train")
    ev = alt.filter_alt_trades(trades, "eval")
    assert [row["symbol"] for row in train] == ["000001"]
    assert [row["symbol"] for row in ev] == ["000002", "000003", "000004"]
    note = alt.overlap_note()
    assert note["independent_oos"] is False
    assert "holdout" in note["reason"]


def test_alt_report_from_trades_is_not_effective(monkeypatch) -> None:
    def fake_score(train_trades, eval_trades, variants, stage_goal_id):
        return {
            "variants": [
                {
                    "train": {
                        "latest_1y_return_pct": 8.0,
                        "full_path_mdd_pct": -10.0,
                        "selected_trade_count": 3,
                    },
                    "holdout": {
                        "latest_1y_return_pct": 27.0,
                        "full_path_mdd_pct": -9.0,
                        "selected_trade_count": 10,
                    },
                }
            ]
        }

    monkeypatch.setattr(alt.baseline, "score_protocol_baseline", fake_score)
    monkeypatch.setattr(
        alt,
        "load_alt_split_trades",
        lambda **_kwargs: ([_trade("000001", "2025-02-03")], {"raw_trade_count": 1}),
    )
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    report = alt.build_path_a_protocol_reclaim_alt_split(require_local_research=True)
    assert report["candidate_id"] == FROZEN_RECLAIM_CANDIDATE_ID
    assert report["candidate_spec_sha256"] == freeze.build_frozen_reclaim_card()[
        "candidate_spec_sha256"
    ]
    assert report["independent_oos"] is False
    assert report["effective_strategy"] is False
    assert report["eval_twelve_month_evaluable"] is True
    assert report["eval_dual_pass_26_15"] is True
    assert report["automatic_trading_allowed"] is goal.AUTOMATIC_TRADING_ALLOWED
    table = alt.format_reclaim_alt_split_table(report)
    assert "independent_oos=False" in table
    assert "effective_strategy=False" in table
    assert "eval_dual_pass_26_15=True" in table
