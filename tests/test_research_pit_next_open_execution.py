"""Next-open execution evidence over the audited PIT universe snapshot.

WHY: ``AuditedPointInTimeUniverse.next_open_execution_evidence`` is the
sanctioned path from the frozen snapshot to a single trade's next-open fill
verdict. It reuses the same symbol / date / open-session anchoring as
``causal_signal_bars``, reads the day's daily / stk_limit / suspend_d rows
scoped to the manifest-anchored generation, hands them to
``app.research_market_data.next_open_fill_gate`` for the tradability verdict,
and binds the verdict to the five-field generation proof.
"""

import pytest

from app.research_pit_store import PITReceiptError
from tests.test_research_pit_causal_signal_bars import (
    _open,
    _publish_two_day_artifact,
)


def test_next_open_execution_evidence_normal_buy_returns_raw_open(tmp_path):
    artifact, audit = _publish_two_day_artifact(tmp_path, label="buy")
    universe = _open(artifact, audit)
    try:
        refs = {
            ref["trade_date"]: ref
            for ref in universe.manifest["market_generations"]["refs"]
        }
        result = universe.next_open_execution_evidence("600001", "2024-01-03", "buy")
    finally:
        universe.close()

    # WHY: 600001's raw open (10.0) sits strictly inside [down_limit 9.0,
    # up_limit 11.0) and the fixture has no suspension, so the next-open gate
    # must accept the fill at the raw open — the only price an execution at the
    # open could realize — and the verdict must be bound to the manifest proof
    # for that session.
    assert result["fillable"] is True
    assert result["reason"] == "raw_open"
    assert result["raw_price"] == pytest.approx(10.0)
    assert set(result["generation_proof"]) == {
        "trade_date",
        "generation_id",
        "manifest_sha256",
        "lineage_sha256",
        "vintage",
    }
    assert result["generation_proof"] == refs["2024-01-03"]


def test_next_open_execution_evidence_missing_rows_returns_missing_reason(tmp_path):
    # WHY: 000002 is registered in security_master (delisted) but the fixture's
    # market session generations carry no daily / stk_limit row for it, so the
    # gate must fail closed with a missing-bar reason rather than ever
    # synthesize a fill price from incomplete evidence.
    artifact, audit = _publish_two_day_artifact(tmp_path, label="missing")
    universe = _open(artifact, audit)
    try:
        result = universe.next_open_execution_evidence("000002", "2024-01-03", "buy")
    finally:
        universe.close()

    assert result["fillable"] is False
    assert result["reason"] in {"missing_raw_bar", "missing_price_limit"}
    assert result["raw_price"] is None
    assert "generation_proof" in result


def test_next_open_execution_evidence_rejects_after_close(tmp_path):
    artifact, audit = _publish_two_day_artifact(tmp_path, label="closed")
    universe = _open(artifact, audit)
    universe.close()
    with pytest.raises(PITReceiptError, match="closed"):
        universe.next_open_execution_evidence("600001", "2024-01-03", "buy")
