from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
import math

import pytest

from app.factor_v2_execution_stress_primitives import (
    CashFeeSchedule,
    MinuteAmount,
    PlannedLeg,
    SelectedWindowLiquidity,
    SlippageScenario,
    aggregate_planned_legs,
    capacity_required_passes,
    compute_trade_cash_cost_scenarios,
    evaluate_capacity_scenarios,
    select_half_open_one_minute_window,
    size_buy_at_c_and_two_c,
    size_sell_all,
)


SHANGHAI_OFFSET = timezone(timedelta(hours=8))


def _fee_schedule(**overrides: float) -> CashFeeSchedule:
    values = {
        "buy_commission_bps": 3.0,
        "sell_commission_bps": 4.0,
        "minimum_commission_cny": 5.0,
        "sell_stamp_duty_bps": 5.0,
        "other_buy_fees_bps": 1.0,
        "other_sell_fees_bps": 2.0,
        "explicit_fee_floor_bps_on_entry_notional": 25.0,
        "base_one_way_slippage_bps": 10.0,
    }
    values.update(overrides)
    return CashFeeSchedule(**values)


def _slippage_grid() -> list[SlippageScenario]:
    return [
        SlippageScenario("additional_20bps", 20.0),
        SlippageScenario("additional_0bps", 0.0),
        SlippageScenario("additional_10bps", 10.0),
        SlippageScenario("additional_5bps", 5.0),
    ]


def _minute(
    minute: int,
    *,
    amount: float = 20_000.0,
    phase: str = "continuous_auction",
    unit: str = "CNY",
    security: str = "security-600001",
    session: date = date(2026, 7, 1),
) -> MinuteAmount:
    start = datetime.combine(session, time(9, minute), SHANGHAI_OFFSET)
    return MinuteAmount(
        stable_security_id=security,
        execution_session=session,
        interval_start_at=start,
        interval_end_at=start + timedelta(minutes=1),
        market_phase=phase,
        traded_amount=amount,
        amount_unit=unit,
    )


def _window_rows() -> list[MinuteAmount]:
    return [_minute(minute) for minute in range(29, 37)]


def _planned_legs() -> list[PlannedLeg]:
    return [
        PlannedLeg(
            trade_key="trade-b",
            order_key="order-buy-2",
            stable_security_id="security-600001",
            side="buy",
            execution_session=date(2026, 7, 1),
            execution_window="entry",
            planned_order_notional_at_c_cny=1_500.0,
            planned_order_notional_at_two_c_cny=3_000.0,
        ),
        PlannedLeg(
            trade_key="trade-a",
            order_key="order-sell-1",
            stable_security_id="security-600001",
            side="sell",
            execution_session=date(2026, 7, 8),
            execution_window="exit",
            planned_order_notional_at_c_cny=2_500.0,
            planned_order_notional_at_two_c_cny=5_000.0,
        ),
        PlannedLeg(
            trade_key="trade-a",
            order_key="order-buy-1",
            stable_security_id="security-600001",
            side="buy",
            execution_session=date(2026, 7, 1),
            execution_window="entry",
            planned_order_notional_at_c_cny=2_500.0,
            planned_order_notional_at_two_c_cny=5_000.0,
        ),
        PlannedLeg(
            trade_key="trade-b",
            order_key="order-sell-2",
            stable_security_id="security-600001",
            side="sell",
            execution_session=date(2026, 7, 8),
            execution_window="exit",
            planned_order_notional_at_c_cny=1_500.0,
            planned_order_notional_at_two_c_cny=3_000.0,
        ),
    ]


def _liquidity() -> list[SelectedWindowLiquidity]:
    return [
        SelectedWindowLiquidity(
            stable_security_id="security-600001",
            side="sell",
            execution_session=date(2026, 7, 8),
            execution_window="exit",
            traded_amount=100_000.0,
            amount_unit="CNY",
            selected_minutes=(),
        ),
        SelectedWindowLiquidity(
            stable_security_id="security-600001",
            side="buy",
            execution_session=date(2026, 7, 1),
            execution_window="entry",
            traded_amount=100_000.0,
            amount_unit="CNY",
            selected_minutes=(),
        ),
    ]


