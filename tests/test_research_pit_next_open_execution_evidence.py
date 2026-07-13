"""Adversarial artifact matrix for ``next_open_execution_evidence``.

WHY: ``AuditedPointInTimeUniverse.next_open_execution_evidence`` is the
sanctioned path from the frozen, hash-anchored snapshot to a single trade's
next-open fill verdict. The normal path is covered by
``test_research_pit_next_open_execution``; this file pins the verdict under
every adversarial daily / stk_limit / suspend_d shape that could let a forged
or mis-scoped row leak an execution price.

Every case is driven through the *real* artifact pipeline: a fixture helper
overrides the day-two market rows, the source store ingests + audits +
publishes the universe artifact, and ``from_file`` re-opens the signed
snapshot. The post-sign SQLite is never mutated — the override happens at
ingest time only, so every verdict is bound to the manifest proof exactly as a
real consumer sees it.
"""

import pytest

from app.research_market_data import MarketEvidenceError
from app.research_pit_store import PITReceiptStore
from tests.test_research_pit_causal_signal_bars import _open
from tests.test_research_pit_store import (
    _audited_universe_loader,
    _ingest_complete_two_day_fixture,
)
import tests.test_research_pit_store as _pit_store_test_module

# Execution day in the two-day fixture; day one (2024-01-02) keeps defaults so
# only the day-two session is the adversarial surface.
_DAY_TWO = "2024-01-03"
_WIRE_DAY_TWO = "20240103"


def _publish_with_day_two_override(
    tmp_path,
    *,
    label,
    daily=None,
    stk_limit=None,
    suspend=None,
):
    """Publish an audited two-day artifact with day-two market rows overridden.

    WHY: the override is applied at ingest time by patching the fixture's
    ``_market_default_rows`` for ``2024-01-03`` only. The signed snapshot that
    ``from_file`` later opens therefore already encodes the adversarial rows —
    no post-sign DB tampering is possible. Each row list follows the
    ``NORMALIZED_FIELDS`` wire order so ingest validation runs unchanged.
    """

    real_defaults = _pit_store_test_module._market_default_rows

    def patched(dataset, trade_date):
        if trade_date == _DAY_TWO:
            if dataset == "daily" and daily is not None:
                return daily
            if dataset == "stk_limit" and stk_limit is not None:
                return stk_limit
            if dataset == "suspend_d" and suspend is not None:
                return suspend
        return real_defaults(dataset, trade_date)

    store = PITReceiptStore(str(tmp_path / f"{label}-store"))
    _pit_store_test_module._market_default_rows = patched
    try:
        _ingest_complete_two_day_fixture(store)
    finally:
        _pit_store_test_module._market_default_rows = real_defaults
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / f"{label}-art"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    return artifact, audit


def _row_refs(universe):
    return {ref["trade_date"]: ref for ref in universe.manifest["market_generations"]["refs"]}


# Reusable raw-bar / limit shapes. The gate only reads daily.open and the
# stk_limit up/down bounds, so each shape fixes exactly one boundary.
_DAILY_OPEN_AT_UP = [
    ["600001.SH", _WIRE_DAY_TWO, 11.0, 11.2, 10.8, 11.0, 10.0, 1.0, 10.0, 1000, 11000]
]
_DAILY_OPEN_AT_DOWN = [
    ["600001.SH", _WIRE_DAY_TWO, 9.0, 9.2, 8.8, 9.0, 10.0, -1.0, -10.0, 1000, 9000]
]
_LIMITS = [[_WIRE_DAY_TWO, "600001.SH", 10.0, 11.0, 9.0]]


def test_next_open_resolves_unique_historical_member_missing_current_master(
    tmp_path, monkeypatch
):
    real_stock_response = _pit_store_test_module._stock_response

    def stock_response_without_historical_member(rows):
        return real_stock_response(
            [row for row in rows if row[0] != "600001.SH"]
        )

    monkeypatch.setattr(
        _pit_store_test_module,
        "_stock_response",
        stock_response_without_historical_member,
    )
    artifact, audit = _publish_with_day_two_override(
        tmp_path,
        label="missing-master",
    )

    universe = _open(artifact, audit)
    try:
        verdict = universe.next_open_execution_evidence(
            "600001", _DAY_TWO, "buy"
        )
    finally:
        universe.close()

    assert verdict["fillable"] is True
    assert verdict["raw_price"] == pytest.approx(10.0)


