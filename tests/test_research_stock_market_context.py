"""stock-only 市场宽度数据契约与纯映射的测试。

覆盖：
- `_historical_market_breadth` 的 PIT 批量 membership（items_as_of 一天一次）、
  missing 分母 / coverage、历史 ST 排除、无 future row、60d / 分位 / 日收益准确性。
- `_stock_breadth_market_context` 的 unknown fail-closed 原因、五状态及 exact 边界。
- `_stock_market_returns` / `_stock_relative_strength_context` 的相对强度与 leader。
- 输出无 proxy / etf 字样；输入不变；deterministic。

禁止 provider / cache / network：全部用内存 fake universe 与就地构造的 frame。
"""
import copy

import numpy as np
import pandas as pd
import pytest

from app.research_context import (
    STOCK_MARKET_CONTEXT_SCHEMA_VERSION,
    STOCK_MARKET_MIN_COVERAGE_PCT,
    STOCK_MARKET_MIN_ELIGIBLE,
    _historical_market_breadth,
    _stock_breadth_market_context,
    _stock_market_returns,
    _stock_relative_strength_context,
)


SIGNAL_DATE = "2024-03-15"


def _frame_at(
    signal_date,
    *,
    close,
    ma20,
    ma60,
    change_pct,
    return_20d,
    return_60d,
    amount=1_000_000_000,
    length=61,
):
    """构造 length 行的 frame，仅最后一行（index=length-1）落在 signal_date。

    breadth 循环从 index 60 开始读，故 length=61 时只读 signal_date 当天那一行，
    其余行为填充（不被读取），便于精确控制当天横截面取值。
    """
    dates = pd.date_range(end=signal_date, periods=length).strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "date": dates,
            "open": [close] * length,
            "high": [close] * length,
            "low": [close] * length,
            "close": [close] * length,
            "volume": [100_000_000] * length,
            "amount": [amount] * length,
            "change_pct": [change_pct] * length,
            "ma20": [ma20] * length,
            "ma60": [ma60] * length,
            "return_20d": [return_20d] * length,
            "return_60d": [return_60d] * length,
        }
    )


def _payload(symbol, frame):
    return {"base": {"symbol": symbol}, "frame": frame}


class _BatchUniverse:
    """fake universe：支持 items_as_of 批量 + item_as_of 单点（用于断言不被调用）。"""

    def __init__(self, membership_by_date):
        self.membership_by_date = membership_by_date
        self.items_calls = []
        self.item_calls = 0

    def items_as_of(self, signal_date):
        self.items_calls.append(signal_date)
        return [dict(m) for m in self.membership_by_date.get(signal_date, [])]

    def item_as_of(self, symbol, signal_date):
        self.item_calls += 1
        return None


# ---------------------------------------------------------------------------
# Part A: _historical_market_breadth —— items_as_of 批量路径
# ---------------------------------------------------------------------------


def test_items_as_of_called_once_per_date_and_item_as_of_never_called():
    membership = {
        SIGNAL_DATE: [
            {"symbol": "000001", "name": "正常甲"},
            {"symbol": "000002", "name": "正常乙"},
            {"symbol": "000003", "name": "正常丙"},
            {"symbol": "000099", "name": "*ST退"},  # 排除
        ]
    }
    universe = _BatchUniverse(membership)
    frames = {
        "000001": _payload("000001", _frame_at(SIGNAL_DATE, close=11, ma20=10, ma60=10, change_pct=1, return_20d=0.02, return_60d=0.04)),
        "000002": _payload("000002", _frame_at(SIGNAL_DATE, close=12, ma20=10, ma60=10, change_pct=2, return_20d=0.04, return_60d=0.06)),
        "000003": _payload("000003", _frame_at(SIGNAL_DATE, close=13, ma20=10, ma60=10, change_pct=3, return_20d=0.06, return_60d=0.08)),
    }

    result = _historical_market_breadth(frames, start_date=SIGNAL_DATE, hold_days=1, universe_source=universe)

    # 每个 signal_date 只批量拉一次 membership，禁止逐股票 item_as_of N+1。
    assert universe.items_calls.count(SIGNAL_DATE) == 1
    assert universe.item_calls == 0
    # 非 _is_excluded_name 的成员才计入 eligible denominator。
    assert result[SIGNAL_DATE]["eligible_count"] == 3


