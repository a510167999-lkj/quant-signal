"""``ArtifactNativeReplayAdapter`` —— 审计 PIT artifact 之上的严格只读回放面。

WHY: 回放运行只允许透过这个适配器读 artifact 的因果信号 K 线与次开盘成交证据。
适配器绝不能触碰任何外部 provider 或缓存（API 根本没有入口），不能对次开盘做
启发式回退，且必须拒绝伪对象 / 非 v4 / 非审计 / final-OOS-eligible / 已关闭的
universe。所有用例都走真实的 ingest → audit → publish → from_file 管线，使每个
断言都绑定到 manifest 锚定的 generation proof，正如真实消费者所见。
"""

import inspect
import shutil

import pandas as pd
import pytest

from app.research_artifact_replay import ArtifactNativeReplayAdapter
from app.research_composite_universe import CompositeAuditedUniverse
from app.research_pit_store import PITReceiptError, assert_same_temporal_authority
from tests.test_research_pit_causal_signal_bars import (
    _open,
    _publish_two_day_artifact,
    _publish_two_day_artifact_with_day_two_factor,
)
from tests.test_research_pit_next_open_execution_evidence import (
    _DAILY_OPEN_AT_UP,
    _LIMITS,
    _publish_with_day_two_override,
)
from tests.test_research_composite_universe import _annual_segments, _bar

_DAY_ONE = "2024-01-02"
_DAY_TWO = "2024-01-03"


def test_cross_role_artifacts_cannot_be_composed():
    class Bound:
        temporal_role = "development"
        temporal_contract_sha256 = "a" * 64

    other = Bound()
    other.temporal_role = "contaminated_diagnostic"
    with pytest.raises(PITReceiptError, match="temporal authorit"):
        assert_same_temporal_authority(Bound(), other)


def _adapter(artifact, audit) -> ArtifactNativeReplayAdapter:
    universe = _open(artifact, audit)
    universe.external_temporal_authority_verified = True
    universe.manifest["temporal_binding"] = {
        "schema_version": "research-artifact-temporal-binding/v1",
        "role": "development",
        "contract_sha256": "a" * 64,
    }
    return ArtifactNativeReplayAdapter(universe)


# ---------------------------------------------------------------- signal_frame


def test_signal_frame_correct_after_source_store_deleted(tmp_path):
    artifact, audit = _publish_two_day_artifact(tmp_path, label="replay")

    # WHY: 删除源 store 证明 snapshot 是自包含的 —— adapter 读到的每一根 K 线、
    # 每一个 factor 与 generation proof 都只来自 artifact 自身。
    shutil.rmtree(tmp_path / "replay-store")

    universe = _open(artifact, audit)
    adapter = ArtifactNativeReplayAdapter(universe)
    try:
        bars = universe.causal_signal_bars("600001", _DAY_ONE, _DAY_TWO)
        frame = adapter.signal_frame("600001", _DAY_ONE, _DAY_TWO)
    finally:
        universe.close()

    assert list(frame.columns) == [
        "date",
        "open",
        "high",
        "low",
        "close",
        "raw_open",
        "raw_high",
        "raw_low",
        "raw_close",
        "bar_adj_factor",
        "as_of_adj_factor",
        "adjustment_as_of_date",
        "volume",
        "amount",
        "change_pct",
        "raw_pre_close",
        "raw_volume_lots",
        "raw_amount_thousand_yuan",
        "generation_proof",
    ]
    # WHY: 按 date 升序，且 OHLC 必须取自 signal_*（这里是 raw 因子为 1.5/1.5 = 1，
    # 但仍按 bars 的 signal_* 字段逐行对齐证明来源）。
    assert list(frame["date"]) == ["2024-01-02", "2024-01-03"]
    for row, bar in zip(frame.to_dict("records"), bars):
        assert row["open"] == pytest.approx(bar["signal_open"])
        assert row["high"] == pytest.approx(bar["signal_high"])
        assert row["low"] == pytest.approx(bar["signal_low"])
        assert row["close"] == pytest.approx(bar["signal_close"])
        # raw_* 原样保留，绝不与 signal 混淆。
        assert row["raw_open"] == pytest.approx(bar["raw_open"])
        assert row["raw_high"] == pytest.approx(bar["raw_high"])
        assert row["raw_low"] == pytest.approx(bar["raw_low"])
        assert row["raw_close"] == pytest.approx(bar["raw_close"])
        assert row["bar_adj_factor"] == pytest.approx(bar["bar_adj_factor"])
        assert row["as_of_adj_factor"] == pytest.approx(bar["as_of_adj_factor"])
        assert row["adjustment_as_of_date"] == bar["adjustment_as_of_date"]
        assert row["volume"] == pytest.approx(bar["volume_shares"])
        assert row["amount"] == pytest.approx(bar["amount_yuan"])
        assert row["change_pct"] == pytest.approx(bar["raw_pct_chg"])
        assert row["raw_pre_close"] == pytest.approx(bar["raw_pre_close"])
        assert row["raw_volume_lots"] == pytest.approx(bar["raw_volume_lots"])
        assert row["raw_amount_thousand_yuan"] == pytest.approx(
            bar["raw_amount_thousand_yuan"]
        )
        assert row["generation_proof"] == bar["generation_proof"]


