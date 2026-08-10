from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_train_locked_formal_data_receipts as receipts


def test_publish_requires_local_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(receipts.TrainLockedFormalDataReceiptError):
        receipts.build_train_locked_formal_data_receipt_bundle(repo_root=tmp_path)


def test_publish_against_real_repo_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration: only runs when real daily/FH artifacts exist in workspace."""
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    daily = repo / receipts.DEFAULT_DAILY_RUN / "state.json"
    if not daily.is_file():
        pytest.skip("real daily-basic run not present")
    bundle = receipts.build_train_locked_formal_data_receipt_bundle(repo_root=repo)
    assert bundle["ok"] is True
    assert bundle["upstream_formal_data_complete"] is True
    assert bundle["parent_eval_formal_complete"] is False
    assert bundle["daily_incremental_sync_required"] is False
    assert bundle["formal_materialization_eligible"] is False
    assert bundle["automatic_trading_allowed"] is False
    tw = bundle["train_window_freeze"]
    assert tw["train_exclusive_end_date"] == "2026-08-01"
    assert tw["train_inclusive_session_end"] == "2026-07-03"
    daily_b = bundle["upstream_formal_data_receipts"]["daily_basic"]
    assert daily_b["last_trade_date"] == "2026-07-03"
    assert daily_b["trade_date_count"] == 733