def test_cash_costs_apply_each_leg_minimum_tax_other_fees_and_slippage() -> None:
    scenarios = compute_trade_cash_cost_scenarios(
        entry_notional_cny=1_000.0,
        gross_exit_leg_proceeds_cny=[400.0, 700.0],
        fee_schedule=_fee_schedule(),
        slippage_scenarios=[SlippageScenario("additional_5bps", 5.0)],
    )

    scenario = scenarios[0]
    assert [leg.side for leg in scenario.legs] == ["buy", "sell", "sell"]
    assert [leg.commission_cash_cny for leg in scenario.legs] == pytest.approx([5.0, 5.0, 5.0])
    assert [leg.stamp_duty_cash_cny for leg in scenario.legs] == pytest.approx([0.0, 0.20, 0.35])
    assert [leg.other_fee_cash_cny for leg in scenario.legs] == pytest.approx([0.10, 0.08, 0.14])
    assert scenario.actual_explicit_fee_cash_cny == pytest.approx(15.87)
    assert scenario.explicit_fee_floor_cash_cny == pytest.approx(2.50)
    assert scenario.charged_explicit_fee_cash_cny == pytest.approx(15.87)
    assert scenario.base_slippage_cash_cny == pytest.approx(2.10)
    assert scenario.additional_slippage_cash_cny == pytest.approx(1.05)
    assert scenario.total_cash_cost_cny == pytest.approx(19.02)


def test_cash_costs_keep_explicit_fee_floor_separate_from_actual_leg_fees() -> None:
    scenarios = compute_trade_cash_cost_scenarios(
        entry_notional_cny=100_000.0,
        gross_exit_leg_proceeds_cny=[110_000.0],
        fee_schedule=_fee_schedule(
            buy_commission_bps=0.0,
            sell_commission_bps=0.0,
            minimum_commission_cny=0.0,
            sell_stamp_duty_bps=0.0,
            other_buy_fees_bps=0.0,
            other_sell_fees_bps=0.0,
            base_one_way_slippage_bps=0.0,
        ),
        slippage_scenarios=[SlippageScenario("additional_0bps", 0.0)],
    )

    scenario = scenarios[0]
    assert scenario.actual_explicit_fee_cash_cny == 0.0
    assert scenario.explicit_fee_floor_cash_cny == 250.0
    assert scenario.charged_explicit_fee_cash_cny == 250.0
    assert scenario.total_cash_cost_cny == 250.0


def test_cash_cost_scenarios_are_explicit_unique_and_order_stable() -> None:
    first = compute_trade_cash_cost_scenarios(
        entry_notional_cny=2_000.0,
        gross_exit_leg_proceeds_cny=[2_200.0],
        fee_schedule=_fee_schedule(base_one_way_slippage_bps=8.0),
        slippage_scenarios=_slippage_grid(),
    )
    second = compute_trade_cash_cost_scenarios(
        entry_notional_cny=2_000.0,
        gross_exit_leg_proceeds_cny=[2_200.0],
        fee_schedule=_fee_schedule(base_one_way_slippage_bps=8.0),
        slippage_scenarios=list(reversed(_slippage_grid())),
    )

    assert first == second
    assert [item.additional_one_way_slippage_bps for item in first] == [
        0.0,
        5.0,
        10.0,
        20.0,
    ]
    assert first[0].base_one_way_slippage_bps == 8.0
    assert first[-1].additional_slippage_cash_cny == pytest.approx(8.4)

    with pytest.raises(ValueError, match="duplicate"):
        compute_trade_cash_cost_scenarios(
            entry_notional_cny=2_000.0,
            gross_exit_leg_proceeds_cny=[2_200.0],
            fee_schedule=_fee_schedule(),
            slippage_scenarios=[
                SlippageScenario("same", 0.0),
                SlippageScenario("same", 5.0),
            ],
        )


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("buy_commission_bps", -1.0),
        ("minimum_commission_cny", math.nan),
        ("sell_stamp_duty_bps", math.inf),
        ("base_one_way_slippage_bps", -0.1),
    ],
)
def test_cash_costs_reject_invalid_explicit_schedule_values(
    field: str,
    invalid: float,
) -> None:
    with pytest.raises(ValueError, match=field):
        compute_trade_cash_cost_scenarios(
            entry_notional_cny=1_000.0,
            gross_exit_leg_proceeds_cny=[1_100.0],
            fee_schedule=_fee_schedule(**{field: invalid}),
            slippage_scenarios=[SlippageScenario("base", 0.0)],
        )