def test_signal_frame_open_uses_signal_price_while_raw_preserved(tmp_path):
    # WHY: 把第二日 adj_factor 改成 99，使 signal_* 偏离 raw_* —— 证明 OHLC 列确实
    # 取自因果复权后的 signal_*，而 raw_* 仍保留原始价。
    artifact, audit = _publish_two_day_artifact_with_day_two_factor(tmp_path, factor=99.0)
    adapter = _adapter(artifact, audit)
    try:
        frame = adapter.signal_frame("600001", _DAY_ONE, _DAY_TWO)
    finally:
        adapter._universe.close()

    day_one = frame[frame["date"] == _DAY_ONE].iloc[0]
    assert day_one["raw_open"] == pytest.approx(10.0)
    assert day_one["open"] == pytest.approx(10.0 * 1.5 / 99.0)
    assert day_one["open"] != pytest.approx(day_one["raw_open"])
    assert day_one["bar_adj_factor"] == pytest.approx(1.5)
    assert day_one["as_of_adj_factor"] == pytest.approx(99.0)
    assert day_one["adjustment_as_of_date"] == _DAY_TWO


def test_signal_frame_sorts_ascending_by_date(tmp_path):
    artifact, audit = _publish_two_day_artifact(tmp_path, label="sorted")
    adapter = _adapter(artifact, audit)
    try:
        frame = adapter.signal_frame("600001", _DAY_ONE, _DAY_TWO)
    finally:
        adapter._universe.close()
    assert list(frame["date"]) == sorted(frame["date"])
    assert isinstance(frame, pd.DataFrame)


def test_signal_frame_accepts_verified_composite_and_uses_global_asof_factor():
    first, second = _annual_segments()
    first.bars = [
        _bar(
            "2022-12-29",
            raw_open=10.0,
            bar_factor=1.0,
            local_asof_factor=1.0,
            local_adjustment_date="2022-12-30",
            segment="2022",
        )
    ]
    second.bars = [
        _bar(
            "2023-06-01",
            raw_open=6.0,
            bar_factor=2.0,
            local_asof_factor=2.0,
            local_adjustment_date="2023-06-01",
            segment="2023",
        )
    ]
    composite = CompositeAuditedUniverse(
        [first, second],
        permitted_boundary_gaps=[("2022-12-30", "2023-01-03")],
    )

    adapter = ArtifactNativeReplayAdapter(
        composite,
        expected_temporal_contract_sha256="c" * 64,
        expected_temporal_role="development",
    )
    frame = adapter.signal_frame("600001", "2022-12-29", "2023-06-01")

    assert adapter.artifact_root_sha256 == composite.composite_root_sha256
    assert list(frame["date"]) == ["2022-12-29", "2023-06-01"]
    assert frame.iloc[0]["open"] == pytest.approx(5.0)
    assert frame.iloc[1]["open"] == pytest.approx(6.0)
    assert list(frame["as_of_adj_factor"]) == pytest.approx([2.0, 2.0])
    assert adapter.next_open("600001", "2023-01-03", "buy")["segment"] == "2023"


