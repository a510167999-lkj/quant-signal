import sqlite3
from datetime import timedelta
from pathlib import Path

from app.research_pit_store import AuditedPointInTimeUniverse, PITReceiptStore
from tests.test_research_membership_generations import (
    BASE,
    CONTRACT,
    ROLE,
    _publish_market,
    _record,
)
from tests.test_research_pit_store import _ingest_complete_two_day_fixture


EXACT = "2024-01-02"
QUARANTINED = "2024-01-03"


def _mixed_membership_fixture(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_complete_two_day_fixture(store, omit_day_two=True)

    with sqlite3.connect(store.database_path) as connection:
        connection.row_factory = sqlite3.Row
        anchor_receipt = dict(
            connection.execute(
                "SELECT * FROM receipts WHERE dataset='bak_basic' AND partition_key=?",
                (EXACT,),
            ).fetchone()
        )
    anchor_raw = (store.root / anchor_receipt["raw_path"]).read_bytes()
    _attempt, anchor_event = _record(
        store,
        "bak_basic",
        EXACT,
        [],
        sequence=80,
        raw=anchor_raw,
    )
    assert anchor_event["status"] == "reused"

    empty_attempt_ids = []
    for sequence in (81, 82, 83):
        attempt, event = _record(
            store,
            "bak_basic",
            QUARANTINED,
            [],
            sequence=sequence,
        )
        assert event["status"] == "invalid_json"
        empty_attempt_ids.append(attempt["attempt_id"])

    _publish_market(store, QUARANTINED, sequence=100)
    generation = store.begin_or_resume_membership_generation(
        BASE + timedelta(minutes=5),
        QUARANTINED,
        temporal_role=ROLE,
        temporal_contract_sha256=CONTRACT,
    )
    store.stage_membership_generation(generation["generation_id"])
    store.publish_membership_generation(generation["generation_id"])
    return store, empty_attempt_ids


def test_coverage_audit_accepts_exact_and_quarantined_membership_authorities(tmp_path):
    store, empty_attempt_ids = _mixed_membership_fixture(tmp_path)
    active = store.verify_membership_generation(QUARANTINED)
    assert active["source_kind"] == "causal_carry_forward"
    assert active["anchor"]["trade_date"] == EXACT
    assert active["anchor"]["trade_date"] < active["trade_date"]
    assert {item["attempt_id"] for item in active["empty_evidence"]} == set(empty_attempt_ids)
    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT 1 FROM receipts WHERE dataset='bak_basic' AND partition_key=?",
                (QUARANTINED,),
            ).fetchone()
            is None
        )

    audit = store.audit_coverage(start_date=EXACT, end_date=QUARANTINED)

    assert audit["status"] == "passed"
    assert audit["exact_membership_session_count"] == 1
    assert audit["quarantined_membership_session_count"] == 1
    assert audit["pre_anchor_session_count"] == 0
    assert audit["maximum_consecutive_quarantined_sessions"] == 1
    assert len(audit["membership_generation_root_sha256"]) == 64

    descriptor = store.publish_universe_artifact(
        str(tmp_path / "artifact"),
        start_date=EXACT,
        end_date=QUARANTINED,
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    assert Path(descriptor["path"]).exists()
    assert descriptor["final_oos_eligible"] is False
    assert descriptor["membership_generations"]["count"] == 1

    universe = AuditedPointInTimeUniverse.from_legacy_unbound_file(
        descriptor["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    try:
        snapshot_store = PITReceiptStore.__new__(PITReceiptStore)
        snapshot_store.root = Path(descriptor["path"]).parent
        snapshot_store.raw_root = snapshot_store.root / "raw"
        snapshot_store.database_path = Path(descriptor["path"])
        generation, verified = snapshot_store._active_membership_on_connection(
            universe._connection,
            QUARANTINED,
        )
        assert generation["trade_date"] == QUARANTINED
        assert verified["source_kind"] == "causal_carry_forward"
        assert verified["anchor"]["trade_date"] == EXACT
    finally:
        universe.close()