def test_buy_sizing_recomputes_c_and_two_c_before_100_share_rounding() -> None:
    sizing = size_buy_at_c_and_two_c(
        initial_available_buy_notional_cny=995.0,
        initial_capital_cny=100_000.0,
        capital_c_cny=200_000.0,
        raw_entry_price_cny_per_share=10.0,
        board_lot_shares=100,
    )

    assert sizing.at_c.capital_multiplier_of_c == 1.0
    assert sizing.at_c.scaled_available_buy_notional_cny == 1_990.0
    assert sizing.at_c.planned_shares == 100
    assert sizing.at_two_c.capital_multiplier_of_c == 2.0
    assert sizing.at_two_c.scaled_available_buy_notional_cny == 3_980.0
    assert sizing.at_two_c.planned_shares == 300
    assert sizing.at_two_c.planned_shares != 2 * sizing.at_c.planned_shares


def test_buy_sizing_keeps_zero_lot_as_zero_and_requires_explicit_100_share_lot() -> None:
    sizing = size_buy_at_c_and_two_c(
        initial_available_buy_notional_cny=249.0,
        initial_capital_cny=100_000.0,
        capital_c_cny=200_000.0,
        raw_entry_price_cny_per_share=10.0,
        board_lot_shares=100,
    )

    assert sizing.at_c.planned_shares == 0
    assert sizing.at_c.planned_order_notional_cny == 0.0
    assert sizing.at_two_c.planned_shares == 0

    with pytest.raises(ValueError, match="100"):
        size_buy_at_c_and_two_c(
            initial_available_buy_notional_cny=1_000.0,
            initial_capital_cny=100_000.0,
            capital_c_cny=200_000.0,
            raw_entry_price_cny_per_share=10.0,
            board_lot_shares=200,
        )


def test_sell_sizing_sells_every_available_share_including_odd_lots_and_zero() -> None:
    odd_lot = size_sell_all(
        available_shares=357,
        raw_exit_price_cny_per_share=10.25,
    )
    zero = size_sell_all(
        available_shares=0,
        raw_exit_price_cny_per_share=10.25,
    )

    assert odd_lot.planned_shares == 357
    assert odd_lot.planned_order_notional_cny == pytest.approx(3_659.25)
    assert zero.planned_shares == 0
    assert zero.planned_order_notional_cny == 0.0


def test_planned_leg_aggregation_is_trade_key_and_order_stable() -> None:
    first = aggregate_planned_legs(_planned_legs())
    second = aggregate_planned_legs(list(reversed(_planned_legs())))

    assert first == second
    assert [(row.side, row.execution_window) for row in first] == [
        ("buy", "entry"),
        ("sell", "exit"),
    ]
    buy = first[0]
    assert buy.trade_and_order_keys == (
        ("trade-a", "order-buy-1"),
        ("trade-b", "order-buy-2"),
    )
    assert buy.planned_order_notional_at_c_cny == 4_000.0
    assert buy.planned_order_notional_at_two_c_cny == 8_000.0