def test_signal_frame_rejects_after_universe_closed(tmp_path):
    artifact, audit = _publish_two_day_artifact(tmp_path, label="closed-frame")
    universe = _open(artifact, audit)
    universe.close()
    adapter = ArtifactNativeReplayAdapter.__new__(ArtifactNativeReplayAdapter)
    # WHY: 绕过构造校验，直接复用一个已关闭的 universe，证明 signal_frame 不会
    # 在 universe 关闭后还能读出数据 —— 读取会落到 artifact 的 fail-closed 路径。
    adapter._universe = universe
    with pytest.raises(PITReceiptError, match="closed"):
        adapter.signal_frame("600001", _DAY_ONE, _DAY_TWO)


# --------------------------------------------------------- no provider surface


def test_adapter_exposes_no_provider_or_cache_entrypoint():
    # WHY: 适配器的全部公共方法签名都不含 provider / cache 参数 —— 外部数据源在
    # API 层面就没有入口，回放因此不可能悄悄走网络。
    for method_name in ("signal_frame", "next_open", "baseline_scenarios"):
        params = inspect.signature(getattr(ArtifactNativeReplayAdapter, method_name))
        assert not any(
            "provider" in name or "cache" in name for name in params.parameters
        ), f"{method_name} leaked a provider/cache parameter"
    # 构造器同样不接受 provider/cache。
    init_params = inspect.signature(ArtifactNativeReplayAdapter.__init__)
    assert not any(
        "provider" in name or "cache" in name for name in init_params.parameters
    )


# ------------------------------------------------------------------- next_open


def test_next_open_passes_through_locked_verdict(tmp_path):
    # WHY: 次开盘锁涨停时，买盘不可成交 —— adapter 必须原样透传该拒绝，绝不做
    # 启发式回退去捏造一个成交价。
    artifact, audit = _publish_with_day_two_override(
        tmp_path, label="lock-pass", daily=_DAILY_OPEN_AT_UP, stk_limit=_LIMITS
    )
    universe = _open(artifact, audit)
    adapter = ArtifactNativeReplayAdapter(universe)
    try:
        expected = universe.next_open_execution_evidence("600001", _DAY_TWO, "buy")
        verdict = adapter.next_open("600001", _DAY_TWO, "buy")
    finally:
        universe.close()

    assert verdict == expected
    assert verdict["fillable"] is False
    assert verdict["reason"] == "buy_open_locked_limit"
    assert verdict["raw_price"] is None


def test_next_open_passes_through_suspended_verdict(tmp_path):
    artifact, audit = _publish_with_day_two_override(
        tmp_path,
        label="suspend-pass",
        suspend=[["600001.SH", "20240103", "全天", "S"]],
    )
    universe = _open(artifact, audit)
    adapter = ArtifactNativeReplayAdapter(universe)
    try:
        expected = universe.next_open_execution_evidence("600001", _DAY_TWO, "buy")
        verdict = adapter.next_open("600001", _DAY_TWO, "buy")
    finally:
        universe.close()

    assert verdict == expected
    assert verdict["fillable"] is False
    assert verdict["reason"] == "suspended"
    assert verdict["raw_price"] is None


