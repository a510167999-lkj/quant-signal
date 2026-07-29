from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_FLOOR
import math
from typing import Any
from zoneinfo import ZoneInfo


__all__ = [
    "AggregatedPlannedLeg",
    "BuyCapacitySizing",
    "BuySizing",
    "CapacityEvaluation",
    "CapacityLegEvaluation",
    "CapacityScenario",
    "CashFeeSchedule",
    "LegCashCost",
    "MinuteAmount",
    "PlannedLeg",
    "SelectedWindowLiquidity",
    "SellSizing",
    "SlippageScenario",
    "TradeCashCostScenario",
    "aggregate_planned_legs",
    "capacity_required_passes",
    "compute_trade_cash_cost_scenarios",
    "evaluate_capacity_scenarios",
    "select_half_open_one_minute_window",
    "size_buy_at_c_and_two_c",
    "size_sell_all",
]


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SUPPORTED_MARKET_PHASES = {
    "opening_call_auction",
    "continuous_auction",
    "closing_call_auction",
}
_CAPACITY_SCENARIOS = (
    ("C_and_p0", 1.0, 1.0),
    ("C_and_p0_over_2", 1.0, 0.5),
    ("2C_and_p0", 2.0, 1.0),
    ("2C_and_p0_over_2", 2.0, 0.5),
)


@dataclass(frozen=True)
class CashFeeSchedule:
    buy_commission_bps: float
    sell_commission_bps: float
    minimum_commission_cny: float
    sell_stamp_duty_bps: float
    other_buy_fees_bps: float
    other_sell_fees_bps: float
    explicit_fee_floor_bps_on_entry_notional: float
    base_one_way_slippage_bps: float


@dataclass(frozen=True)
class SlippageScenario:
    scenario_id: str
    additional_one_way_slippage_bps: float


@dataclass(frozen=True)
class LegCashCost:
    leg_index: int
    side: str
    gross_notional_cny: float
    commission_cash_cny: float
    stamp_duty_cash_cny: float
    other_fee_cash_cny: float
    base_slippage_cash_cny: float
    additional_slippage_cash_cny: float
    actual_leg_cash_cost_cny: float


@dataclass(frozen=True)
class TradeCashCostScenario:
    scenario_id: str
    base_one_way_slippage_bps: float
    additional_one_way_slippage_bps: float
    legs: tuple[LegCashCost, ...]
    actual_explicit_fee_cash_cny: float
    explicit_fee_floor_cash_cny: float
    charged_explicit_fee_cash_cny: float
    base_slippage_cash_cny: float
    additional_slippage_cash_cny: float
    total_cash_cost_cny: float


@dataclass(frozen=True)
class BuySizing:
    capital_multiplier_of_c: float
    capital_cny: float
    scaled_available_buy_notional_cny: float
    raw_entry_price_cny_per_share: float
    board_lot_shares: int
    planned_shares: int
    planned_order_notional_cny: float


@dataclass(frozen=True)
class BuyCapacitySizing:
    at_c: BuySizing
    at_two_c: BuySizing


@dataclass(frozen=True)
class SellSizing:
    available_shares: int
    raw_exit_price_cny_per_share: float
    planned_shares: int
    planned_order_notional_cny: float


@dataclass(frozen=True)
class PlannedLeg:
    trade_key: str
    order_key: str
    stable_security_id: str
    side: str
    execution_session: date
    execution_window: str
    planned_order_notional_at_c_cny: float
    planned_order_notional_at_two_c_cny: float


@dataclass(frozen=True)
class AggregatedPlannedLeg:
    stable_security_id: str
    side: str
    execution_session: date
    execution_window: str
    trade_and_order_keys: tuple[tuple[str, str], ...]
    planned_order_notional_at_c_cny: float
    planned_order_notional_at_two_c_cny: float