def test_missing_denominator_counts_false_and_coverage_reflects_eligible():
    membership = {
        SIGNAL_DATE: [
            {"symbol": "000001", "name": "正常甲"},
            {"symbol": "000002", "name": "正常乙"},
            {"symbol": "000003", "name": "正常丙"},
            {"symbol": "000004", "name": "正常丁"},
            {"symbol": "000005", "name": "正常戊"},
        ]
    }
    universe = _BatchUniverse(membership)
    # 只给 3 个 eligible 成员价格行；000004/000005 当天缺行 → missing。
    frames = {
        "000001": _payload("000001", _frame_at(SIGNAL_DATE, close=11, ma20=10, ma60=10, change_pct=1, return_20d=0.02, return_60d=0.04)),
        "000002": _payload("000002", _frame_at(SIGNAL_DATE, close=9, ma20=10, ma60=10, change_pct=-1, return_20d=-0.02, return_60d=-0.04)),
        "000003": _payload("000003", _frame_at(SIGNAL_DATE, close=12, ma20=10, ma60=10, change_pct=2, return_20d=0.04, return_60d=0.06)),
        # 000004 / 000005 没有 frame。
    }

    result = _historical_market_breadth(frames, start_date=SIGNAL_DATE, hold_days=1, universe_source=universe)
    ctx = result[SIGNAL_DATE]

    assert ctx["eligible_count"] == 5
    assert ctx["observed_count"] == 3
    assert ctx["missing_count"] == 2
    assert ctx["coverage_pct"] == 60.0
    # 布尔宽度分母按 eligible 计：3 只中 2 只 above_ma20 → 2/5=40%，missing 按 False 计入。
    assert ctx["above_ma20_pct"] == 40.0
    # 收益分位数只用 observed 有效值（3 个）。
    assert ctx["observed_return_sample_count"] == 3
    assert ctx["median_return_20d_pct"] == 0.02 * 100  # median([2,-2,4]) = 2


def test_historical_st_member_excluded_from_eligible():
    membership = {SIGNAL_DATE: [{"symbol": "600001", "name": "ST历史名称"}]}
    universe = _BatchUniverse(membership)
    frames = {"600001": _payload("600001", _frame_at(SIGNAL_DATE, close=10, ma20=9, ma60=9, change_pct=0, return_20d=0.01, return_60d=0.01))}

    result = _historical_market_breadth(frames, start_date=SIGNAL_DATE, hold_days=1, universe_source=universe)

    # ST 名称被排除 → 无 eligible → 该日不产出 breadth（fail closed，不静默含入）。
    assert result == {}


def test_no_future_row_used_for_missing_member():
    # 000002 在 SIGNAL_DATE 是 eligible，但其 frame 最后一行落在更早的日期（无当天行）。
    earlier = "2024-03-13"
    membership = {
        SIGNAL_DATE: [
            {"symbol": "000001", "name": "正常甲"},
            {"symbol": "000002", "name": "正常乙"},
        ],
        # earlier 当天无 membership，避免 000002 的旧行被计入其他日期。
    }
    universe = _BatchUniverse(membership)
    frames = {
        "000001": _payload("000001", _frame_at(SIGNAL_DATE, close=11, ma20=10, ma60=10, change_pct=1, return_20d=0.02, return_60d=0.04)),
        "000002": _payload("000002", _frame_at(earlier, close=11, ma20=10, ma60=10, change_pct=1, return_20d=0.02, return_60d=0.04)),
    }

    result = _historical_market_breadth(frames, start_date="2024-03-13", hold_days=1, universe_source=universe)

    # 只产出 SIGNAL_DATE；000002 当天缺行计 missing，不借用 earlier 的行。
    assert list(result) == [SIGNAL_DATE]
    ctx = result[SIGNAL_DATE]
    assert ctx["eligible_count"] == 2
    assert ctx["observed_count"] == 1
    assert ctx["missing_count"] == 1