def test_next_open_passes_through_missing_verdict_without_fallback(tmp_path):
    # WHY: missing 类拒绝（缺逐笔/缺涨跌停）对 artifact 内被覆盖的 in-universe
    # 标的在结构上不可达，但 adapter 的契约是“逐字透传、绝不回退”。把 universe
    # 的证据方法替换成一个合成 missing 裁决，直接证明 adapter 既不改写也不回退。
    artifact, audit = _publish_two_day_artifact(tmp_path, label="missing-pass")
    universe = _open(artifact, audit)
    adapter = ArtifactNativeReplayAdapter(universe)
    synthetic = {
        "fillable": False,
        "reason": "missing_raw_bar",
        "raw_price": None,
        "generation_proof": {"trade_date": _DAY_TWO, "sentinel": True},
    }
    original = universe.next_open_execution_evidence
    universe.next_open_execution_evidence = lambda *a, **k: synthetic  # type: ignore[assignment]
    try:
        verdict = adapter.next_open("600001", _DAY_TWO, "buy")
    finally:
        universe.next_open_execution_evidence = original  # type: ignore[assignment]
        universe.close()

    assert verdict is synthetic


def test_next_open_default_side_is_buy(tmp_path):
    # WHY: 默认 side='buy'，与 next_open_execution_evidence 的 buy 口径一致。
    artifact, audit = _publish_two_day_artifact(tmp_path, label="default-side")
    universe = _open(artifact, audit)
    adapter = ArtifactNativeReplayAdapter(universe)
    try:
        explicit = universe.next_open_execution_evidence("600001", _DAY_TWO, "buy")
        verdict = adapter.next_open("600001", _DAY_TWO)
    finally:
        universe.close()
    assert verdict == explicit


# ------------------------------------------------------------- baseline grid


def _open_adapter(tmp_path, label="base"):
    artifact, audit = _publish_two_day_artifact(tmp_path, label=label)
    return _adapter(artifact, audit)


def test_baseline_scenarios_are_four_exact_and_ordered(tmp_path):
    adapter = _open_adapter(tmp_path)
    try:
        scenarios = adapter.baseline_scenarios()
    finally:
        adapter._universe.close()

    assert len(scenarios) == 4
    expected_grid = [
        ("base_1x", 1.0, 8.0, 25, 10),
        ("base_2_08x", 2.08, 8.0, 25, 10),
        ("double_cost_1x", 1.0, 8.0, 50, 10),
        ("double_slippage_1x", 1.0, 8.0, 25, 20),
    ]
    for scenario, (name, exposure, financing, roundtrip, slip) in zip(
        scenarios, expected_grid
    ):
        assert scenario["name"] == name
        assert scenario["exposure_multiplier"] == exposure
        assert scenario["annual_financing_rate_pct"] == financing
        assert scenario["roundtrip_cost_bps"] == roundtrip
        assert scenario["slippage_bps"] == slip
        assert scenario["final_oos_eligible"] is False
        # 场景被冻结。
        with pytest.raises(TypeError):
            scenario["name"] = "tampered"  # type: ignore[index]


def test_baseline_scenarios_hash_stable_and_bound_to_artifact(tmp_path):
    # WHY: 场景指纹只覆盖成本身份（不含 artifact_root），故跨任意两个 artifact 都
    # 必须逐位相同；contract_sha256 把有序场景指纹绑定到 artifact_root，故同一
    # artifact 两次构造必须稳定，但不同 artifact 之间必不同。
    adapter_a = _open_adapter(tmp_path, label="a")
    artifact_root_a = adapter_a.artifact_root_sha256
    contract_a = adapter_a.baseline_scenarios()
    adapter_a._universe.close()

    adapter_b = _open_adapter(tmp_path, label="b")
    artifact_root_b = adapter_b.artifact_root_sha256
    contract_b = adapter_b.baseline_scenarios()
    adapter_b._universe.close()

    scenario_hashes_a = [s["scenario_sha256"] for s in contract_a]
    scenario_hashes_b = [s["scenario_sha256"] for s in contract_b]
    assert scenario_hashes_a == scenario_hashes_b  # 成本身份跨 artifact 稳定
    assert artifact_root_a != artifact_root_b  # 两个不同 artifact
    # 每个场景的 artifact_root 字段各自绑定到所属 artifact。
    assert all(s["artifact_root_sha256"] == artifact_root_a for s in contract_a)
    assert all(s["artifact_root_sha256"] == artifact_root_b for s in contract_b)
    # 指纹形态：64-hex。
    assert all(len(h) == 64 for h in scenario_hashes_a)