@dataclass(frozen=True)
class MinuteAmount:
    stable_security_id: str
    execution_session: date
    interval_start_at: datetime
    interval_end_at: datetime
    market_phase: str
    traded_amount: float
    amount_unit: str


@dataclass(frozen=True)
class SelectedWindowLiquidity:
    stable_security_id: str
    side: str
    execution_session: date
    execution_window: str
    traded_amount: float
    amount_unit: str
    selected_minutes: tuple[MinuteAmount, ...]


@dataclass(frozen=True)
class CapacityLegEvaluation:
    stable_security_id: str
    side: str
    execution_session: date
    execution_window: str
    trade_and_order_keys: tuple[tuple[str, str], ...]
    planned_order_notional_cny: float
    traded_amount_cny: float
    maximum_participation_rate: float
    participation_rate: float | None
    passes: bool


@dataclass(frozen=True)
class CapacityScenario:
    scenario_id: str
    capital_multiplier_of_c: float
    participation_multiplier_of_p0: float
    maximum_participation_rate: float
    leg_evaluations: tuple[CapacityLegEvaluation, ...]
    passes: bool


@dataclass(frozen=True)
class CapacityEvaluation:
    base_participation_rate: float
    scenarios: tuple[CapacityScenario, ...]
    required_passes: bool


def _finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return number


def _nonnegative_number(value: Any, *, field: str) -> float:
    number = _finite_number(value, field=field)
    if number < 0.0:
        raise ValueError(f"{field} must be nonnegative")
    return number


def _positive_number(value: Any, *, field: str) -> float:
    number = _finite_number(value, field=field)
    if number <= 0.0:
        raise ValueError(f"{field} must be strictly positive")
    return number