def test_open_at_up_limit_rejects_buy_but_fills_sell(tmp_path):
    # WHY: an open equal to the up limit is a one-sided lock — a buy could not
    # be filled at the open (no seller at the ceiling), but a sell realizes the
    # raw open. The gate must side-discriminate rather than blanket-reject.
    artifact, audit = _publish_with_day_two_override(
        tmp_path,
        label="up-lock",
        daily=_DAILY_OPEN_AT_UP,
        stk_limit=_LIMITS,
    )
    universe = _open(artifact, audit)
    try:
        buy = universe.next_open_execution_evidence("600001", _DAY_TWO, "buy")
        sell = universe.next_open_execution_evidence("600001", _DAY_TWO, "sell")
        refs = _row_refs(universe)
    finally:
        universe.close()

    assert buy["fillable"] is False
    assert buy["reason"] == "buy_open_locked_limit"
    assert buy["raw_price"] is None
    assert buy["generation_proof"] == refs[_DAY_TWO]

    assert sell["fillable"] is True
    assert sell["reason"] == "raw_open"
    assert sell["raw_price"] == pytest.approx(11.0)
    assert sell["generation_proof"] == refs[_DAY_TWO]


def test_open_at_down_limit_rejects_sell_but_fills_buy(tmp_path):
    # WHY: symmetric to the up-limit lock — an open at the down limit blocks a
    # sell (no buyer at the floor) but a buy still fills at the raw open. The
    # verdict must mirror the up-limit case on the opposite side.
    artifact, audit = _publish_with_day_two_override(
        tmp_path,
        label="down-lock",
        daily=_DAILY_OPEN_AT_DOWN,
        stk_limit=_LIMITS,
    )
    universe = _open(artifact, audit)
    try:
        buy = universe.next_open_execution_evidence("600001", _DAY_TWO, "buy")
        sell = universe.next_open_execution_evidence("600001", _DAY_TWO, "sell")
        refs = _row_refs(universe)
    finally:
        universe.close()

    assert sell["fillable"] is False
    assert sell["reason"] == "sell_open_locked_limit"
    assert sell["raw_price"] is None
    assert sell["generation_proof"] == refs[_DAY_TWO]

    assert buy["fillable"] is True
    assert buy["reason"] == "raw_open"
    assert buy["raw_price"] == pytest.approx(9.0)
    assert buy["generation_proof"] == refs[_DAY_TWO]


def test_full_day_suspend_s_blocks_both_sides_with_null_price(tmp_path):
    # WHY: a type-S (full-day suspension) row makes the open unfillable on
    # either side — the gate must fail closed with raw_price None and never
    # surface the daily open, since no trade could occur.
    artifact, audit = _publish_with_day_two_override(
        tmp_path,
        label="suspend-s",
        suspend=[["600001.SH", _WIRE_DAY_TWO, "全天", "S"]],
    )
    universe = _open(artifact, audit)
    try:
        buy = universe.next_open_execution_evidence("600001", _DAY_TWO, "buy")
        sell = universe.next_open_execution_evidence("600001", _DAY_TWO, "sell")
        refs = _row_refs(universe)
    finally:
        universe.close()

    for verdict in (buy, sell):
        assert verdict["fillable"] is False
        assert verdict["reason"] == "suspended"
        assert verdict["raw_price"] is None
        assert verdict["generation_proof"] == refs[_DAY_TWO]


def test_resumption_only_r_does_not_block(tmp_path):
    # WHY: type-R (resumption) signals a return to trading, not a halt — an R
    # row alone must not flip the session to suspended, so the open fills at the
    # raw price just like an unsuspended session.
    artifact, audit = _publish_with_day_two_override(
        tmp_path,
        label="resume-r",
        suspend=[["600001.SH", _WIRE_DAY_TWO, "开盘", "R"]],
    )
    universe = _open(artifact, audit)
    try:
        buy = universe.next_open_execution_evidence("600001", _DAY_TWO, "buy")
        sell = universe.next_open_execution_evidence("600001", _DAY_TWO, "sell")
        refs = _row_refs(universe)
    finally:
        universe.close()

    for verdict in (buy, sell):
        assert verdict["fillable"] is True
        assert verdict["reason"] == "raw_open"
        assert verdict["raw_price"] == pytest.approx(10.0)
        assert verdict["generation_proof"] == refs[_DAY_TWO]