def test_planned_leg_aggregation_rejects_ambiguous_identity_or_side_window() -> None:
    duplicate = _planned_legs()
    duplicate[1] = PlannedLeg(
        **{
            **asdict(duplicate[1]),
            "order_key": duplicate[0].order_key,
        }
    )
    with pytest.raises(ValueError, match="duplicate order_key"):
        aggregate_planned_legs(duplicate)

    invalid = _planned_legs()
    invalid[0] = PlannedLeg(
        **{
            **asdict(invalid[0]),
            "execution_window": "exit",
        }
    )
    with pytest.raises(ValueError, match="side.*window"):
        aggregate_planned_legs(invalid)


def test_each_trade_key_requires_at_least_one_buy_and_one_sell_leg() -> None:
    incomplete = [
        leg for leg in _planned_legs() if not (leg.trade_key == "trade-b" and leg.side == "sell")
    ]

    with pytest.raises(ValueError, match="trade_key.*buy.*sell"):
        aggregate_planned_legs(incomplete)


def test_capacity_revalidates_trade_completeness_and_order_identity() -> None:
    planned = list(aggregate_planned_legs(_planned_legs()))
    sell = planned[1]
    planned[1] = type(sell)(
        **{
            **asdict(sell),
            "trade_and_order_keys": tuple(
                pair for pair in sell.trade_and_order_keys if pair[0] != "trade-b"
            ),
        }
    )
    with pytest.raises(ValueError, match="trade_key.*buy.*sell"):
        evaluate_capacity_scenarios(
            planned_legs=planned,
            window_liquidity=_liquidity(),
            base_participation_rate=0.10,
        )

    planned = list(aggregate_planned_legs(_planned_legs()))
    buy = planned[0]
    planned[0] = type(buy)(
        **{
            **asdict(buy),
            "trade_and_order_keys": (
                ("trade-a", "duplicate-order"),
                ("trade-b", "duplicate-order"),
            ),
        }
    )
    with pytest.raises(ValueError, match="duplicate order_key"):
        evaluate_capacity_scenarios(
            planned_legs=planned,
            window_liquidity=_liquidity(),
            base_participation_rate=0.10,
        )


def test_minute_selector_uses_asia_shanghai_half_open_window_and_end_boundary() -> None:
    selected = select_half_open_one_minute_window(
        rows=_window_rows(),
        stable_security_id="security-600001",
        side="buy",
        execution_session=date(2026, 7, 1),
        execution_window="entry",
        window_start_time=time(9, 30),
        window_end_time=time(9, 35),
        timezone_name="Asia/Shanghai",
        include_call_auction=False,
        amount_unit="CNY",
    )

    assert [row.interval_start_at.minute for row in selected.selected_minutes] == [
        30,
        31,
        32,
        33,
        34,
    ]
    assert selected.traded_amount == 100_000.0
    assert selected.amount_unit == "CNY"


def test_minute_selector_requires_complete_observed_minutes_instead_of_guessing_zero() -> None:
    rows = [row for row in _window_rows() if row.interval_start_at.minute != 32]

    with pytest.raises(ValueError, match="missing observed one-minute interval"):
        select_half_open_one_minute_window(
            rows=rows,
            stable_security_id="security-600001",
            side="buy",
            execution_session=date(2026, 7, 1),
            execution_window="entry",
            window_start_time=time(9, 30),
            window_end_time=time(9, 35),
            timezone_name="Asia/Shanghai",
            include_call_auction=False,
            amount_unit="CNY",
        )


def test_minute_selector_keeps_explicit_zero_observation_but_does_not_invent_rows() -> None:
    rows = _window_rows()
    rows[3] = _minute(32, amount=0.0)

    selected = select_half_open_one_minute_window(
        rows=rows,
        stable_security_id="security-600001",
        side="buy",
        execution_session=date(2026, 7, 1),
        execution_window="entry",
        window_start_time=time(9, 30),
        window_end_time=time(9, 35),
        timezone_name="Asia/Shanghai",
        include_call_auction=False,
        amount_unit="CNY",
    )

    assert len(selected.selected_minutes) == 5
    assert selected.traded_amount == 80_000.0


