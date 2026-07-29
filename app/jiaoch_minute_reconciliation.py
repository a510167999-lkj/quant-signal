"""Pure cross-frequency Jiaoch minute reconciliation without source authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
import re
from typing import Any
from zoneinfo import ZoneInfo


__all__ = [
    "AMOUNT_RELATIVE_TOLERANCE",
    "COLLECTION_SET_SCHEMA",
    "DAILY_AMOUNT_ABSOLUTE_TOLERANCE_CNY",
    "FIVE_MINUTE_AMOUNT_ABSOLUTE_TOLERANCE_CNY",
    "JiaochMinuteCrossFrequencyReconciliationReceipt",
    "ReconciledFiveMinuteBucket",
    "ReconciledOneMinuteRow",
    "reconcile_jiaoch_minute_raw_bodies_unbound",
]


FIVE_MINUTE_AMOUNT_ABSOLUTE_TOLERANCE_CNY = Decimal("50")
DAILY_AMOUNT_ABSOLUTE_TOLERANCE_CNY = Decimal("150")
AMOUNT_RELATIVE_TOLERANCE = Decimal("1e-8")
COLLECTION_SET_SCHEMA = "jiaoch-minute-collection-set/v1"

_SCHEMA = "jiaoch-minute-cross-frequency-reconciliation/v1"
_MAX_RESPONSE_BYTES = 1024 * 1024
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_HEX = frozenset("0123456789abcdef")
_MINUTE_FIELDS = [
    "ts_code",
    "trade_time",
    "open",
    "close",
    "high",
    "low",
    "vol",
    "amount",
]
_DAILY_FIELDS = [
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
    "ah_vol",
    "ah_amount",
]
_SUPPORTED_BOARD_PATTERNS = {
    "SSE_MAIN": re.compile(r"(?:600|601|603|605)[0-9]{3}\.SH"),
    "SZSE_MAIN": re.compile(r"(?:000|001|002|003)[0-9]{3}\.SZ"),
    "SZSE_CHINEXT": re.compile(r"(?:300|301|302)[0-9]{3}\.SZ"),
}


@dataclass(frozen=True)
class ReconciledOneMinuteRow:
    source_ts_code: str
    source_label_at: datetime
    open_price_cny: Decimal
    close_price_cny: Decimal
    high_price_cny: Decimal
    low_price_cny: Decimal
    volume_shares: int
    amount_cny: Decimal
    phase_status: str
    eligible_for_capacity: bool


@dataclass(frozen=True)
class ReconciledFiveMinuteBucket:
    source_label_at: datetime
    constituent_labels: tuple[str, ...]
    observed_amount_cny: Decimal
    aggregated_amount_cny: Decimal
    amount_delta_cny: Decimal
    phase_status: str
    ohlc_volume_exact: bool
    eligible_for_capacity: bool


@dataclass(frozen=True)
class JiaochMinuteCrossFrequencyReconciliationReceipt:
    schema: str
    source_id: str
    requested_ts_code: str
    stable_security_id: str
    pit_security_descriptor_sha256: str
    board: str
    execution_session: date
    one_minute_response_body_sha256: str
    five_minute_response_body_sha256: str
    daily_response_body_sha256: str
    one_minute_row_count: int
    five_minute_row_count: int
    daily_row_count: int
    one_minute_rows: tuple[ReconciledOneMinuteRow, ...]
    five_minute_buckets: tuple[ReconciledFiveMinuteBucket, ...]
    opening_special_reconciled: bool
    daily_after_hours_status: str
    maximum_five_minute_amount_delta_cny: Decimal
    daily_amount_delta_cny: Decimal
    five_minute_amount_absolute_tolerance_cny: Decimal
    daily_amount_absolute_tolerance_cny: Decimal
    amount_relative_tolerance: Decimal
    collection_set_schema: str
    collection_set_status: str
    collection_set_sha256: None
    reconciliation_status: str
    source_authority_status: str
    minute_amount_authority_status: str
    mathematical_reconciliation_verified: bool
    evidence_complete: bool
    verified: bool
    all_capacity_eligible: bool
    rows_published: bool
    production_effect: str
    embargo_consumed: bool
    final_oos_consumed: bool
    production_profile_registered: bool
    production_recommendation_eligible: bool


@dataclass(frozen=True)
class _MinuteBar:
    ts_code: str
    source_label_at: datetime
    open_price_cny: Decimal
    close_price_cny: Decimal
    high_price_cny: Decimal
    low_price_cny: Decimal
    volume_shares: int
    amount_cny: Decimal


@dataclass(frozen=True)
class _DailyBar:
    open_price_cny: Decimal
    close_price_cny: Decimal
    high_price_cny: Decimal
    low_price_cny: Decimal
    volume_shares: int
    amount_cny: Decimal


def _strict_json_loads(raw: Any, *, role: str) -> Any:
    if type(raw) is not bytes or not raw or len(raw) > _MAX_RESPONSE_BYTES:
        raise ValueError(f"{role} response body rejected")

    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"non-finite JSON value: {value}")

    try:
        return json.loads(
            raw,
            object_pairs_hook=object_pairs,
            parse_float=Decimal,
            parse_constant=reject_constant,
        )
    except (
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        raise ValueError(f"{role} strict JSON response rejected") from None


def _strict_items(
    raw: bytes,
    *,
    role: str,
    expected_fields: list[str],
    expected_rows: int,
) -> list[Any]:
    envelope = _strict_json_loads(raw, role=role)
    if type(envelope) is not dict or set(envelope) != {"code", "data", "msg"}:
        raise ValueError(f"{role} response envelope rejected")
    if type(envelope["code"]) is not int or envelope["code"] != 0:
        raise ValueError(f"{role} response success code rejected")
    if type(envelope["msg"]) is not str or envelope["msg"] != "success":
        raise ValueError(f"{role} response success message rejected")
    data = envelope["data"]
    if type(data) is not dict or set(data) != {"fields", "items"}:
        raise ValueError(f"{role} response data envelope rejected")
    if type(data["fields"]) is not list or data["fields"] != expected_fields:
        raise ValueError(f"{role} response fields rejected")
    items = data["items"]
    if type(items) is not list or len(items) != expected_rows:
        raise ValueError(f"{role} response row count rejected")
    return items


def _bounded_text(value: Any, *, field: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 256
        or value.strip() != value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field} binding rejected")
    return value


def _sha256(value: Any, *, field: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in _HEX for character in value)
    ):
        raise ValueError(f"{field} binding rejected")
    return value


def _execution_session(value: Any) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValueError("execution_session binding rejected")
    return value


def _security_binding(board: Any, ts_code: Any) -> tuple[str, str]:
    if type(board) is not str or board not in _SUPPORTED_BOARD_PATTERNS:
        raise ValueError("board binding rejected")
    if type(ts_code) is not str or _SUPPORTED_BOARD_PATTERNS[board].fullmatch(ts_code) is None:
        raise ValueError("board and requested_ts_code binding rejected")
    return board, ts_code


def _expected_one_minute_labels(session: date) -> tuple[datetime, ...]:
    labels = [datetime.combine(session, time(9, 30), _SHANGHAI)]
    cursor = datetime.combine(session, time(9, 31), _SHANGHAI)
    morning_end = datetime.combine(session, time(11, 30), _SHANGHAI)
    while cursor <= morning_end:
        labels.append(cursor)
        cursor += timedelta(minutes=1)
    cursor = datetime.combine(session, time(13, 1), _SHANGHAI)
    afternoon_end = datetime.combine(session, time(15, 0), _SHANGHAI)
    while cursor <= afternoon_end:
        labels.append(cursor)
        cursor += timedelta(minutes=1)
    return tuple(labels)


def _expected_five_minute_labels(session: date) -> tuple[datetime, ...]:
    labels = [datetime.combine(session, time(9, 30), _SHANGHAI)]
    cursor = datetime.combine(session, time(9, 35), _SHANGHAI)
    morning_end = datetime.combine(session, time(11, 30), _SHANGHAI)
    while cursor <= morning_end:
        labels.append(cursor)
        cursor += timedelta(minutes=5)
    cursor = datetime.combine(session, time(13, 5), _SHANGHAI)
    afternoon_end = datetime.combine(session, time(15, 0), _SHANGHAI)
    while cursor <= afternoon_end:
        labels.append(cursor)
        cursor += timedelta(minutes=5)
    return tuple(labels)


def _source_label(value: Any, *, session: date, role: str, row_index: int) -> datetime:
    if type(value) is not str:
        raise ValueError(f"{role} items[{row_index}].trade_time rejected")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise ValueError(f"{role} items[{row_index}].trade_time rejected") from None
    if (
        parsed.strftime("%Y-%m-%d %H:%M:%S") != value
        or parsed.second != 0
        or parsed.microsecond != 0
        or parsed.date() != session
    ):
        raise ValueError(f"{role} items[{row_index}].trade_time rejected")
    return parsed.replace(tzinfo=_SHANGHAI)


def _positive_decimal(value: Any, *, field: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be a finite positive JSON float")
    return value


def _nonnegative_decimal(value: Any, *, field: str) -> Decimal:
    if type(value) is not Decimal or not value.is_finite() or value < 0:
        raise ValueError(f"{field} must be a finite nonnegative JSON float")
    return value


def _volume(value: Any, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _minute_bar(
    row: Any,
    *,
    role: str,
    row_index: int,
    requested_ts_code: str,
    session: date,
) -> _MinuteBar:
    if type(row) is not list or len(row) != len(_MINUTE_FIELDS):
        raise ValueError(f"{role} items[{row_index}] must contain exactly eight values")
    if type(row[0]) is not str or row[0] != requested_ts_code:
        raise ValueError(f"{role} items[{row_index}].ts_code rejected")
    label = _source_label(row[1], session=session, role=role, row_index=row_index)
    open_price = _positive_decimal(row[2], field=f"{role} items[{row_index}].open")
    close_price = _positive_decimal(row[3], field=f"{role} items[{row_index}].close")
    high_price = _positive_decimal(row[4], field=f"{role} items[{row_index}].high")
    low_price = _positive_decimal(row[5], field=f"{role} items[{row_index}].low")
    if (
        low_price > high_price
        or not low_price <= open_price <= high_price
        or not low_price <= close_price <= high_price
    ):
        raise ValueError(f"{role} items[{row_index}] OHLC bounds rejected")
    volume = _volume(row[6], field=f"{role} items[{row_index}].vol")
    amount = _nonnegative_decimal(row[7], field=f"{role} items[{row_index}].amount")
    if (volume == 0) != (amount == 0):
        raise ValueError(f"{role} items[{row_index}] volume and amount zero state differs")
    return _MinuteBar(
        ts_code=requested_ts_code,
        source_label_at=label,
        open_price_cny=open_price,
        close_price_cny=close_price,
        high_price_cny=high_price,
        low_price_cny=low_price,
        volume_shares=volume,
        amount_cny=amount,
    )


def _ordered_minute_bars(
    items: list[Any],
    *,
    role: str,
    requested_ts_code: str,
    session: date,
    expected_labels: tuple[datetime, ...],
) -> tuple[_MinuteBar, ...]:
    bars = tuple(
        _minute_bar(
            row,
            role=role,
            row_index=index,
            requested_ts_code=requested_ts_code,
            session=session,
        )
        for index, row in enumerate(items)
    )
    observed = tuple(bar.source_label_at for bar in bars)
    if observed == expected_labels:
        return bars
    if observed == tuple(reversed(expected_labels)):
        return tuple(reversed(bars))
    raise ValueError(f"{role} source labels are not the exact schedule")


def _after_hours_zero_or_null(value: Any, *, field: str) -> None:
    if value is None:
        return
    if type(value) is Decimal and value.is_finite() and value == 0:
        return
    if type(value) is int and value == 0:
        return
    raise ValueError(f"daily after-hours {field} must be null or zero")


def _daily_bar(
    items: list[Any],
    *,
    requested_ts_code: str,
    session: date,
) -> _DailyBar:
    row = items[0]
    if type(row) is not list or len(row) != len(_DAILY_FIELDS):
        raise ValueError("daily item must contain exactly ten values")
    if type(row[0]) is not str or row[0] != requested_ts_code:
        raise ValueError("daily ts_code rejected")
    expected_date = session.strftime("%Y%m%d")
    if type(row[1]) is not str or row[1] != expected_date:
        raise ValueError("daily trade_date rejected")
    open_price = _positive_decimal(row[2], field="daily open")
    high_price = _positive_decimal(row[3], field="daily high")
    low_price = _positive_decimal(row[4], field="daily low")
    close_price = _positive_decimal(row[5], field="daily close")
    if (
        low_price > high_price
        or not low_price <= open_price <= high_price
        or not low_price <= close_price <= high_price
    ):
        raise ValueError("daily OHLC bounds rejected")
    volume_lots = _nonnegative_decimal(row[6], field="daily vol")
    amount_thousand_cny = _nonnegative_decimal(row[7], field="daily amount")
    _after_hours_zero_or_null(row[8], field="ah_vol")
    _after_hours_zero_or_null(row[9], field="ah_amount")
    volume_shares_decimal = volume_lots * Decimal(100)
    if volume_shares_decimal != volume_shares_decimal.to_integral_value():
        raise ValueError("daily volume cannot be converted exactly from lots to shares")
    volume_shares = int(volume_shares_decimal)
    amount_cny = amount_thousand_cny * Decimal(1000)
    if (volume_shares == 0) != (amount_cny == 0):
        raise ValueError("daily volume and amount zero state differs")
    return _DailyBar(
        open_price_cny=open_price,
        close_price_cny=close_price,
        high_price_cny=high_price,
        low_price_cny=low_price,
        volume_shares=volume_shares,
        amount_cny=amount_cny,
    )


def _amount_within_tolerance(
    observed: Decimal,
    aggregated: Decimal,
    *,
    absolute_tolerance: Decimal,
) -> bool:
    larger = max(observed, aggregated)
    smaller = min(observed, aggregated)
    return larger * (Decimal(1) - AMOUNT_RELATIVE_TOLERANCE) <= (smaller + absolute_tolerance)


def _one_minute_phase_status(label: datetime) -> str:
    label_time = label.time()
    if label_time == time(9, 30):
        return "OPENING_SPECIAL_UNRESOLVED"
    if label_time in {time(11, 30), time(14, 57)}:
        return "LABEL_BOUNDARY_UNRESOLVED"
    if label_time >= time(14, 58):
        return "CLOSING_CALL_AUCTION"
    return "CONTINUOUS_AUCTION_INTERIOR_LABEL"


def _five_minute_phase_status(label: datetime) -> str:
    label_time = label.time()
    if label_time == time(9, 30):
        return "OPENING_SPECIAL_UNRESOLVED"
    if label_time == time(15, 0):
        return "CONTAINS_CLOSING_BOUNDARY"
    if label_time in {time(11, 30), time(14, 55)}:
        return "LABEL_BOUNDARY_UNRESOLVED"
    return "RECONCILIATION_ONLY"


def _one_minute_rows(bars: tuple[_MinuteBar, ...]) -> tuple[ReconciledOneMinuteRow, ...]:
    return tuple(
        ReconciledOneMinuteRow(
            source_ts_code=bar.ts_code,
            source_label_at=bar.source_label_at,
            open_price_cny=bar.open_price_cny,
            close_price_cny=bar.close_price_cny,
            high_price_cny=bar.high_price_cny,
            low_price_cny=bar.low_price_cny,
            volume_shares=bar.volume_shares,
            amount_cny=bar.amount_cny,
            phase_status=_one_minute_phase_status(bar.source_label_at),
            eligible_for_capacity=False,
        )
        for bar in bars
    )


def _bucket_constituents(
    one_minute_by_label: dict[datetime, _MinuteBar],
    five_minute_label: datetime,
) -> tuple[_MinuteBar, ...]:
    if five_minute_label.time() == time(9, 30):
        return (one_minute_by_label[five_minute_label],)
    labels = tuple(five_minute_label - timedelta(minutes=offset) for offset in range(4, -1, -1))
    try:
        return tuple(one_minute_by_label[label] for label in labels)
    except KeyError:
        raise ValueError("five-minute bucket crosses a missing one-minute interval") from None


def _reconciled_five_minute_buckets(
    one_minute: tuple[_MinuteBar, ...],
    five_minute: tuple[_MinuteBar, ...],
) -> tuple[ReconciledFiveMinuteBucket, ...]:
    one_minute_by_label = {bar.source_label_at: bar for bar in one_minute}
    results = []
    for observed in five_minute:
        constituents = _bucket_constituents(one_minute_by_label, observed.source_label_at)
        aggregate_open = constituents[0].open_price_cny
        aggregate_close = constituents[-1].close_price_cny
        aggregate_high = max(row.high_price_cny for row in constituents)
        aggregate_low = min(row.low_price_cny for row in constituents)
        aggregate_volume = sum(row.volume_shares for row in constituents)
        aggregate_amount = sum((row.amount_cny for row in constituents), Decimal(0))
        if (
            observed.open_price_cny != aggregate_open
            or observed.close_price_cny != aggregate_close
            or observed.high_price_cny != aggregate_high
            or observed.low_price_cny != aggregate_low
        ):
            raise ValueError("five-minute OHLC reconciliation rejected")
        if observed.volume_shares != aggregate_volume:
            raise ValueError("five-minute volume reconciliation rejected")
        if not _amount_within_tolerance(
            observed.amount_cny,
            aggregate_amount,
            absolute_tolerance=FIVE_MINUTE_AMOUNT_ABSOLUTE_TOLERANCE_CNY,
        ):
            raise ValueError("five-minute amount tolerance rejected")
        results.append(
            ReconciledFiveMinuteBucket(
                source_label_at=observed.source_label_at,
                constituent_labels=tuple(
                    row.source_label_at.strftime("%H:%M") for row in constituents
                ),
                observed_amount_cny=observed.amount_cny,
                aggregated_amount_cny=aggregate_amount,
                amount_delta_cny=abs(observed.amount_cny - aggregate_amount),
                phase_status=_five_minute_phase_status(observed.source_label_at),
                ohlc_volume_exact=True,
                eligible_for_capacity=False,
            )
        )
    return tuple(results)


def _reconcile_daily(daily: _DailyBar, one_minute: tuple[_MinuteBar, ...]) -> Decimal:
    aggregate_open = one_minute[0].open_price_cny
    aggregate_close = one_minute[-1].close_price_cny
    aggregate_high = max(row.high_price_cny for row in one_minute)
    aggregate_low = min(row.low_price_cny for row in one_minute)
    aggregate_volume = sum(row.volume_shares for row in one_minute)
    aggregate_amount = sum((row.amount_cny for row in one_minute), Decimal(0))
    if (
        daily.open_price_cny != aggregate_open
        or daily.close_price_cny != aggregate_close
        or daily.high_price_cny != aggregate_high
        or daily.low_price_cny != aggregate_low
    ):
        raise ValueError("daily OHLC reconciliation rejected")
    if daily.volume_shares != aggregate_volume:
        raise ValueError("daily volume reconciliation rejected")
    if not _amount_within_tolerance(
        daily.amount_cny,
        aggregate_amount,
        absolute_tolerance=DAILY_AMOUNT_ABSOLUTE_TOLERANCE_CNY,
    ):
        raise ValueError("daily amount tolerance rejected")
    return abs(daily.amount_cny - aggregate_amount)


def reconcile_jiaoch_minute_raw_bodies_unbound(
    *,
    one_minute_response_body: bytes,
    five_minute_response_body: bytes,
    daily_response_body: bytes,
    requested_ts_code: str,
    execution_session: date,
    stable_security_id: str,
    pit_security_descriptor_sha256: str,
    board: str,
) -> JiaochMinuteCrossFrequencyReconciliationReceipt:
    """Reconcile three verified raw bodies mathematically without granting authority."""

    security_id = _bounded_text(stable_security_id, field="stable_security_id")
    descriptor_sha256 = _sha256(
        pit_security_descriptor_sha256,
        field="pit_security_descriptor_sha256",
    )
    session = _execution_session(execution_session)
    normalized_board, ts_code = _security_binding(board, requested_ts_code)
    one_minute_items = _strict_items(
        one_minute_response_body,
        role="one-minute",
        expected_fields=_MINUTE_FIELDS,
        expected_rows=241,
    )
    five_minute_items = _strict_items(
        five_minute_response_body,
        role="five-minute",
        expected_fields=_MINUTE_FIELDS,
        expected_rows=49,
    )
    daily_items = _strict_items(
        daily_response_body,
        role="daily",
        expected_fields=_DAILY_FIELDS,
        expected_rows=1,
    )
    one_minute = _ordered_minute_bars(
        one_minute_items,
        role="one-minute",
        requested_ts_code=ts_code,
        session=session,
        expected_labels=_expected_one_minute_labels(session),
    )
    five_minute = _ordered_minute_bars(
        five_minute_items,
        role="five-minute",
        requested_ts_code=ts_code,
        session=session,
        expected_labels=_expected_five_minute_labels(session),
    )
    daily = _daily_bar(
        daily_items,
        requested_ts_code=ts_code,
        session=session,
    )
    one_minute_rows = _one_minute_rows(one_minute)
    five_minute_buckets = _reconciled_five_minute_buckets(one_minute, five_minute)
    daily_amount_delta = _reconcile_daily(daily, one_minute)
    return JiaochMinuteCrossFrequencyReconciliationReceipt(
        schema=_SCHEMA,
        source_id="jiaoch",
        requested_ts_code=ts_code,
        stable_security_id=security_id,
        pit_security_descriptor_sha256=descriptor_sha256,
        board=normalized_board,
        execution_session=session,
        one_minute_response_body_sha256=hashlib.sha256(one_minute_response_body).hexdigest(),
        five_minute_response_body_sha256=hashlib.sha256(five_minute_response_body).hexdigest(),
        daily_response_body_sha256=hashlib.sha256(daily_response_body).hexdigest(),
        one_minute_row_count=len(one_minute),
        five_minute_row_count=len(five_minute),
        daily_row_count=len(daily_items),
        one_minute_rows=one_minute_rows,
        five_minute_buckets=five_minute_buckets,
        opening_special_reconciled=True,
        daily_after_hours_status="NULL_OR_ZERO_ONLY",
        maximum_five_minute_amount_delta_cny=max(
            (bucket.amount_delta_cny for bucket in five_minute_buckets),
            default=Decimal(0),
        ),
        daily_amount_delta_cny=daily_amount_delta,
        five_minute_amount_absolute_tolerance_cny=(FIVE_MINUTE_AMOUNT_ABSOLUTE_TOLERANCE_CNY),
        daily_amount_absolute_tolerance_cny=DAILY_AMOUNT_ABSOLUTE_TOLERANCE_CNY,
        amount_relative_tolerance=AMOUNT_RELATIVE_TOLERANCE,
        collection_set_schema=COLLECTION_SET_SCHEMA,
        collection_set_status="NOT_PROVIDED",
        collection_set_sha256=None,
        reconciliation_status="CROSS_FREQUENCY_RECONCILED_UNBOUND",
        source_authority_status="UNBOUND",
        minute_amount_authority_status="UNBOUND",
        mathematical_reconciliation_verified=True,
        evidence_complete=False,
        verified=False,
        all_capacity_eligible=False,
        rows_published=False,
        production_effect="NONE",
        embargo_consumed=False,
        final_oos_consumed=False,
        production_profile_registered=False,
        production_recommendation_eligible=False,
    )