def test_same_day_r_and_s_with_distinct_timing_still_suspends(tmp_path):
    # WHY: suspend_d's PK is (trade_date, ts_code, suspend_timing, suspend_type),
    # so an R row and an S row with different timings must coexist in the same
    # session. The presence of any S row still suspends the open, and both rows
    # must survive ingest + audit + publish into the signed snapshot — proving
    # the timing PK does not collapse the two events into one.
    artifact, audit = _publish_with_day_two_override(
        tmp_path,
        label="rs-coexist",
        suspend=[
            ["600001.SH", _WIRE_DAY_TWO, "开盘", "R"],
            ["600001.SH", _WIRE_DAY_TWO, "盘中", "S"],
        ],
    )
    universe = _audited_universe_loader().from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    try:
        buy = universe.next_open_execution_evidence("600001", _DAY_TWO, "buy")
        sell = universe.next_open_execution_evidence("600001", _DAY_TWO, "sell")
        refs = _row_refs(universe)
        # WHY: assert the timing PK kept both rows in the audited snapshot — the
        # S row drives the verdict, and the R row's survival proves coexistence
        # rather than dedup of the two suspend events.
        row_count = universe._connection.execute(
            "SELECT COUNT(*) FROM market_session_generation_rows_suspend_d "
            "WHERE ts_code = ? AND trade_date = ?",
            ("600001.SH", _DAY_TWO),
        ).fetchone()[0]
    finally:
        universe.close()

    assert row_count == 2
    for verdict in (buy, sell):
        assert verdict["fillable"] is False
        assert verdict["reason"] == "suspended"
        assert verdict["raw_price"] is None
        assert verdict["generation_proof"] == refs[_DAY_TWO]


@pytest.mark.parametrize("side", ["", "hold", "x", "long"])
def test_illegal_side_raises(tmp_path, side):
    # WHY: the fill gate is a binary buy/sell contract; any other side is a
    # caller bug that must surface as a hard error (MarketEvidenceError, itself a
    # ValueError) rather than silently defaulting to a tradable verdict. Note
    # the gate normalizes with strip().lower(), so case/whitespace variants of a
    # valid side (e.g. " BUY ") are accepted and must NOT appear here — only
    # genuinely non-buy/sell strings raise.
    artifact, audit = _publish_with_day_two_override(tmp_path, label="bad-side")
    universe = _open(artifact, audit)
    try:
        with pytest.raises(MarketEvidenceError):
            universe.next_open_execution_evidence("600001", _DAY_TWO, side)
    finally:
        universe.close()


def test_rejected_verdicts_never_leak_adjusted_or_raw_price(tmp_path):
    # WHY: a rejection must report raw_price None on every non-tradable path so
    # no downstream consumer can mistake a locked / suspended reason for an
    # executable price. This sweeps the full rejection surface in one place.
    cases = [
        ("up-lock-buy", _DAILY_OPEN_AT_UP, _LIMITS, None, "buy"),
        ("down-lock-sell", _DAILY_OPEN_AT_DOWN, _LIMITS, None, "sell"),
        ("suspend-s-buy", None, None, [["600001.SH", _WIRE_DAY_TWO, "全天", "S"]], "buy"),
        ("suspend-s-sell", None, None, [["600001.SH", _WIRE_DAY_TWO, "全天", "S"]], "sell"),
    ]
    for label, daily, stk_limit, suspend, side in cases:
        artifact, audit = _publish_with_day_two_override(
            tmp_path,
            label=label,
            daily=daily,
            stk_limit=stk_limit,
            suspend=suspend,
        )
        universe = _open(artifact, audit)
        try:
            verdict = universe.next_open_execution_evidence("600001", _DAY_TWO, side)
            refs = _row_refs(universe)
        finally:
            universe.close()

        assert verdict["fillable"] is False
        assert verdict["raw_price"] is None
        assert verdict["reason"] in {
            "buy_open_locked_limit",
            "sell_open_locked_limit",
            "suspended",
        }
        assert verdict["generation_proof"] == refs[_DAY_TWO]
