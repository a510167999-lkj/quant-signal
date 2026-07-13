"""Causal signal-bar reader over the audited PIT universe snapshot.

WHY: ``AuditedPointInTimeUniverse.causal_signal_bars`` is the only sanctioned way
to turn the frozen, hash-anchored receipt snapshot into signal-price bars. It
must read solely from the read-only SQLite snapshot (no network, no source
store), bind every daily / adj_factor row to the manifest's per-session market
generation proof, and hand the raw rows to
``app.research_market_data.causal_adjusted_bars`` so that later adjustment
factors are excluded by construction.
"""

import shutil

import pytest

from app.research_pit_store import PITReceiptError, PITReceiptStore
from tests.test_research_pit_store import (
    _audited_universe_loader,
    _ingest_complete_two_day_fixture,
)
import tests.test_research_pit_store as _pit_store_test_module


def _publish_two_day_artifact(tmp_path, *, label):
    """Build a standard two-day fixture and publish its audited artifact.

    Returns ``(artifact, audit)``. The source store lives under
    ``tmp_path/<label>-store`` and the artifact under ``tmp_path/<label>-art``.
    """

    store = PITReceiptStore(str(tmp_path / f"{label}-store"))
    _ingest_complete_two_day_fixture(store)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / f"{label}-art"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    return artifact, audit