def test_return_percentiles_and_daily_return_accuracy():
    membership = {
        SIGNAL_DATE: [
            {"symbol": "000001", "name": "甲"},
            {"symbol": "000002", "name": "乙"},
            {"symbol": "000003", "name": "丙"},
        ]
    }
    universe = _BatchUniverse(membership)
    frames = {
        "000001": _payload("000001", _frame_at(SIGNAL_DATE, close=11, ma20=10, ma60=10, change_pct=1.0, return_20d=0.02, return_60d=0.04)),
        "000002": _payload("000002", _frame_at(SIGNAL_DATE, close=12, ma20=10, ma60=10, change_pct=2.0, return_20d=0.04, return_60d=0.06)),
        "000003": _payload("000003", _frame_at(SIGNAL_DATE, close=13, ma20=10, ma60=10, change_pct=3.0, return_20d=0.06, return_60d=0.08)),
    }

    result = _historical_market_breadth(frames, start_date=SIGNAL_DATE, hold_days=1, universe_source=universe)
    ctx = result[SIGNAL_DATE]

    assert ctx["eligible_count"] == 3
    assert ctx["observed_count"] == 3
    assert ctx["missing_count"] == 0
    assert ctx["coverage_pct"] == 100.0
    assert ctx["sample_count"] == ctx["eligible_count"]
    # return_20d: [2,4,6]
    assert ctx["median_return_20d_pct"] == 4.0
    assert ctx["p75_return_20d_pct"] == pytest.approx(5.0)
    # return_60d: [4,6,8]
    assert ctx["median_return_60d_pct"] == 6.0
    assert ctx["p75_return_60d_pct"] == pytest.approx(7.0)
    # daily change: [1,2,3]
    assert ctx["equal_weight_daily_return_pct"] == 2.0
    assert ctx["median_daily_return_pct"] == 2.0
    assert ctx["observed_return_sample_count"] == 3


def test_duplicate_symbol_date_fails_closed():
    frame = _frame_at(SIGNAL_DATE, close=11, ma20=10, ma60=10, change_pct=1, return_20d=0.02, return_60d=0.04)
    # 制造同一 symbol 在同一 signal_date 的重复行（数据损坏）。
    frame = pd.concat([frame, frame.iloc[[-1]]], ignore_index=True)
    frame["date"] = pd.date_range(end=SIGNAL_DATE, periods=len(frame)).strftime("%Y-%m-%d")
    # 最后两行落在不同日期，强行把倒数第二行日期改成与最后一行相同。
    frame.loc[frame.index[-2], "date"] = SIGNAL_DATE

    frames = {"000001": _payload("000001", frame)}
    with pytest.raises(ValueError):
        _historical_market_breadth(frames, start_date="2024-01-01", hold_days=1)


def test_no_universe_falls_back_to_eligible_equals_observed():
    frame = _frame_at(SIGNAL_DATE, close=11, ma20=10, ma60=10, change_pct=1, return_20d=0.02, return_60d=0.04)
    result = _historical_market_breadth(
        {"000001": _payload("000001", frame)},
        start_date=SIGNAL_DATE,
        hold_days=1,
    )
    ctx = result[SIGNAL_DATE]
    # 无 universe_source：eligible=observed、coverage=100，兼容旧路径。
    assert ctx["eligible_count"] == ctx["observed_count"] == 1
    assert ctx["missing_count"] == 0
    assert ctx["coverage_pct"] == 100.0
    assert ctx["sample_count"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("ma20", None),  # ma 缺失不得当 0 伪装成 above_ma20=强势
        ("ma60", float("nan")),  # ma60 NaN
        ("return_20d", None),  # 收益缺失
        ("return_60d", float("nan")),  # return_60d NaN
    ],
)
def test_batch_path_invalid_indicator_fails_closed(field, value):
    # 真实 batch membership 路径（universe 有 items_as_of）：index>=60 的 observed 行
    # 必须有有限正 close/ma20/ma60、有限 return_20d/return_60d；任一 None/NaN/非数字/ma<=0
    # 必须 ValueError fail closed，不得降低 observed_count 后静默继续。
    membership = {SIGNAL_DATE: [{"symbol": "000001", "name": "正常甲"}]}
    universe = _BatchUniverse(membership)
    kwargs = dict(close=11, ma20=10, ma60=10, change_pct=1, return_20d=0.02, return_60d=0.04)
    kwargs[field] = value
    frames = {"000001": _payload("000001", _frame_at(SIGNAL_DATE, **kwargs))}

    with pytest.raises(ValueError):
        _historical_market_breadth(
            frames, start_date=SIGNAL_DATE, hold_days=1, universe_source=universe
        )


