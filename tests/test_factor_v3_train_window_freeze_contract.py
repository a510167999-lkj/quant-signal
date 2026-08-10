from __future__ import annotations

import pytest

from app import factor_v3_train_window_freeze_contract as freeze


def test_train_window_freeze_is_before_august_2026() -> None:
    freeze.assert_train_window_freeze_consistent()
    assert freeze.TRAIN_EXCLUSIVE_END_DATE == "2026-08-01"
    assert freeze.TRAIN_INCLUSIVE_SESSION_END == "2026-07-03"
    assert freeze.TRAIN_INCLUSIVE_SESSION_END < freeze.TRAIN_EXCLUSIVE_END_DATE
    assert freeze.DAILY_INCREMENTAL_SYNC_REQUIRED is False
    assert freeze.AUTOMATIC_TRADING_ALLOWED is False


def test_session_on_or_after_cutoff_rejected() -> None:
    with pytest.raises(freeze.TrainWindowFreezeError):
        freeze.assert_session_date_in_train_window(
            "2026-08-01", label="session"
        )
    with pytest.raises(freeze.TrainWindowFreezeError):
        freeze.assert_session_date_in_train_window(
            "2026-08-10", label="session"
        )


def test_session_before_cutoff_ok() -> None:
    assert (
        freeze.assert_session_date_in_train_window("2026-07-03", label="session")
        == "2026-07-03"
    )