def _publish_two_day_artifact_with_day_two_factor(tmp_path, *, factor):
    """Publish an artifact whose 2024-01-03 adj_factor generation is overridden.

    WHY: ``causal_signal_bars`` must only read factors in ``[start, as_of]``.
    Overriding the second session's factor proves an ``as_of`` anchored on the
    first session cannot see it, and that an ``as_of`` on the second session
    actually applies it (so ``signal_*`` diverges from ``raw_*``).
    """

    real_defaults = _pit_store_test_module._market_default_rows

    def patched(dataset, trade_date):
        rows = real_defaults(dataset, trade_date)
        if dataset == "adj_factor" and trade_date == "2024-01-03":
            return [["600001.SH", "20240103", factor]]
        return rows

    store = PITReceiptStore(str(tmp_path / "factor-store"))
    _pit_store_test_module._market_default_rows = patched
    try:
        _ingest_complete_two_day_fixture(store)
    finally:
        _pit_store_test_module._market_default_rows = real_defaults
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    artifact = store.publish_universe_artifact(
        str(tmp_path / "factor-art"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    return artifact, audit


def _open(artifact, audit):
    return _audited_universe_loader().from_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )


def test_causal_signal_bars_binds_generation_proof_after_source_store_deleted(tmp_path):
    artifact, audit = _publish_two_day_artifact(tmp_path, label="base")

    # WHY: deleting the source store proves the snapshot is self-contained —
    # every bar, factor and generation proof lives inside the bundle.
    shutil.rmtree(tmp_path / "base-store")

    universe = _open(artifact, audit)
    try:
        bars = universe.causal_signal_bars("600001", "2024-01-02", "2024-01-03")
    finally:
        universe.close()

    assert [bar["trade_date"] for bar in bars] == ["2024-01-02", "2024-01-03"]
    refs = {ref["trade_date"]: ref for ref in universe.manifest["market_generations"]["refs"]}
    for bar in bars:
        # raw and signal fields coexist on every row.
        for field in ("open", "high", "low", "close"):
            assert f"raw_{field}" in bar
            assert f"signal_{field}" in bar
        # Tushare daily 的原始单位必须保留并显式换算，不能把“手/千元”
        # 误当作“股/元”喂给成交额与流动性筛选。
        assert bar["raw_volume_lots"] == pytest.approx(1000.0)
        assert bar["raw_amount_thousand_yuan"] == pytest.approx(10100.0)
        assert bar["volume_shares"] == pytest.approx(100_000.0)
        assert bar["amount_yuan"] == pytest.approx(10_100_000.0)
        assert bar["raw_pct_chg"] == pytest.approx(3.03)
        assert bar["raw_pre_close"] == pytest.approx(9.9)
        # five-field generation proof equals the manifest ref for that session.
        assert set(bar["generation_proof"]) == {
            "trade_date",
            "generation_id",
            "manifest_sha256",
            "lineage_sha256",
            "vintage",
        }
        assert bar["generation_proof"] == refs[bar["trade_date"]]


def test_causal_signal_bars_resolves_unique_historical_member_missing_current_master(
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
    artifact, audit = _publish_two_day_artifact(tmp_path, label="missing-master")

    universe = _open(artifact, audit)
    try:
        bars = universe.causal_signal_bars("600001", "2024-01-02", "2024-01-03")
    finally:
        universe.close()

    assert [bar["trade_date"] for bar in bars] == ["2024-01-02", "2024-01-03"]


def test_causal_signal_bars_as_of_day_one_ignores_day_two_factor(tmp_path):
    # WHY: as_of anchored on the first session reads only [day1, day1], so a
    # wildly different second-session factor must not move the result.
    base_artifact, base_audit = _publish_two_day_artifact(tmp_path, label="cmp")
    fxd_artifact, fxd_audit = _publish_two_day_artifact_with_day_two_factor(tmp_path, factor=99.0)

    base = _open(base_artifact, base_audit)
    fxd = _open(fxd_artifact, fxd_audit)
    try:
        base_bars = base.causal_signal_bars("600001", "2024-01-02", "2024-01-02")
        fxd_bars = fxd.causal_signal_bars("600001", "2024-01-02", "2024-01-02")
    finally:
        base.close()
        fxd.close()

    # WHY: the generation proof is store-specific (per-run generation ids /
    # hashes differ), so only the causal signal output — raw and signal
    # prices — must be invariant under the unrelated second-session factor.
    def _signal_view(bars):
        return [
            {key: value for key, value in bar.items() if key != "generation_proof"} for bar in bars
        ]

    assert _signal_view(base_bars) == _signal_view(fxd_bars)
    assert [bar["trade_date"] for bar in base_bars] == ["2024-01-02"]


def test_causal_signal_bars_adjusts_signal_against_as_of_factor(tmp_path):
    # WHY: with day-one factor 1.5 and day-two factor 99, a day-two as_of must
    # scale day one's signal prices by 1.5/99 while leaving raw prices intact —
    # proving the adjustment is real and raw_* is never overwritten.
    artifact, audit = _publish_two_day_artifact_with_day_two_factor(tmp_path, factor=99.0)
    universe = _open(artifact, audit)
    try:
        bars = universe.causal_signal_bars("600001", "2024-01-02", "2024-01-03")
    finally:
        universe.close()

    day_one = next(bar for bar in bars if bar["trade_date"] == "2024-01-02")
    assert day_one["raw_open"] == pytest.approx(10.0)
    assert day_one["signal_open"] == pytest.approx(10.0 * 1.5 / 99.0)
    assert day_one["raw_open"] != pytest.approx(day_one["signal_open"])


def test_causal_signal_bars_rejects_unknown_symbol(tmp_path):
    artifact, audit = _publish_two_day_artifact(tmp_path, label="reject")
    universe = _open(artifact, audit)
    try:
        with pytest.raises(PITReceiptError, match="unknown symbol"):
            universe.causal_signal_bars("999999", "2024-01-02", "2024-01-03")
    finally:
        universe.close()


@pytest.mark.parametrize(
    ("start", "as_of"),
    [
        ("2024-01-01", "2024-01-03"),  # start before coverage
        ("2024-01-02", "2024-01-04"),  # as_of after coverage
        ("2024-01-03", "2024-01-02"),  # start after as_of
    ],
)
def test_causal_signal_bars_rejects_out_of_window(tmp_path, start, as_of):
    artifact, audit = _publish_two_day_artifact(tmp_path, label="window")
    universe = _open(artifact, audit)
    try:
        with pytest.raises(ValueError):
            universe.causal_signal_bars("600001", start, as_of)
    finally:
        universe.close()


def test_causal_signal_bars_rejects_after_close(tmp_path):
    artifact, audit = _publish_two_day_artifact(tmp_path, label="closed")
    universe = _open(artifact, audit)
    universe.close()
    with pytest.raises(PITReceiptError, match="closed"):
        universe.causal_signal_bars("600001", "2024-01-02", "2024-01-03")