def test_legacy_no_universe_path_tolerates_missing_return_60d():
    # 严格指标校验只在 items_as_of batch stock-only 路径开启；无 universe 的 legacy 路径
    # 保持兼容：旧 fixture 缺 return_60d 不得 fail（新研究合同边界）。
    frame = _frame_at(SIGNAL_DATE, close=11, ma20=10, ma60=10, change_pct=1, return_20d=0.02, return_60d=None)
    result = _historical_market_breadth(
        {"000001": _payload("000001", frame)},
        start_date=SIGNAL_DATE,
        hold_days=1,
    )
    ctx = result[SIGNAL_DATE]
    assert ctx["median_return_60d_pct"] is None
    assert ctx["observed_count"] == 1


# ---------------------------------------------------------------------------
# Part B: _stock_breadth_market_context —— 状态机与 exact 边界
# ---------------------------------------------------------------------------


def _ctx(**overrides):
    base = {
        "eligible_count": 1000,
        "observed_count": 1000,
        "missing_count": 0,
        "coverage_pct": 100.0,
        "above_ma20_pct": 50.0,
        "above_ma60_pct": 50.0,
        "return_20d_positive_pct": 50.0,
        "advancing_pct": 50.0,
        "median_return_20d_pct": 1.0,
        "median_return_60d_pct": 1.0,
    }
    base.update(overrides)
    return base


def test_constants_frozen():
    assert STOCK_MARKET_CONTEXT_SCHEMA_VERSION == "stock-breadth-market-context/v1"
    assert STOCK_MARKET_MIN_ELIGIBLE == 100
    assert STOCK_MARKET_MIN_COVERAGE_PCT == 95.0


def test_unknown_when_eligible_below_min():
    out = _stock_breadth_market_context(_ctx(eligible_count=99))
    assert out["level"] == "unknown"
    assert out["allow_buy"] is False
    assert out["min_signal_score"] == 999


def test_unknown_when_coverage_below_min():
    out = _stock_breadth_market_context(_ctx(coverage_pct=94.99))
    assert out["level"] == "unknown"
    assert out["allow_buy"] is False
    assert out["min_signal_score"] == 999


def test_unknown_when_required_field_missing():
    ctx = _ctx()
    del ctx["above_ma20_pct"]
    out = _stock_breadth_market_context(ctx)
    assert out["level"] == "unknown"
    assert out["allow_buy"] is False


@pytest.mark.parametrize("mode", ["none_value", "missing_key"])
def test_unknown_when_median_return_60d_missing(mode):
    # eligible/coverage/其余必需字段全合格，但 median_return_60d_pct 为 None 或缺键
    # → 必须 fail closed 到 unknown、allow_buy=False（不得伪装成强势状态）。
    ctx = _ctx()
    if mode == "none_value":
        ctx["median_return_60d_pct"] = None
    else:
        del ctx["median_return_60d_pct"]
    out = _stock_breadth_market_context(ctx)
    assert out["level"] == "unknown"
    assert out["allow_buy"] is False