def _nonempty_text(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text")
    return value


def _session_date(value: Any, *, field: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValueError(f"{field} must be a date")
    return value


def _nonnegative_whole_number(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative whole number")
    return value


def _validated_fee_schedule(schedule: CashFeeSchedule) -> CashFeeSchedule:
    if not isinstance(schedule, CashFeeSchedule):
        raise ValueError("fee_schedule must be CashFeeSchedule")
    values = {
        field: _nonnegative_number(getattr(schedule, field), field=field)
        for field in (
            "buy_commission_bps",
            "sell_commission_bps",
            "minimum_commission_cny",
            "sell_stamp_duty_bps",
            "other_buy_fees_bps",
            "other_sell_fees_bps",
            "explicit_fee_floor_bps_on_entry_notional",
            "base_one_way_slippage_bps",
        )
    }
    return CashFeeSchedule(**values)


def _validated_slippage_scenarios(
    scenarios: Sequence[SlippageScenario],
) -> tuple[SlippageScenario, ...]:
    if isinstance(scenarios, (str, bytes)) or not isinstance(scenarios, Sequence) or not scenarios:
        raise ValueError("slippage_scenarios must be a nonempty sequence")
    normalized: list[SlippageScenario] = []
    observed_ids: set[str] = set()
    observed_bps: set[float] = set()
    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, SlippageScenario):
            raise ValueError(f"slippage_scenarios[{index}] must be SlippageScenario")
        scenario_id = _nonempty_text(
            scenario.scenario_id,
            field=f"slippage_scenarios[{index}].scenario_id",
        )
        additional = _nonnegative_number(
            scenario.additional_one_way_slippage_bps,
            field=(f"slippage_scenarios[{index}].additional_one_way_slippage_bps"),
        )
        if scenario_id in observed_ids:
            raise ValueError("duplicate slippage scenario_id")
        if additional in observed_bps:
            raise ValueError("duplicate additional slippage bps")
        observed_ids.add(scenario_id)
        observed_bps.add(additional)
        normalized.append(SlippageScenario(scenario_id, additional))
    return tuple(
        sorted(
            normalized,
            key=lambda scenario: (
                scenario.additional_one_way_slippage_bps,
                scenario.scenario_id,
            ),
        )
    )


def compute_trade_cash_cost_scenarios(
    *,
    entry_notional_cny: float,
    gross_exit_leg_proceeds_cny: Sequence[float],
    fee_schedule: CashFeeSchedule,
    slippage_scenarios: Sequence[SlippageScenario],
) -> tuple[TradeCashCostScenario, ...]:
    entry_notional = _positive_number(
        entry_notional_cny,
        field="entry_notional_cny",
    )
    if (
        isinstance(gross_exit_leg_proceeds_cny, (str, bytes))
        or not isinstance(gross_exit_leg_proceeds_cny, Sequence)
        or not gross_exit_leg_proceeds_cny
    ):
        raise ValueError("gross_exit_leg_proceeds_cny must be a nonempty sequence")
    exit_notionals = tuple(
        _positive_number(value, field=f"gross_exit_leg_proceeds_cny[{index}]")
        for index, value in enumerate(gross_exit_leg_proceeds_cny)
    )
    schedule = _validated_fee_schedule(fee_schedule)
    scenarios = _validated_slippage_scenarios(slippage_scenarios)
    leg_inputs = (("buy", entry_notional),) + tuple(
        ("sell", notional) for notional in exit_notionals
    )

    results: list[TradeCashCostScenario] = []
    for scenario in scenarios:
        legs: list[LegCashCost] = []
        for leg_index, (side, notional) in enumerate(leg_inputs):
            commission_bps = (
                schedule.buy_commission_bps if side == "buy" else schedule.sell_commission_bps
            )
            other_fee_bps = (
                schedule.other_buy_fees_bps if side == "buy" else schedule.other_sell_fees_bps
            )
            commission = max(
                notional * commission_bps / 10_000.0,
                schedule.minimum_commission_cny,
            )
            stamp_duty = (
                0.0 if side == "buy" else notional * schedule.sell_stamp_duty_bps / 10_000.0
            )
            other_fee = notional * other_fee_bps / 10_000.0
            base_slippage = notional * schedule.base_one_way_slippage_bps / 10_000.0
            additional_slippage = notional * scenario.additional_one_way_slippage_bps / 10_000.0
            legs.append(
                LegCashCost(
                    leg_index=leg_index,
                    side=side,
                    gross_notional_cny=notional,
                    commission_cash_cny=commission,
                    stamp_duty_cash_cny=stamp_duty,
                    other_fee_cash_cny=other_fee,
                    base_slippage_cash_cny=base_slippage,
                    additional_slippage_cash_cny=additional_slippage,
                    actual_leg_cash_cost_cny=math.fsum(
                        (
                            commission,
                            stamp_duty,
                            other_fee,
                            base_slippage,
                            additional_slippage,
                        )
                    ),
                )
            )

        actual_explicit = math.fsum(
            leg.commission_cash_cny + leg.stamp_duty_cash_cny + leg.other_fee_cash_cny
            for leg in legs
        )
        fee_floor = entry_notional * schedule.explicit_fee_floor_bps_on_entry_notional / 10_000.0
        charged_explicit = max(actual_explicit, fee_floor)
        base_slippage = math.fsum(leg.base_slippage_cash_cny for leg in legs)
        additional_slippage = math.fsum(leg.additional_slippage_cash_cny for leg in legs)
        results.append(
            TradeCashCostScenario(
                scenario_id=scenario.scenario_id,
                base_one_way_slippage_bps=(schedule.base_one_way_slippage_bps),
                additional_one_way_slippage_bps=(scenario.additional_one_way_slippage_bps),
                legs=tuple(legs),
                actual_explicit_fee_cash_cny=actual_explicit,
                explicit_fee_floor_cash_cny=fee_floor,
                charged_explicit_fee_cash_cny=charged_explicit,
                base_slippage_cash_cny=base_slippage,
                additional_slippage_cash_cny=additional_slippage,
                total_cash_cost_cny=math.fsum(
                    (
                        charged_explicit,
                        base_slippage,
                        additional_slippage,
                    )
                ),
            )
        )
    return tuple(results)


def _floor_to_board_lot(
    *,
    available_buy_notional_cny: float,
    price_cny_per_share: float,
    board_lot_shares: int,
) -> int:
    affordable = (
        Decimal(str(available_buy_notional_cny)) / Decimal(str(price_cny_per_share))
    ).to_integral_value(rounding=ROUND_FLOOR)
    return int(affordable) // board_lot_shares * board_lot_shares


def size_buy_at_c_and_two_c(
    *,
    initial_available_buy_notional_cny: float,
    initial_capital_cny: float,
    capital_c_cny: float,
    raw_entry_price_cny_per_share: float,
    board_lot_shares: int,
) -> BuyCapacitySizing:
    initial_available = _nonnegative_number(
        initial_available_buy_notional_cny,
        field="initial_available_buy_notional_cny",
    )
    initial_capital = _positive_number(
        initial_capital_cny,
        field="initial_capital_cny",
    )
    capital_c = _positive_number(capital_c_cny, field="capital_c_cny")
    if capital_c < initial_capital:
        raise ValueError("capital_c_cny must be at least initial_capital_cny")
    price = _positive_number(
        raw_entry_price_cny_per_share,
        field="raw_entry_price_cny_per_share",
    )
    if (
        isinstance(board_lot_shares, bool)
        or not isinstance(board_lot_shares, int)
        or board_lot_shares != 100
    ):
        raise ValueError("board_lot_shares must explicitly equal 100")

    sized: list[BuySizing] = []
    for multiplier in (1.0, 2.0):
        capacity = capital_c * multiplier
        scaled_available = initial_available * capacity / initial_capital
        planned_shares = _floor_to_board_lot(
            available_buy_notional_cny=scaled_available,
            price_cny_per_share=price,
            board_lot_shares=board_lot_shares,
        )
        sized.append(
            BuySizing(
                capital_multiplier_of_c=multiplier,
                capital_cny=capacity,
                scaled_available_buy_notional_cny=scaled_available,
                raw_entry_price_cny_per_share=price,
                board_lot_shares=board_lot_shares,
                planned_shares=planned_shares,
                planned_order_notional_cny=planned_shares * price,
            )
        )
    return BuyCapacitySizing(at_c=sized[0], at_two_c=sized[1])


def size_sell_all(
    *,
    available_shares: int,
    raw_exit_price_cny_per_share: float,
) -> SellSizing:
    shares = _nonnegative_whole_number(
        available_shares,
        field="available_shares",
    )
    price = _positive_number(
        raw_exit_price_cny_per_share,
        field="raw_exit_price_cny_per_share",
    )
    return SellSizing(
        available_shares=shares,
        raw_exit_price_cny_per_share=price,
        planned_shares=shares,
        planned_order_notional_cny=shares * price,
    )


def _validate_side_and_window(side: Any, execution_window: Any) -> tuple[str, str]:
    normalized_side = _nonempty_text(side, field="side")
    normalized_window = _nonempty_text(
        execution_window,
        field="execution_window",
    )
    if normalized_side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if normalized_window not in {"entry", "exit"}:
        raise ValueError("execution_window must be entry or exit")
    if (normalized_side == "buy") != (normalized_window == "entry"):
        raise ValueError("side and execution_window differ")
    return normalized_side, normalized_window


def _planned_key(
    leg: PlannedLeg | AggregatedPlannedLeg,
) -> tuple[str, str, date, str]:
    return (
        leg.stable_security_id,
        leg.side,
        leg.execution_session,
        leg.execution_window,
    )


def aggregate_planned_legs(
    legs: Sequence[PlannedLeg],
) -> tuple[AggregatedPlannedLeg, ...]:
    if isinstance(legs, (str, bytes)) or not isinstance(legs, Sequence) or not legs:
        raise ValueError("planned legs must be a nonempty sequence")
    observed_order_keys: set[str] = set()
    normalized: list[PlannedLeg] = []
    for index, leg in enumerate(legs):
        if not isinstance(leg, PlannedLeg):
            raise ValueError(f"planned legs[{index}] must be PlannedLeg")
        trade_key = _nonempty_text(
            leg.trade_key,
            field=f"planned legs[{index}].trade_key",
        )
        order_key = _nonempty_text(
            leg.order_key,
            field=f"planned legs[{index}].order_key",
        )
        if order_key in observed_order_keys:
            raise ValueError("duplicate order_key")
        observed_order_keys.add(order_key)
        security = _nonempty_text(
            leg.stable_security_id,
            field=f"planned legs[{index}].stable_security_id",
        )
        side, execution_window = _validate_side_and_window(
            leg.side,
            leg.execution_window,
        )
        session = _session_date(
            leg.execution_session,
            field=f"planned legs[{index}].execution_session",
        )
        at_c = _nonnegative_number(
            leg.planned_order_notional_at_c_cny,
            field=(f"planned legs[{index}].planned_order_notional_at_c_cny"),
        )
        at_two_c = _nonnegative_number(
            leg.planned_order_notional_at_two_c_cny,
            field=(f"planned legs[{index}].planned_order_notional_at_two_c_cny"),
        )
        normalized.append(
            PlannedLeg(
                trade_key=trade_key,
                order_key=order_key,
                stable_security_id=security,
                side=side,
                execution_session=session,
                execution_window=execution_window,
                planned_order_notional_at_c_cny=at_c,
                planned_order_notional_at_two_c_cny=at_two_c,
            )
        )

    grouped: dict[
        tuple[str, str, date, str],
        list[PlannedLeg],
    ] = {}
    for leg in sorted(
        normalized,
        key=lambda item: (
            item.trade_key,
            item.order_key,
            *_planned_key(item),
        ),
    ):
        grouped.setdefault(_planned_key(leg), []).append(leg)

    aggregated = [
        AggregatedPlannedLeg(
            stable_security_id=key[0],
            side=key[1],
            execution_session=key[2],
            execution_window=key[3],
            trade_and_order_keys=tuple((leg.trade_key, leg.order_key) for leg in grouped[key]),
            planned_order_notional_at_c_cny=math.fsum(
                leg.planned_order_notional_at_c_cny for leg in grouped[key]
            ),
            planned_order_notional_at_two_c_cny=math.fsum(
                leg.planned_order_notional_at_two_c_cny for leg in grouped[key]
            ),
        )
        for key in sorted(grouped)
    ]
    return tuple(aggregated)


def _aligned_local_time(value: time, *, field: str) -> time:
    if not isinstance(value, time) or value.tzinfo is not None:
        raise ValueError(f"{field} must be a naive Asia/Shanghai local time")
    if value.second != 0 or value.microsecond != 0:
        raise ValueError(f"{field} must align to a one-minute boundary")
    return value


def _aware_datetime(value: Any, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a UTC offset")
    return value.astimezone(_SHANGHAI)


def _normalized_minute(row: MinuteAmount, *, index: int) -> MinuteAmount:
    security = _nonempty_text(
        row.stable_security_id,
        field=f"rows[{index}].stable_security_id",
    )
    session = _session_date(
        row.execution_session,
        field=f"rows[{index}].execution_session",
    )
    start = _aware_datetime(
        row.interval_start_at,
        field=f"rows[{index}].interval_start_at",
    )
    end = _aware_datetime(
        row.interval_end_at,
        field=f"rows[{index}].interval_end_at",
    )
    if (
        start.second != 0
        or start.microsecond != 0
        or end.second != 0
        or end.microsecond != 0
        or end - start != timedelta(minutes=1)
    ):
        raise ValueError("each source row must be an aligned one-minute interval")
    if start.date() != session or end.date() != session:
        raise ValueError("one-minute interval must stay within execution_session")
    if row.market_phase not in _SUPPORTED_MARKET_PHASES:
        raise ValueError("market_phase is unsupported")
    amount = _nonnegative_number(
        row.traded_amount,
        field=f"rows[{index}].traded_amount",
    )
    unit = _nonempty_text(
        row.amount_unit,
        field=f"rows[{index}].amount_unit",
    )
    return MinuteAmount(
        stable_security_id=security,
        execution_session=session,
        interval_start_at=start,
        interval_end_at=end,
        market_phase=row.market_phase,
        traded_amount=amount,
        amount_unit=unit,
    )


def select_half_open_one_minute_window(
    *,
    rows: Sequence[MinuteAmount],
    stable_security_id: str,
    side: str,
    execution_session: date,
    execution_window: str,
    window_start_time: time,
    window_end_time: time,
    timezone_name: str,
    include_call_auction: bool,
    amount_unit: str,
) -> SelectedWindowLiquidity:
    if timezone_name != "Asia/Shanghai":
        raise ValueError("timezone_name must explicitly equal Asia/Shanghai")
    if type(include_call_auction) is not bool:
        raise ValueError("include_call_auction must be boolean")
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Sequence) or not rows:
        raise ValueError("rows must be a nonempty sequence")
    security = _nonempty_text(
        stable_security_id,
        field="stable_security_id",
    )
    normalized_side, normalized_window = _validate_side_and_window(
        side,
        execution_window,
    )
    session = _session_date(
        execution_session,
        field="execution_session",
    )
    start_time = _aligned_local_time(
        window_start_time,
        field="window_start_time",
    )
    end_time = _aligned_local_time(
        window_end_time,
        field="window_end_time",
    )
    if start_time >= end_time:
        raise ValueError("window_start_time must precede window_end_time")
    unit = _nonempty_text(amount_unit, field="amount_unit")
    window_start = datetime.combine(session, start_time, _SHANGHAI)
    window_end = datetime.combine(session, end_time, _SHANGHAI)

    normalized_rows: list[MinuteAmount] = []
    for index, raw_row in enumerate(rows):
        if not isinstance(raw_row, MinuteAmount):
            raise ValueError(f"rows[{index}] must be MinuteAmount")
        normalized_rows.append(_normalized_minute(raw_row, index=index))
    normalized_rows.sort(
        key=lambda row: (
            row.stable_security_id,
            row.execution_session,
            row.interval_start_at,
            row.interval_end_at,
            row.market_phase,
        )
    )

    target_by_start: dict[datetime, MinuteAmount] = {}
    for row in normalized_rows:
        if row.stable_security_id != security or row.execution_session != session:
            continue
        if row.interval_start_at in target_by_start:
            raise ValueError("duplicate observed one-minute interval")
        target_by_start[row.interval_start_at] = row
        if row.amount_unit != unit:
            raise ValueError("source row amount_unit differs from explicit amount_unit")
        overlaps = row.interval_start_at < window_end and row.interval_end_at > window_start
        contained = row.interval_start_at >= window_start and row.interval_end_at <= window_end
        if overlaps and not contained:
            raise ValueError("one-minute interval crosses half-open window boundary")

    expected_starts: list[datetime] = []
    cursor = window_start
    while cursor < window_end:
        expected_starts.append(cursor)
        cursor += timedelta(minutes=1)
    missing = [
        expected.isoformat() for expected in expected_starts if expected not in target_by_start
    ]
    if missing:
        raise ValueError("missing observed one-minute interval: " + ",".join(missing))

    observed = [target_by_start[start] for start in expected_starts]
    selected = tuple(
        row for row in observed if include_call_auction or row.market_phase == "continuous_auction"
    )
    return SelectedWindowLiquidity(
        stable_security_id=security,
        side=normalized_side,
        execution_session=session,
        execution_window=normalized_window,
        traded_amount=math.fsum(row.traded_amount for row in selected),
        amount_unit=unit,
        selected_minutes=selected,
    )


def _liquidity_key(
    value: SelectedWindowLiquidity,
) -> tuple[str, str, date, str]:
    return (
        value.stable_security_id,
        value.side,
        value.execution_session,
        value.execution_window,
    )


def capacity_required_passes(
    *,
    c_and_p0_passes: bool,
    c_and_p0_over_2_passes: bool,
    two_c_and_p0_passes: bool,
) -> bool:
    values = (
        c_and_p0_passes,
        c_and_p0_over_2_passes,
        two_c_and_p0_passes,
    )
    if any(type(value) is not bool for value in values):
        raise ValueError("capacity pass inputs must be boolean")
    return c_and_p0_passes and (c_and_p0_over_2_passes or two_c_and_p0_passes)


def evaluate_capacity_scenarios(
    *,
    planned_legs: Sequence[AggregatedPlannedLeg],
    window_liquidity: Sequence[SelectedWindowLiquidity],
    base_participation_rate: float,
) -> CapacityEvaluation:
    try:
        p0 = _finite_number(
            base_participation_rate,
            field="base_participation_rate",
        )
    except ValueError as exc:
        raise ValueError("base_participation_rate must explicitly equal 0.10") from exc
    if p0 != 0.10:
        raise ValueError("base_participation_rate must explicitly equal 0.10")
    if (
        isinstance(planned_legs, (str, bytes))
        or not isinstance(planned_legs, Sequence)
        or not planned_legs
    ):
        raise ValueError("planned_legs must be a nonempty sequence")
    if (
        isinstance(window_liquidity, (str, bytes))
        or not isinstance(window_liquidity, Sequence)
        or not window_liquidity
    ):
        raise ValueError("window_liquidity must be a nonempty sequence")

    planned_by_key: dict[
        tuple[str, str, date, str],
        AggregatedPlannedLeg,
    ] = {}
    for index, leg in enumerate(planned_legs):
        if not isinstance(leg, AggregatedPlannedLeg):
            raise ValueError(f"planned_legs[{index}] must be AggregatedPlannedLeg")
        security = _nonempty_text(
            leg.stable_security_id,
            field=f"planned_legs[{index}].stable_security_id",
        )
        side, execution_window = _validate_side_and_window(
            leg.side,
            leg.execution_window,
        )
        session = _session_date(
            leg.execution_session,
            field=f"planned_legs[{index}].execution_session",
        )
        key = (security, side, session, execution_window)
        if key in planned_by_key:
            raise ValueError("duplicate aggregated planned-leg key")
        if not leg.trade_and_order_keys:
            raise ValueError("aggregated planned leg has no trade/order keys")
        normalized_pairs: list[tuple[str, str]] = []
        for trade_key, order_key in leg.trade_and_order_keys:
            normalized_pairs.append(
                (
                    _nonempty_text(trade_key, field="trade_key"),
                    _nonempty_text(order_key, field="order_key"),
                )
            )
        if tuple(sorted(normalized_pairs)) != tuple(normalized_pairs):
            raise ValueError("trade/order keys must use stable sorted order")
        normalized = AggregatedPlannedLeg(
            stable_security_id=security,
            side=side,
            execution_session=session,
            execution_window=execution_window,
            trade_and_order_keys=tuple(normalized_pairs),
            planned_order_notional_at_c_cny=_nonnegative_number(
                leg.planned_order_notional_at_c_cny,
                field="planned_order_notional_at_c_cny",
            ),
            planned_order_notional_at_two_c_cny=_nonnegative_number(
                leg.planned_order_notional_at_two_c_cny,
                field="planned_order_notional_at_two_c_cny",
            ),
        )
        planned_by_key[key] = normalized
    if {key[1] for key in planned_by_key} != {"buy", "sell"}:
        raise ValueError("planned legs must include both buy and sell sides")

    liquidity_by_key: dict[
        tuple[str, str, date, str],
        SelectedWindowLiquidity,
    ] = {}
    for index, row in enumerate(window_liquidity):
        if not isinstance(row, SelectedWindowLiquidity):
            raise ValueError(f"window_liquidity[{index}] must be SelectedWindowLiquidity")
        security = _nonempty_text(
            row.stable_security_id,
            field=f"window_liquidity[{index}].stable_security_id",
        )
        side, execution_window = _validate_side_and_window(
            row.side,
            row.execution_window,
        )
        session = _session_date(
            row.execution_session,
            field=f"window_liquidity[{index}].execution_session",
        )
        key = (security, side, session, execution_window)
        if key in liquidity_by_key:
            raise ValueError("duplicate window-liquidity key")
        if row.amount_unit != "CNY":
            raise ValueError("capacity liquidity amount_unit must explicitly equal CNY")
        amount = _nonnegative_number(
            row.traded_amount,
            field=f"window_liquidity[{index}].traded_amount",
        )
        liquidity_by_key[key] = SelectedWindowLiquidity(
            stable_security_id=security,
            side=side,
            execution_session=session,
            execution_window=execution_window,
            traded_amount=amount,
            amount_unit="CNY",
            selected_minutes=tuple(row.selected_minutes),
        )

    missing = sorted(set(planned_by_key) - set(liquidity_by_key))
    extra = sorted(set(liquidity_by_key) - set(planned_by_key))
    if missing:
        raise ValueError(f"missing liquidity keys: {missing!r}")
    if extra:
        raise ValueError(f"extra liquidity keys: {extra!r}")

    scenario_results: list[CapacityScenario] = []
    for scenario_id, capital_multiplier, participation_multiplier in _CAPACITY_SCENARIOS:
        maximum_rate = p0 * participation_multiplier
        leg_evaluations: list[CapacityLegEvaluation] = []
        for key in sorted(planned_by_key):
            planned = planned_by_key[key]
            liquidity = liquidity_by_key[key]
            planned_notional = (
                planned.planned_order_notional_at_c_cny
                if capital_multiplier == 1.0
                else planned.planned_order_notional_at_two_c_cny
            )
            amount = liquidity.traded_amount
            if planned_notional > 0.0 and amount > 0.0:
                participation_rate: float | None = planned_notional / amount
                leg_passes = participation_rate <= maximum_rate
            else:
                participation_rate = None
                leg_passes = False
            leg_evaluations.append(
                CapacityLegEvaluation(
                    stable_security_id=key[0],
                    side=key[1],
                    execution_session=key[2],
                    execution_window=key[3],
                    trade_and_order_keys=planned.trade_and_order_keys,
                    planned_order_notional_cny=planned_notional,
                    traded_amount_cny=amount,
                    maximum_participation_rate=maximum_rate,
                    participation_rate=participation_rate,
                    passes=leg_passes,
                )
            )
        scenario_results.append(
            CapacityScenario(
                scenario_id=scenario_id,
                capital_multiplier_of_c=capital_multiplier,
                participation_multiplier_of_p0=participation_multiplier,
                maximum_participation_rate=maximum_rate,
                leg_evaluations=tuple(leg_evaluations),
                passes=all(item.passes for item in leg_evaluations),
            )
        )

    result_by_id = {scenario.scenario_id: scenario for scenario in scenario_results}
    required = capacity_required_passes(
        c_and_p0_passes=result_by_id["C_and_p0"].passes,
        c_and_p0_over_2_passes=(result_by_id["C_and_p0_over_2"].passes),
        two_c_and_p0_passes=result_by_id["2C_and_p0"].passes,
    )
    return CapacityEvaluation(
        base_participation_rate=p0,
        scenarios=tuple(scenario_results),
        required_passes=required,
    )
