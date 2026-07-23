import numpy as np
import pandas as pd

from app import audited_pit_walk_forward_rank as rank


def test_fold_ranges_are_contiguous_after_minimum_training_window():
    sessions = [f"2025-01-{index + 1:02d}" for index in range(10)]

    assert rank._fold_ranges(
        sessions,
        minimum_training_sessions=4,
        validation_sessions=3,
    ) == [
        ("2025-01-05", "2025-01-07"),
        ("2025-01-08", "2025-01-10"),
    ]


def test_weighted_ridge_learns_cross_sectional_direction():
    x = np.asarray([[-2.0], [-1.0], [1.0], [2.0]])
    y = np.asarray([0.0, 0.0, 1.0, 1.0])
    model = rank._fit_weighted_ridge(
        x,
        y,
        ["2025-01-01", "2025-01-01", "2025-01-02", "2025-01-02"],
    )

    scores = rank._predict_ridge(model, np.asarray([[-1.0], [1.0]]))

    assert scores[1] > scores[0]
    assert model["coefficients"][0] == 0.5
    assert model["coefficients"][1] > 0


def _bars(include_future=False):
    rows = []
    for index in range(65 + int(include_future)):
        close = 10.0 + index * 0.1
        if index == 65:
            close = 1000.0
        rows.append(
            {
                "date": f"2025-{index // 28 + 1:02d}-{index % 28 + 1:02d}",
                "ts_code": "000001.SZ",
                "open": close - 0.05,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "pre_close": close - 0.1,
                "amount": 100_000_000.0 + index * 1_000_000.0,
                "adj_factor": 1.0,
                "suspended": False,
                "membership_name": "历史名称",
            }
        )
    return pd.DataFrame(rows)


def test_candidate_features_are_complete_and_unchanged_by_future_bar():
    trade = {
        "signal_date": "2025-03-05",
        "exit_date": "2025-03-11",
        "symbol": "000001",
        "return_pct": 2.0,
    }

    before = rank._candidate_features([trade], _bars())
    after = rank._candidate_features([trade], _bars(include_future=True))

    assert bool(before.at[0, "feature_complete"]) is True
    assert before[list(rank.FEATURE_NAMES)].to_dict("records") == after[
        list(rank.FEATURE_NAMES)
    ].to_dict("records")
    assert before.at[0, "positive_after_cost"] == 1.0