def test_unknown_schema_version_present():
    out = _stock_breadth_market_context(_ctx(eligible_count=99))
    assert out["schema_version"] == STOCK_MARKET_CONTEXT_SCHEMA_VERSION
    assert isinstance(out["reasons"], list) and out["reasons"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"above_ma20_pct": 25.0},  # <=25 边界
        {"above_ma60_pct": 30.0, "median_return_60d_pct": -0.01},  # <=30 且 60d<0
        {"median_return_20d_pct": -8.0},  # <=-8 边界
        {"advancing_pct": 20.0, "median_return_20d_pct": -0.01},  # <=20 且 20d<0
    ],
)
def test_defensive_exact_boundaries(overrides):
    out = _stock_breadth_market_context(_ctx(**overrides))
    assert out["level"] == "defensive"
    assert out["min_signal_score"] == 4
    assert out["allow_buy"] is True
    assert out["allow_watch"] is False
    assert out["score_adjustment"] == -18


@pytest.mark.parametrize(
    "overrides",
    [
        {"above_ma20_pct": 25.0, "median_return_60d_pct": 1.0},  # 60d>=0 不触发该支，但 ma20<=25 仍 defensive
    ],
)
def test_defensive_above_ma60_branch_requires_negative_60d(overrides):
    # above_ma60_pct<=30 但 median_return_60d_pct>=0 → 该支不触发；ma20=25 仍 defensive。
    out = _stock_breadth_market_context(_ctx(**overrides))
    assert out["level"] == "defensive"


def test_cautious_exact_boundaries():
    # above_ma20_pct=44 (<45) → cautious；45 应落 neutral/favorable。
    assert _stock_breadth_market_context(_ctx(above_ma20_pct=44.0))["level"] == "cautious"
    # above_ma60_pct=44 → cautious
    assert _stock_breadth_market_context(_ctx(above_ma60_pct=44.0))["level"] == "cautious"
    # median_return_20d_pct=-0.01 (<0) → cautious；=0 不触发
    assert _stock_breadth_market_context(_ctx(median_return_20d_pct=-0.01, above_ma20_pct=65, above_ma60_pct=60, return_20d_positive_pct=60))["level"] == "cautious"
    # advancing_pct=39 (<40) → cautious
    assert _stock_breadth_market_context(_ctx(advancing_pct=39.0))["level"] == "cautious"


def test_cautious_output_fields():
    out = _stock_breadth_market_context(_ctx(above_ma20_pct=44.0))
    assert out["min_signal_score"] == 3
    assert out["allow_buy"] is True
    assert out["allow_watch"] is True
    assert out["score_adjustment"] == -8


def test_favorable_exact_boundary():
    out = _stock_breadth_market_context(
        _ctx(above_ma20_pct=65.0, above_ma60_pct=60.0, return_20d_positive_pct=60.0, median_return_20d_pct=3.0)
    )
    assert out["level"] == "favorable"
    assert out["min_signal_score"] == 2
    assert out["allow_buy"] is True
    assert out["allow_watch"] is True
    assert out["score_adjustment"] == 8


@pytest.mark.parametrize(
    "overrides",
    [
        {"above_ma20_pct": 64.0},  # <65
        {"above_ma60_pct": 59.0},  # <60
        {"return_20d_positive_pct": 59.0},  # <60
        {"median_return_20d_pct": 2.99},  # <3
    ],
)
def test_favorable_below_boundary_falls_to_neutral(overrides):
    # 在其余 favorable 条件满足的前提下，任一条件差一点 → 不构成 favorable。
    base = {"above_ma20_pct": 65.0, "above_ma60_pct": 60.0, "return_20d_positive_pct": 60.0, "median_return_20d_pct": 3.0}
    base.update(overrides)
    out = _stock_breadth_market_context(_ctx(**base))
    assert out["level"] == "neutral"


def test_neutral_output_fields():
    out = _stock_breadth_market_context(_ctx(above_ma20_pct=50.0, above_ma60_pct=50.0, median_return_20d_pct=1.0, advancing_pct=50.0))
    assert out["level"] == "neutral"
    assert out["min_signal_score"] == 2.5
    assert out["allow_buy"] is True
    assert out["allow_watch"] is True
    assert out["score_adjustment"] == 0


