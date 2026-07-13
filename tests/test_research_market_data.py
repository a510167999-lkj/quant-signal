import pytest

from app.research_market_data import (
    MarketEvidenceError,
    build_market_session_specs,
    causal_adjusted_bars,
    next_open_fill_gate,
)


def test_market_session_specs_freeze_raw_and_tradability_contracts():
    specs = build_market_session_specs("2024-01-02")

    assert [spec.dataset for spec in specs] == [
        "daily",
        "adj_factor",
        "stk_limit",
        "suspend_d",
    ]
    assert all(spec.partition_key == "2024-01-02" for spec in specs)
    assert all(spec.wire_params == {"trade_date": "20240102"} for spec in specs)
    assert specs[0].fields == (
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
    )
    assert specs[0].row_cap == 6000
    assert specs[0].price_basis == "raw_unadjusted_execution"
    assert specs[1].fields == ("ts_code", "trade_date", "adj_factor")
    assert specs[1].price_basis == "causal_signal_input_only"
    assert specs[2].fields == (
        "trade_date",
        "ts_code",
        "pre_close",
        "up_limit",
        "down_limit",
    )
    assert specs[2].row_cap == 5800
    assert specs[3].fields == (
        "ts_code",
        "trade_date",
        "suspend_timing",
        "suspend_type",
    )


def test_causal_adjustment_ignores_future_factors_and_preserves_raw_execution_prices():
    raw = [
        {
            "ts_code": "600001.SH",
            "trade_date": "2024-01-02",
            "open": 9.8,
            "high": 10.2,
            "low": 9.7,
            "close": 10.0,
        },
        {
            "ts_code": "600001.SH",
            "trade_date": "2024-01-03",
            "open": 5.0,
            "high": 5.2,
            "low": 4.9,
            "close": 5.1,
        },
    ]
    factors = [
        {"ts_code": "600001.SH", "trade_date": "2024-01-02", "adj_factor": 1.0},
        {"ts_code": "600001.SH", "trade_date": "2024-01-03", "adj_factor": 2.0},
        {"ts_code": "600001.SH", "trade_date": "2024-02-01", "adj_factor": 99.0},
    ]

    adjusted = causal_adjusted_bars(raw, factors, as_of_date="2024-01-03")
    changed_future = [*factors[:-1], {**factors[-1], "adj_factor": 999999.0}]
    adjusted_after_future_revision = causal_adjusted_bars(
        raw, changed_future, as_of_date="2024-01-03"
    )

    assert adjusted == adjusted_after_future_revision
    assert adjusted[0]["signal_close"] == 5.0
    assert adjusted[1]["signal_close"] == 5.1
    assert adjusted[0]["raw_open"] == 9.8
    assert adjusted[1]["raw_open"] == 5.0


def test_causal_adjustment_uses_latest_observed_factor_before_nontrading_as_of():
    raw = [
        {
            "ts_code": "000038.SZ",
            "trade_date": "2023-07-10",
            "open": 0.5,
            "high": 0.52,
            "low": 0.49,
            "close": 0.51,
        },
        {
            "ts_code": "000038.SZ",
            "trade_date": "2023-07-11",
            "open": 0.48,
            "high": 0.5,
            "low": 0.47,
            "close": 0.49,
        },
    ]
    factors = [
        {"ts_code": "000038.SZ", "trade_date": "2023-07-10", "adj_factor": 2.924},
        {"ts_code": "000038.SZ", "trade_date": "2023-07-11", "adj_factor": 2.924},
        {"ts_code": "000038.SZ", "trade_date": "2024-01-02", "adj_factor": 99.0},
    ]

    adjusted = causal_adjusted_bars(raw, factors, as_of_date="2023-12-29")

    assert [row["as_of_adj_factor"] for row in adjusted] == [2.924, 2.924]
    assert [row["adjustment_as_of_date"] for row in adjusted] == [
        "2023-07-11",
        "2023-07-11",
    ]
    assert [row["signal_close"] for row in adjusted] == pytest.approx([0.51, 0.49])


@pytest.mark.parametrize(
    "factors, message",
    [
        (
            [
                {
                    "ts_code": "600001.SH",
                    "trade_date": "2024-01-02",
                    "adj_factor": 1.0,
                },
                {
                    "ts_code": "600001.SH",
                    "trade_date": "2024-01-02",
                    "adj_factor": 1.1,
                },
            ],
            "duplicate",
        ),
        ([], "missing"),
        (
            [
                {
                    "ts_code": "600001.SH",
                    "trade_date": "2024-01-02",
                    "adj_factor": 0,
                }
            ],
            "positive",
        ),
    ],
)
def test_causal_adjustment_fails_closed_on_invalid_factor_lineage(factors, message):
    raw = [
        {
            "ts_code": "600001.SH",
            "trade_date": "2024-01-02",
            "open": 10,
            "high": 10,
            "low": 10,
            "close": 10,
        }
    ]
    with pytest.raises(MarketEvidenceError, match=message):
        causal_adjusted_bars(raw, factors, as_of_date="2024-01-02")


@pytest.mark.parametrize(
    "side, bar, limits, suspension, expected_reason",
    [
        ("buy", None, {"up_limit": 11, "down_limit": 9}, None, "missing_raw_bar"),
        (
            "buy",
            {"open": 10, "high": 10, "low": 10, "close": 10},
            None,
            None,
            "missing_price_limit",
        ),
        (
            "buy",
            {"open": 10, "high": 10, "low": 10, "close": 10},
            {"up_limit": 11, "down_limit": 9},
            {"suspend_type": "S", "suspend_timing": None},
            "suspended",
        ),
        (
            "buy",
            {"open": 11, "high": 11, "low": 11, "close": 11},
            {"up_limit": 11, "down_limit": 9},
            None,
            "buy_open_locked_limit",
        ),
        (
            "sell",
            {"open": 9, "high": 9, "low": 9, "close": 9},
            {"up_limit": 11, "down_limit": 9},
            None,
            "sell_open_locked_limit",
        ),
    ],
)
def test_next_open_fill_gate_rejects_unproven_or_untradable_days(
    side, bar, limits, suspension, expected_reason
):
    result = next_open_fill_gate(
        side=side,
        raw_bar=bar,
        price_limit=limits,
        suspension=suspension,
    )

    assert result == {"fillable": False, "reason": expected_reason, "raw_price": None}


def test_next_open_fill_gate_returns_only_raw_open_for_proven_normal_day():
    result = next_open_fill_gate(
        side="buy",
        raw_bar={"open": 10.0, "high": 10.5, "low": 9.8, "close": 10.2},
        price_limit={"up_limit": 11.0, "down_limit": 9.0},
        suspension=None,
    )

    assert result == {"fillable": True, "reason": "raw_open", "raw_price": 10.0}