def test_baseline_contract_stable_for_same_artifact(tmp_path):
    # WHY: contract_sha256 绑定 artifact_root，故同一 artifact 两次打开构造出的
    # adapter 必须给出完全相同的 contract。
    artifact, audit = _publish_two_day_artifact(tmp_path, label="same")
    adapter_first = _adapter(artifact, audit)
    contract_first = adapter_first.contract_sha256
    artifact_root = adapter_first.artifact_root_sha256
    adapter_first._universe.close()

    adapter_again = _adapter(artifact, audit)
    contract_again = adapter_again.contract_sha256
    adapter_again._universe.close()

    assert contract_first == contract_again
    assert len(contract_first) == 64
    assert adapter_again.artifact_root_sha256 == artifact_root


def test_single_artifact_baseline_contract_hash_remains_pinned():
    assert (
        _build_baseline_contract_via_module()["contract_sha256"]
        == "2fd126409a9a664954c64c53d2b27af3cd7f86678219b3652d605b7fb1a7c211"
    )


def test_baseline_scenario_sha256_pinned(tmp_path):
    # WHY: 把四个场景的成本指纹钉死成回归字面量，任何字段漂移（融资率、费率、
    # 滑点、exposure、顺序）都会让这里失败。
    adapter = _open_adapter(tmp_path)
    try:
        scenarios = adapter.baseline_scenarios()
    finally:
        adapter._universe.close()
    pinned = {
        "base_1x": "14d3bb56a351f489fc96c21dab86c91af2543f9de337a63a27ebf60dcff8fc9d",
        "base_2_08x": "c2fc9a1f6c345ed4413608415002113d854de617f3e2de69c11b2181cbe32abb",
        "double_cost_1x": "66b276377cceea96c2df10d5acf0e31725a18d8cfb467286fa714870e75496af",
        "double_slippage_1x": "81d8ebc5681de4b7a7c285c8b59110886c1279f3bd060518049b9cc2f685c4cf",
    }
    actual = {s["name"]: s["scenario_sha256"] for s in scenarios}
    assert actual == pinned


def test_baseline_contract_rejects_exposure_over_cap(monkeypatch):
    # WHY: 即便有人改了网格常量把 exposure 抬过 2.08，构造也必须在此大声失败。
    drifted = (("leaky", 3.0, 8.0, 25, 10),)
    monkeypatch.setattr(
        "app.research_artifact_replay._BASELINE_SCENARIO_GRID", drifted
    )
    with pytest.raises(PITReceiptError, match="exceeds cap"):
        _build_baseline_contract_via_module()


def _build_baseline_contract_via_module():
    from app.research_artifact_replay import _build_baseline_contract

    return _build_baseline_contract("0" * 64)