def test_defensive_takes_precedence_over_cautious():
    # 同时满足 defensive 与 cautious 条件时，先判 defensive。
    out = _stock_breadth_market_context(_ctx(above_ma20_pct=20.0))
    assert out["level"] == "defensive"


def test_breadth_context_output_has_no_proxy_or_etf_strings():
    out = _stock_breadth_market_context(_ctx())
    blob = json_blob(out).lower()
    assert "proxy" not in blob
    assert "etf" not in blob


# NaN/inf/非数字可绕过 unknown 并污染判定：必需字段非有限、eligible 非整数语义、
# coverage 越界都必须 fail closed 到 unknown、allow_buy=False，不得泄漏 TypeError、
# 不得落 neutral/favorable。
@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("above_ma20_pct", float("nan")),
        ("above_ma20_pct", float("inf")),
        ("above_ma20_pct", "bad"),
        ("above_ma60_pct", float("nan")),
        ("advancing_pct", float("inf")),
        ("median_return_20d_pct", float("-inf")),
        ("median_return_60d_pct", float("nan")),
        ("median_return_60d_pct", "bad"),
        ("return_20d_positive_pct", float("inf")),
    ],
)
def test_unknown_when_required_field_non_finite(field, bad_value):
    # NaN 在比较中恒为 False 会跳过 defensive 判定落 neutral；inf 会扭曲阈值；
    # 'bad' 字符串会触发 TypeError。三者都必须先于状态机 fail closed 到 unknown。
    ctx = _ctx()
    ctx[field] = bad_value
    out = _stock_breadth_market_context(ctx)
    assert out["level"] == "unknown"
    assert out["allow_buy"] is False
    assert out["min_signal_score"] == 999


@pytest.mark.parametrize(
    "overrides",
    [
        {"eligible_count": float("nan")},
        {"eligible_count": float("inf")},
        {"eligible_count": "bad"},
        {"eligible_count": 100.5},  # 非整数语义：eligible 必须可数
        {"eligible_count": -5},  # 负数
        {"coverage_pct": float("nan")},
        {"coverage_pct": float("inf")},
        {"coverage_pct": "bad"},
        {"coverage_pct": 101.0},  # 越界（>100）
        {"coverage_pct": -0.01},  # 越界（<0）
    ],
)
def test_unknown_when_eligible_or_coverage_invalid(overrides):
    out = _stock_breadth_market_context(_ctx(**overrides))
    assert out["level"] == "unknown"
    assert out["allow_buy"] is False


def test_breadth_state_output_json_serializable_strict():
    # 状态机输出（含 unknown/各状态）必须可被 json.dumps(allow_nan=False) 序列化，
    # 不得残留 NaN/inf。
    import json

    for ctx in [
        _ctx(),
        _ctx(eligible_count=99),
        _ctx(coverage_pct=94.99),
        _ctx(above_ma20_pct=20.0),
        _ctx(above_ma20_pct=44.0),
    ]:
        json.dumps(_stock_breadth_market_context(ctx), allow_nan=False)


# ---------------------------------------------------------------------------
# Part B: _stock_market_returns / _stock_relative_strength_context
# ---------------------------------------------------------------------------


def test_stock_market_returns_passthrough_and_none():
    ctx = _ctx(median_return_20d_pct=2.0, median_return_60d_pct=4.0)
    ctx["p75_return_20d_pct"] = 5.0
    ctx["p75_return_60d_pct"] = 7.0
    out = _stock_market_returns(ctx)
    assert out == {
        "market_median_return_20d_pct": 2.0,
        "market_median_return_60d_pct": 4.0,
        "market_p75_return_20d_pct": 5.0,
        "market_p75_return_60d_pct": 7.0,
    }
    # 未提供分位字段时透传 None（_ctx 默认不设 p75_*）
    ctx2 = _ctx()
    out2 = _stock_market_returns(ctx2)
    assert out2["market_p75_return_20d_pct"] is None
    assert out2["market_p75_return_60d_pct"] is None
    # 空 breadth 全部 None
    out3 = _stock_market_returns({})
    assert out3["market_median_return_20d_pct"] is None
    assert out3["market_median_return_60d_pct"] is None