def test_minute_selector_excludes_call_auction_only_after_coverage_check() -> None:
    rows = _window_rows()
    rows[1] = _minute(30, phase="opening_call_auction")

    selected = select_half_open_one_minute_window(
        rows=rows,
        stable_security_id="security-600001",
        side="buy",
        execution_session=date(2026, 7, 1),
        execution_window="entry",
        window_start_time=time(9, 30),
        window_end_time=time(9, 35),
        timezone_name="Asia/Shanghai",
        include_call_auction=False,
        amount_unit="CNY",
    )

    assert [row.interval_start_at.minute for row in selected.selected_minutes] == [
        31,
        32,
        33,
        34,
    ]
    assert selected.traded_amount == 80_000.0


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (
            lambda rows: rows.__setitem__(2, _minute(31, unit="SHARES")),
            "amount_unit",
        ),
        (
            lambda rows: rows.__setitem__(
                2,
                MinuteAmount(
                    **{
                        **asdict(rows[2]),
                        "interval_start_at": datetime(2026, 7, 1, 9, 31),
                        "interval_end_at": datetime(2026, 7, 1, 9, 32),
                    }
                ),
            ),
            "UTC offset",
        ),
        (
            lambda rows: rows.__setitem__(
                2,
                MinuteAmount(
                    **{
                        **asdict(rows[2]),
                        "interval_end_at": rows[2].interval_end_at + timedelta(minutes=1),
                    }
                ),
            ),
            "one-minute",
        ),
    ],
)
def test_minute_selector_rejects_unit_timezone_or_interval_ambiguity(
    mutator,
    message: str,
) -> None:
    rows = _window_rows()
    mutator(rows)

    with pytest.raises(ValueError, match=message):
        select_half_open_one_minute_window(
            rows=rows,
            stable_security_id="security-600001",
            side="buy",
            execution_session=date(2026, 7, 1),
            execution_window="entry",
            window_start_time=time(9, 30),
            window_end_time=time(9, 35),
            timezone_name="Asia/Shanghai",
            include_call_auction=False,
            amount_unit="CNY",
        )


def test_capacity_computes_four_stable_10_percent_scenarios() -> None:
    planned = aggregate_planned_legs(_planned_legs())

    result = evaluate_capacity_scenarios(
        planned_legs=planned,
        window_liquidity=list(reversed(_liquidity())),
        base_participation_rate=0.10,
    )

    assert [scenario.scenario_id for scenario in result.scenarios] == [
        "C_and_p0",
        "C_and_p0_over_2",
        "2C_and_p0",
        "2C_and_p0_over_2",
    ]
    assert [scenario.maximum_participation_rate for scenario in result.scenarios] == [
        0.10,
        0.05,
        0.10,
        0.05,
    ]
    assert [scenario.passes for scenario in result.scenarios] == [
        True,
        True,
        True,
        False,
    ]
    assert result.required_passes is True
    buy_at_two_c = result.scenarios[2].leg_evaluations[0]
    assert buy_at_two_c.planned_order_notional_cny == 8_000.0
    assert buy_at_two_c.participation_rate == pytest.approx(0.08)


def test_capacity_aggregates_same_key_before_participation_math() -> None:
    result = evaluate_capacity_scenarios(
        planned_legs=aggregate_planned_legs(_planned_legs()),
        window_liquidity=_liquidity(),
        base_participation_rate=0.10,
    )

    buy = result.scenarios[0].leg_evaluations[0]
    assert buy.trade_and_order_keys == (
        ("trade-a", "order-buy-1"),
        ("trade-b", "order-buy-2"),
    )
    assert buy.planned_order_notional_cny == 4_000.0
    assert buy.participation_rate == pytest.approx(0.04)


