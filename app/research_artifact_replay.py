"""严格只读回放适配器：在单个已审计 PIT universe artifact 之上。

WHY: 研究回放必须且仅能从冻结的、哈希锚定的 artifact 读取信号与执行证据，
绝不能触碰实时 provider 或缓存。``ArtifactNativeReplayAdapter`` 是回放运行
唯一被允许接触的面：它不持有任何网络或缓存句柄，任何方法都不接受
provider / cache 参数，所有读取都委托给构造时传入的、已经打开的
``AuditedPointInTimeUniverse``。

该适配器刻意不接回 ``research_backtest`` 循环，也不触碰 final OOS —— 它只是
把 artifact 的因果信号 K 线和次开盘成交证据原样暴露成一个研究友好的形状。
"""

from __future__ import annotations

import hashlib
import inspect
import json
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd

from app.research_composite_universe import CompositeAuditedUniverse
from app.research_pit_store import (
    UNIVERSE_ARTIFACT_SCHEMA_VERSION,
    AuditedPointInTimeUniverse,
    PITReceiptError,
)

# 成本网格的字面常量。WHY: 这四个 baseline 场景是每次回放都要打分的固定研究
# 网格，其顺序与字段绝不能悄悄漂移，故在构造时再按规格字面量校验一次，并哈希
# 进 contract_sha256 作为回归锚点。字段名与单位刻意与消费接口
# ``research_validation._fixed_sweep`` / ``sweep_qualified_trades`` 完全一致
# （融资率是百分数 8.0 而非小数 0.08，滑点是单边 bps），使 scenario 可直接作为
# strategy dict 喂入而不会因口径错位被误读。
_MAX_EXPOSURE = 2.08
_BASE_ANNUAL_FINANCING_RATE_PCT = 8.0
_BASE_ROUNDTRIP_COST_BPS = 25
_BASE_SLIPPAGE_BPS = 10

# (name, exposure_multiplier, annual_financing_rate_pct, roundtrip_cost_bps,
#  slippage_bps) 的有序冻结网格。
_BASELINE_SCENARIO_GRID: tuple[tuple[str, float, float, int, int], ...] = (
    ("base_1x", 1.0, _BASE_ANNUAL_FINANCING_RATE_PCT, _BASE_ROUNDTRIP_COST_BPS, _BASE_SLIPPAGE_BPS),
    ("base_2_08x", _MAX_EXPOSURE, _BASE_ANNUAL_FINANCING_RATE_PCT, _BASE_ROUNDTRIP_COST_BPS, _BASE_SLIPPAGE_BPS),
    ("double_cost_1x", 1.0, _BASE_ANNUAL_FINANCING_RATE_PCT, _BASE_ROUNDTRIP_COST_BPS * 2, _BASE_SLIPPAGE_BPS),
    ("double_slippage_1x", 1.0, _BASE_ANNUAL_FINANCING_RATE_PCT, _BASE_ROUNDTRIP_COST_BPS, _BASE_SLIPPAGE_BPS * 2),
)

# signal_frame 的确定列顺序：date/open/high/low/close 取自 signal_*；研究层
# volume/amount 使用明确换算后的“股/元”，同时保留原始“手/千元”、raw OHLC、
# 复权因子锚点和 generation_proof。
_SIGNAL_FRAME_COLUMNS: tuple[str, ...] = (
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
)


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _build_baseline_contract(artifact_root_sha256: str) -> dict[str, Any]:
    """构造冻结且有序的 baseline 场景契约，并绑定到指定 artifact。

    WHY: 再按规格字面量校验一次 exposure 上限，使任何被改大越过 2.08 的常量
    在这里立即大声失败，而不是把错误的成本模型喂给回放。每个场景的
    scenario_sha256 只覆盖成本身份（不含 artifact_root），contract_sha256 再把
    有序的场景指纹与 artifact_root 绑在一起，保证整体顺序与归属都可回归。
    """

    scenarios: list[MappingProxyType] = []
    scenario_digests: list[Mapping[str, Any]] = []
    for name, exposure, financing_pct, roundtrip_bps, slippage_bps in _BASELINE_SCENARIO_GRID:
        if exposure > _MAX_EXPOSURE:
            raise PITReceiptError(f"baseline scenario exposure exceeds cap: {name}")
        identity = {
            "name": name,
            "exposure_multiplier": exposure,
            "annual_financing_rate_pct": financing_pct,
            "roundtrip_cost_bps": roundtrip_bps,
            "slippage_bps": slippage_bps,
        }
        scenario_sha256 = _digest(identity)
        record = MappingProxyType(
            {
                **identity,
                "final_oos_eligible": False,
                "artifact_root_sha256": artifact_root_sha256,
                "scenario_sha256": scenario_sha256,
            }
        )
        scenarios.append(record)
        scenario_digests.append({"name": name, "scenario_sha256": scenario_sha256})
    contract_sha256 = _digest(
        {"artifact_root_sha256": artifact_root_sha256, "scenarios": scenario_digests}
    )
    return {"scenarios": tuple(scenarios), "contract_sha256": contract_sha256}