# market 收益基准只透传有限数字：None/NaN/inf/'bad' 统一输出 None（保持字段名），
# 否则会把 NaN/inf 灌进下游 relative strength 污染阈值。
@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), float("-inf"), "bad"],
)
def test_stock_market_returns_non_finite_becomes_none(bad_value):
    ctx = _ctx()
    ctx["median_return_20d_pct"] = bad_value
    ctx["median_return_60d_pct"] = bad_value
    ctx["p75_return_20d_pct"] = bad_value
    ctx["p75_return_60d_pct"] = bad_value
    out = _stock_market_returns(ctx)
    assert out == {
        "market_median_return_20d_pct": None,
        "market_median_return_60d_pct": None,
        "market_p75_return_20d_pct": None,
        "market_p75_return_60d_pct": None,
    }


@pytest.mark.parametrize(
    "bad_value",
    [float("nan"), float("inf"), float("-inf"), "bad"],
)
def test_relative_strength_non_finite_market_returns_is_none_safe(bad_value):
    # caller 绕过 _stock_market_returns、直接把 NaN/inf/'bad' 塞进 market median/p75：
    # 不得泄漏 TypeError、不得输出 NaN/inf、不得产生对应 relative/leader tag。
    import json

    market_returns = {
        "market_median_return_20d_pct": bad_value,
        "market_median_return_60d_pct": bad_value,
        "market_p75_return_20d_pct": bad_value,
        "market_p75_return_60d_pct": bad_value,
    }
    latest_row = {"return_20d": 0.10, "return_60d": 0.20}
    out = _stock_relative_strength_context(latest_row, market_returns)

    assert out["relative_strength_20d_pct"] is None
    assert out["relative_strength_60d_pct"] is None
    assert out["market_median_return_20d_pct"] is None
    assert out["market_p75_return_20d_pct"] is None
    assert "stock_rs20_market_leader" not in out["tags"]
    assert "stock_rs60_market_leader" not in out["tags"]
    # 所有数值字段有限或 None → allow_nan=False 可序列化
    json.dumps(out, allow_nan=False)


def test_relative_strength_output_json_serializable_strict():
    import json

    ctx = _ctx(median_return_20d_pct=2.0, median_return_60d_pct=4.0)
    ctx["p75_return_20d_pct"] = 5.0
    ctx["p75_return_60d_pct"] = 7.0
    market_returns = _stock_market_returns(ctx)
    latest_row = {"return_20d": 0.10, "return_60d": 0.20}
    json.dumps(_stock_relative_strength_context(latest_row, market_returns), allow_nan=False)


def test_relative_strength_and_leader_tags():
    ctx = _ctx(median_return_20d_pct=2.0, median_return_60d_pct=4.0)
    ctx["p75_return_20d_pct"] = 5.0
    ctx["p75_return_60d_pct"] = 7.0
    market_returns = _stock_market_returns(ctx)
    latest_row = {"return_20d": 0.10, "return_60d": 0.20}  # stock 20d=10, 60d=20

    out = _stock_relative_strength_context(latest_row, market_returns)

    assert out["stock_return_20d_pct"] == 10.0
    assert out["stock_return_60d_pct"] == 20.0
    assert out["relative_strength_20d_pct"] == 8.0  # 10 - median 2
    assert out["relative_strength_60d_pct"] == 16.0  # 20 - median 4
    # leader: stock >= p75 + 5(20d)=10 / +10(60d)=17
    assert "stock_rs20_market_leader" in out["tags"]
    assert "stock_rs60_market_leader" in out["tags"]
    assert "stock_rs20_strong" in out["tags"]


def test_relative_strength_leader_boundary_not_triggered():
    ctx = _ctx(median_return_20d_pct=2.0, median_return_60d_pct=4.0)
    ctx["p75_return_20d_pct"] = 5.0
    ctx["p75_return_60d_pct"] = 7.0
    market_returns = _stock_market_returns(ctx)
    # stock_20d=9.99 < p75+5=10 → 非 leader；stock_60d=16.99 < 17 → 非 leader
    latest_row = {"return_20d": 0.0999, "return_60d": 0.1699}

    out = _stock_relative_strength_context(latest_row, market_returns)

    assert "stock_rs20_market_leader" not in out["tags"]
    assert "stock_rs60_market_leader" not in out["tags"]