def test_capacity_requires_exact_liquidity_keys_and_explicit_cny_unit() -> None:
    planned = aggregate_planned_legs(_planned_legs())

    with pytest.raises(ValueError, match="missing liquidity"):
        evaluate_capacity_scenarios(
            planned_legs=planned,
            window_liquidity=_liquidity()[:1],
            base_participation_rate=0.10,
        )

    extra = _liquidity() + [
        SelectedWindowLiquidity(
            stable_security_id="security-600002",
            side="buy",
            execution_session=date(2026, 7, 1),
            execution_window="entry",
            traded_amount=100_000.0,
            amount_unit="CNY",
            selected_minutes=(),
        )
    ]
    with pytest.raises(ValueError, match="extra liquidity"):
        evaluate_capacity_scenarios(
            planned_legs=planned,
            window_liquidity=extra,
            base_participation_rate=0.10,
        )

    wrong_unit = _liquidity()
    wrong_unit[0] = SelectedWindowLiquidity(
        **{
            **asdict(wrong_unit[0]),
            "amount_unit": "SHARES",
        }
    )
    with pytest.raises(ValueError, match="CNY"):
        evaluate_capacity_scenarios(
            planned_legs=planned,
            window_liquidity=wrong_unit,
            base_participation_rate=0.10,
        )


def test_capacity_explicit_zero_denominator_is_not_treated_as_missing_or_passing() -> None:
    liquidity = _liquidity()
    liquidity[1] = SelectedWindowLiquidity(
        **{
            **asdict(liquidity[1]),
            "traded_amount": 0.0,
        }
    )

    result = evaluate_capacity_scenarios(
        planned_legs=aggregate_planned_legs(_planned_legs()),
        window_liquidity=liquidity,
        base_participation_rate=0.10,
    )

    for scenario in result.scenarios:
        buy = scenario.leg_evaluations[0]
        assert buy.traded_amount_cny == 0.0
        assert buy.participation_rate is None
        assert buy.passes is False
        assert scenario.passes is False
    assert result.required_passes is False


@pytest.mark.parametrize("invalid", [10.0, 0.20, 0.0, -0.1, math.nan])
def test_capacity_requires_explicit_fractional_frozen_p0(invalid: float) -> None:
    with pytest.raises(ValueError, match="0.10"):
        evaluate_capacity_scenarios(
            planned_legs=aggregate_planned_legs(_planned_legs()),
            window_liquidity=_liquidity(),
            base_participation_rate=invalid,
        )


@pytest.mark.parametrize(
    ("base", "half_p", "double_c", "expected"),
    [
        (True, True, False, True),
        (True, False, True, True),
        (True, False, False, False),
        (False, True, True, False),
    ],
)
def test_capacity_required_formula_is_base_and_half_p_or_double_c(
    base: bool,
    half_p: bool,
    double_c: bool,
    expected: bool,
) -> None:
    assert (
        capacity_required_passes(
            c_and_p0_passes=base,
            c_and_p0_over_2_passes=half_p,
            two_c_and_p0_passes=double_c,
        )
        is expected
    )


def test_primitives_do_not_emit_authority_green_or_receipt_fields() -> None:
    costs = compute_trade_cash_cost_scenarios(
        entry_notional_cny=1_000.0,
        gross_exit_leg_proceeds_cny=[1_100.0],
        fee_schedule=_fee_schedule(),
        slippage_scenarios=[SlippageScenario("additional_0bps", 0.0)],
    )
    capacity = evaluate_capacity_scenarios(
        planned_legs=aggregate_planned_legs(_planned_legs()),
        window_liquidity=_liquidity(),
        base_participation_rate=0.10,
    )

    payload = repr((asdict(costs[0]), asdict(capacity)))
    assert "authority" not in payload
    assert "receipt" not in payload
    assert "GREEN" not in payload