class ArtifactNativeReplayAdapter:
    """Strict read-only replay over one audited artifact or verified composite.

    A single input must be an open v4 ``AuditedPointInTimeUniverse``. A segmented
    input must already be a fail-closed ``CompositeAuditedUniverse``. Both paths
    reject promotion-eligible or closed evidence and expose no provider/cache.
    """

    def __init__(
        self,
        universe: AuditedPointInTimeUniverse | CompositeAuditedUniverse,
        *,
        expected_temporal_contract_sha256: str | None = None,
        expected_temporal_role: str = "development",
    ) -> None:
        is_single_artifact = isinstance(universe, AuditedPointInTimeUniverse)
        is_composite = isinstance(universe, CompositeAuditedUniverse)
        if not (is_single_artifact or is_composite):
            raise PITReceiptError(
                "replay adapter requires an AuditedPointInTimeUniverse "
                "or verified CompositeAuditedUniverse"
            )
        if not universe.is_audited_store_artifact:
            raise PITReceiptError("replay adapter requires an audited store artifact")
        if is_single_artifact and (
            universe.manifest.get("schema_version")
            != UNIVERSE_ARTIFACT_SCHEMA_VERSION
        ):
            raise PITReceiptError("replay adapter requires a v4 audited universe artifact")
        if universe.final_oos_eligible is not False:
            raise PITReceiptError("replay adapter rejects final-OOS-eligible artifacts")
        strict_temporal_binding = expected_temporal_contract_sha256 is not None
        if strict_temporal_binding and universe.external_temporal_authority_verified is not True:
            raise PITReceiptError("replay adapter requires verified external temporal authority")
        if strict_temporal_binding and (
            expected_temporal_role != "development" or universe.temporal_role != "development"
        ):
            raise PITReceiptError("replay adapter requires the development temporal role")
        expected_contract = (
            expected_temporal_contract_sha256 or universe.temporal_contract_sha256
        )
        if strict_temporal_binding and (
            not isinstance(expected_contract, str)
            or universe.temporal_contract_sha256 != expected_contract
        ):
            raise PITReceiptError("replay adapter temporal contract hash mismatch")
        # WHY: 在边界处探测连接，使一个已关闭的 universe 在此就被拒绝，而不是
        # 在回放到一半时才抛出难以定位的错误。
        universe._require_open()  # noqa: SLF001 — 受信同包消费者
        self._universe = universe
        self._artifact_root_sha256 = universe.artifact_root_sha256
        self._baseline_contract = _build_baseline_contract(self._artifact_root_sha256)

    @property
    def artifact_root_sha256(self) -> str:
        return self._artifact_root_sha256

    @property
    def contract_sha256(self) -> str:
        return self._baseline_contract["contract_sha256"]

    def baseline_scenarios(self) -> tuple[Mapping[str, Any], ...]:
        """返回冻结且确定顺序的四个 baseline 成本场景。"""

        return self._baseline_contract["scenarios"]

    def signal_frame(
        self, symbol: Any, start_date: Any, as_of_date: Any
    ) -> pd.DataFrame:
        """返回 [start_date, as_of_date] 上按 date 升序的因果信号 K 线。

        OHLC 列取自 ``signal_*``（已按 as_of_date 因果复权），同时原样保留
        ``raw_*``、复权因子锚点与每行的 ``generation_proof``。不接受 provider / cache 参数：
        所有数据只读自构造时传入的 artifact。
        """

        bars = self._universe.causal_signal_bars(symbol, start_date, as_of_date)
        rows = [
            {
                "date": bar["trade_date"],
                "open": bar["signal_open"],
                "high": bar["signal_high"],
                "low": bar["signal_low"],
                "close": bar["signal_close"],
                "raw_open": bar["raw_open"],
                "raw_high": bar["raw_high"],
                "raw_low": bar["raw_low"],
                "raw_close": bar["raw_close"],
                "bar_adj_factor": bar["bar_adj_factor"],
                "as_of_adj_factor": bar["as_of_adj_factor"],
                "adjustment_as_of_date": bar["adjustment_as_of_date"],
                "volume": bar["volume_shares"],
                "amount": bar["amount_yuan"],
                "change_pct": bar["raw_pct_chg"],
                "raw_pre_close": bar["raw_pre_close"],
                "raw_volume_lots": bar["raw_volume_lots"],
                "raw_amount_thousand_yuan": bar[
                    "raw_amount_thousand_yuan"
                ],
                "generation_proof": bar["generation_proof"],
            }
            for bar in bars
        ]
        frame = pd.DataFrame(rows, columns=list(_SIGNAL_FRAME_COLUMNS))
        return frame.sort_values("date", kind="mergesort").reset_index(drop=True)

    def next_open(
        self, symbol: Any, trade_date: Any, side: str = "buy"
    ) -> dict[str, Any]:
        """直接委托给 universe 的次开盘成交证据，绝不做启发式回退。"""

        return self._universe.next_open_execution_evidence(symbol, trade_date, side)


# 公共方法签名自检：确保没有任何方法意外引入 provider / cache 入口。
for _method_name in ("signal_frame", "next_open", "baseline_scenarios"):
    _sig_params = inspect.signature(getattr(ArtifactNativeReplayAdapter, _method_name))
    assert not any(
        "provider" in param or "cache" in param
        for param in _sig_params.parameters
    ), f"{_method_name} must not accept provider/cache parameters"
del _method_name, _sig_params