def test_relative_strength_missing_market_returns_yields_none():
    latest_row = {"return_20d": 0.10, "return_60d": 0.20}
    market_returns = {
        "market_median_return_20d_pct": None,
        "market_median_return_60d_pct": None,
        "market_p75_return_20d_pct": None,
        "market_p75_return_60d_pct": None,
    }
    out = _stock_relative_strength_context(latest_row, market_returns)
    assert out["relative_strength_20d_pct"] is None
    assert out["relative_strength_60d_pct"] is None
    assert "stock_rs20_market_leader" not in out["tags"]


@pytest.mark.parametrize(
    "missing_value",
    [None, float("nan"), float("inf"), float("-inf"), "not_a_number"],
)
def test_relative_strength_missing_stock_20d_is_none_safe(missing_value):
    # stock 的 return_20d 缺失/NaN/非数字 → 该 horizon 的 stock_return / relative_strength
    # 都为 None，且不产生任何 stock_rs20 tag；return_60d 有值仍可独立计算。
    # 不得 _num(None)->0 把缺失伪装成 0/强势。
    ctx = _ctx(median_return_20d_pct=2.0, median_return_60d_pct=4.0)
    ctx["p75_return_20d_pct"] = 5.0
    ctx["p75_return_60d_pct"] = 7.0
    market_returns = _stock_market_returns(ctx)
    latest_row = {"return_20d": missing_value, "return_60d": 0.20}

    out = _stock_relative_strength_context(latest_row, market_returns)

    assert out["stock_return_20d_pct"] is None
    assert out["relative_strength_20d_pct"] is None
    assert not [t for t in out["tags"] if t.startswith("stock_rs20")]
    # 60d horizon 仍独立计算
    assert out["stock_return_60d_pct"] == 20.0
    assert out["relative_strength_60d_pct"] == 16.0


def test_relative_strength_missing_stock_60d_keeps_20d():
    # 反向：return_60d 缺失 → 60d horizon 全 None 且无 stock_rs60 tag；20d 仍独立计算。
    ctx = _ctx(median_return_20d_pct=2.0, median_return_60d_pct=4.0)
    ctx["p75_return_20d_pct"] = 5.0
    ctx["p75_return_60d_pct"] = 7.0
    market_returns = _stock_market_returns(ctx)
    latest_row = {"return_20d": 0.10, "return_60d": None}

    out = _stock_relative_strength_context(latest_row, market_returns)

    assert out["stock_return_60d_pct"] is None
    assert out["relative_strength_60d_pct"] is None
    assert not [t for t in out["tags"] if t.startswith("stock_rs60")]
    assert out["stock_return_20d_pct"] == 10.0
    assert out["relative_strength_20d_pct"] == 8.0


def test_relative_strength_input_unchanged_and_deterministic():
    ctx = _ctx(median_return_20d_pct=2.0, median_return_60d_pct=4.0)
    ctx["p75_return_20d_pct"] = 5.0
    ctx["p75_return_60d_pct"] = 7.0
    market_returns = _stock_market_returns(ctx)
    latest_row = {"return_20d": 0.10, "return_60d": 0.20}
    snapshot = copy.deepcopy(latest_row)

    out1 = _stock_relative_strength_context(latest_row, market_returns)
    out2 = _stock_relative_strength_context(latest_row, market_returns)

    assert latest_row == snapshot  # 输入不变
    assert out1 == out2  # deterministic
    blob = json_blob(out1).lower()
    assert "proxy" not in blob
    assert "etf" not in blob


def json_blob(obj):
    import json

    def _default(value):
        if isinstance(value, (np.floating, np.integer)):
            return value.item()
        raise TypeError

    return json.dumps(obj, default=_default, sort_keys=True)