def test_baseline_scenarios_feed_fixed_sweep_without_unit_mismatch(tmp_path, monkeypatch):
    # WHY: scenario 的字段名与单位必须与 _fixed_sweep / sweep_qualified_trades 完全
    # 一致——融资率是百分数 8.0 而非小数 0.08，否则 8% 资金成本会被误读成 0.08%。
    # 把每个 scenario 直接当作 strategy dict 喂入 _fixed_sweep，并 monkeypatch
    # capture 真正传给 sweep_qualified_trades 的参数：只有字段名匹配才会读到非默认
    # 值（exposure 2.08、roundtrip 50、slippage 20 都是默认之外的值），从而同时
    # 证明字段名正确、单位未被误读。
    import app.research_validation as rv
    from app.research_validation import _fixed_sweep

    adapter = _open_adapter(tmp_path)
    try:
        scenarios = adapter.baseline_scenarios()
    finally:
        adapter._universe.close()

    captured: dict = {}

    def fake_sweep(trades, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return {"stub": True}

    monkeypatch.setattr(rv, "sweep_qualified_trades", fake_sweep)

    expected = {
        "base_1x": (1.0, 8.0, 25, 10),
        "base_2_08x": (2.08, 8.0, 25, 10),
        "double_cost_1x": (1.0, 8.0, 50, 10),
        "double_slippage_1x": (1.0, 8.0, 25, 20),
    }
    for scenario in scenarios:
        captured.clear()
        # 一个非空 trade 列表即可让 _fixed_sweep 走到 sweep 调用；交易不会真跑。
        _fixed_sweep([{"symbol": "600001"}], dict(scenario))
        exposure, financing_pct, roundtrip, slip = expected[scenario["name"]]
        assert captured["exposure_multipliers"] == [exposure]
        assert captured["annual_financing_rate_pct"] == financing_pct
        assert captured["roundtrip_cost_bps"] == roundtrip
        assert captured["slippage_bps"] == slip
        # 关键反断言：融资率读到的是百分数 8.0，绝不是 0.08 的小数口径。
        assert captured["annual_financing_rate_pct"] != 0.08


# ------------------------------------------------------------- construction


def test_constructor_rejects_fake_object():
    # WHY: 适配器只接受真正的 AuditedPointInTimeUniverse —— 任何鸭子类型伪对象
    # 都必须在边界处被拒。
    class FakeUniverse:
        is_audited_store_artifact = True

        @property
        def manifest(self):
            return {"schema_version": "audited-pit-universe/v4"}

        @property
        def final_oos_eligible(self):
            return False

        def _require_open(self):
            return None

    with pytest.raises(PITReceiptError, match="AuditedPointInTimeUniverse"):
        ArtifactNativeReplayAdapter(FakeUniverse())


def test_constructor_rejects_closed_universe(tmp_path):
    artifact, audit = _publish_two_day_artifact(tmp_path, label="closed-ctor")
    universe = _open(artifact, audit)
    universe.close()
    with pytest.raises(PITReceiptError, match="closed"):
        ArtifactNativeReplayAdapter(universe)


def test_constructor_rejects_non_v4_manifest(tmp_path):
    # WHY: schema_version 必须是 v4 —— 任何其它版本都不可消费。manifest 在
    # AuditedPointInTimeUniverse 上是普通实例属性，直接覆盖即可模拟版本漂移。
    artifact, audit = _publish_two_day_artifact(tmp_path, label="nonv4")
    universe = _open(artifact, audit)
    original = universe.manifest
    universe.manifest = {**original, "schema_version": "legacy/v1"}
    try:
        with pytest.raises(PITReceiptError, match="v4"):
            ArtifactNativeReplayAdapter(universe)
    finally:
        universe.manifest = original
        universe.close()


def test_constructor_rejects_legacy_unbound_temporal_authority(tmp_path):
    artifact, audit = _publish_two_day_artifact(tmp_path, label="unbound-ctor")
    universe = _open(artifact, audit)
    universe.external_temporal_authority_verified = False
    try:
        with pytest.raises(PITReceiptError, match="external temporal authority"):
            ArtifactNativeReplayAdapter(
                universe, expected_temporal_contract_sha256="a" * 64
            )
    finally:
        universe.close()


def test_constructor_requires_development_role_and_expected_contract(tmp_path):
    artifact, audit = _publish_two_day_artifact(tmp_path, label="role-ctor")
    universe = _open(artifact, audit)
    universe.external_temporal_authority_verified = True
    universe.manifest["temporal_binding"] = {
        "schema_version": "research-artifact-temporal-binding/v1",
        "role": "development",
        "contract_sha256": "a" * 64,
    }
    try:
        with pytest.raises(PITReceiptError, match="development"):
            ArtifactNativeReplayAdapter(
                universe,
                expected_temporal_contract_sha256=universe.temporal_contract_sha256,
                expected_temporal_role="contaminated_diagnostic",
            )
        with pytest.raises(PITReceiptError, match="contract hash"):
            ArtifactNativeReplayAdapter(
                universe,
                expected_temporal_contract_sha256="f" * 64,
                expected_temporal_role="development",
            )
    finally:
        universe.close()
