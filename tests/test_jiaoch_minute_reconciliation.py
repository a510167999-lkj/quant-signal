from __future__ import annotations

import copy
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import inspect
import json

import pytest

from app.jiaoch_minute_reconciliation import (
    AMOUNT_RELATIVE_TOLERANCE,
    COLLECTION_SET_SCHEMA,
    DAILY_AMOUNT_ABSOLUTE_TOLERANCE_CNY,
    FIVE_MINUTE_AMOUNT_ABSOLUTE_TOLERANCE_CNY,
    JiaochMinuteCrossFrequencyReconciliationReceipt,
    reconcile_jiaoch_minute_raw_bodies_unbound,
)


_SESSION = date(2026, 7, 28)
_TS_CODE = "600000.SH"
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


def _minute_labels() -> list[str]:
    prefix = _SESSION.isoformat()
    labels = [f"{prefix} 09:30:00"]
    cursor = datetime.combine(_SESSION, time(9, 31))
    while cursor.time() <= time(11, 30):
        labels.append(cursor.strftime("%Y-%m-%d %H:%M:%S"))
        cursor += timedelta(minutes=1)
    cursor = datetime.combine(_SESSION, time(13, 1))
    while cursor.time() <= time(15, 0):
        labels.append(cursor.strftime("%Y-%m-%d %H:%M:%S"))
        cursor += timedelta(minutes=1)
    assert len(labels) == 241
    return labels


def _five_minute_labels() -> list[str]:
    prefix = _SESSION.isoformat()
    labels = [f"{prefix} 09:30:00"]
    cursor = datetime.combine(_SESSION, time(9, 35))
    while cursor.time() <= time(11, 30):
        labels.append(cursor.strftime("%Y-%m-%d %H:%M:%S"))
        cursor += timedelta(minutes=5)
    cursor = datetime.combine(_SESSION, time(13, 5))
    while cursor.time() <= time(15, 0):
        labels.append(cursor.strftime("%Y-%m-%d %H:%M:%S"))
        cursor += timedelta(minutes=5)
    assert len(labels) == 49
    return labels


def _minute_row(
    label: str,
    *,
    ts_code: str = _TS_CODE,
    volume: object = 100,
    amount: object = Decimal("1000.0"),
) -> list[object]:
    return [
        ts_code,
        label,
        Decimal("10.0"),
        Decimal("10.0"),
        Decimal("10.1"),
        Decimal("9.9"),
        volume,
        amount,
    ]


def _payloads(
    *,
    ts_code: str = _TS_CODE,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    one_minute = {
        "code": 0,
        "data": {
            "fields": list(_MINUTE_FIELDS),
            "items": [_minute_row(label, ts_code=ts_code) for label in _minute_labels()],
        },
        "msg": "success",
    }
    five_minute = {
        "code": 0,
        "data": {
            "fields": list(_MINUTE_FIELDS),
            "items": [
                _minute_row(_five_minute_labels()[0], ts_code=ts_code),
                *[
                    _minute_row(
                        label,
                        ts_code=ts_code,
                        volume=500,
                        amount=Decimal("5000.0"),
                    )
                    for label in _five_minute_labels()[1:]
                ],
            ],
        },
        "msg": "success",
    }
    daily = {
        "code": 0,
        "data": {
            "fields": list(_DAILY_FIELDS),
            "items": [
                [
                    ts_code,
                    _SESSION.strftime("%Y%m%d"),
                    Decimal("10.0"),
                    Decimal("10.1"),
                    Decimal("9.9"),
                    Decimal("10.0"),
                    Decimal("241.0"),
                    Decimal("241.0"),
                    None,
                    None,
                ]
            ],
        },
        "msg": "success",
    }
    return one_minute, five_minute, daily


def _raw(value: object) -> bytes:
    replacements: dict[str, str] = {}

    def replace_decimals(item: object) -> object:
        if isinstance(item, Decimal):
            marker = f"__decimal_{len(replacements)}__"
            replacements[f'"{marker}"'] = format(item, "f")
            return marker
        if isinstance(item, list):
            return [replace_decimals(value) for value in item]
        if isinstance(item, dict):
            return {key: replace_decimals(value) for key, value in item.items()}
        return item

    encoded = json.dumps(
        replace_decimals(value),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=True,
    )
    for marker, number in replacements.items():
        encoded = encoded.replace(marker, number)
    return encoded.encode("utf-8")


def _reconcile(
    payloads: tuple[object, object, object] | None = None,
    *,
    ts_code: str = _TS_CODE,
    board: str = "SSE_MAIN",
) -> JiaochMinuteCrossFrequencyReconciliationReceipt:
    one_minute, five_minute, daily = _payloads(ts_code=ts_code) if payloads is None else payloads
    return reconcile_jiaoch_minute_raw_bodies_unbound(
        one_minute_response_body=_raw(one_minute),
        five_minute_response_body=_raw(five_minute),
        daily_response_body=_raw(daily),
        requested_ts_code=ts_code,
        execution_session=_SESSION,
        stable_security_id=f"security-{ts_code}",
        pit_security_descriptor_sha256="a" * 64,
        board=board,
    )


def _amount_at_upper_boundary(aggregated: Decimal, absolute: Decimal) -> Decimal:
    return (aggregated + absolute) / (Decimal(1) - AMOUNT_RELATIVE_TOLERANCE)


def test_reconciles_exact_241_49_1_closure_without_granting_authority() -> None:
    receipt = _reconcile()

    assert receipt.schema == "jiaoch-minute-cross-frequency-reconciliation/v1"
    assert receipt.reconciliation_status == "CROSS_FREQUENCY_RECONCILED_UNBOUND"
    assert receipt.source_authority_status == "UNBOUND"
    assert receipt.minute_amount_authority_status == "UNBOUND"
    assert receipt.collection_set_schema == COLLECTION_SET_SCHEMA
    assert receipt.collection_set_status == "NOT_PROVIDED"
    assert receipt.collection_set_sha256 is None
    assert receipt.mathematical_reconciliation_verified is True
    assert receipt.verified is False
    assert receipt.one_minute_row_count == 241
    assert receipt.five_minute_row_count == 49
    assert receipt.daily_row_count == 1
    assert len(receipt.one_minute_rows) == 241
    assert len(receipt.five_minute_buckets) == 49
    assert receipt.opening_special_reconciled is True
    assert all(row.eligible_for_capacity is False for row in receipt.one_minute_rows)
    assert all(bucket.eligible_for_capacity is False for bucket in receipt.five_minute_buckets)
    assert receipt.all_capacity_eligible is False
    assert receipt.rows_published is False
    assert receipt.embargo_consumed is False
    assert receipt.final_oos_consumed is False
    assert receipt.production_profile_registered is False
    assert receipt.production_recommendation_eligible is False


def test_bucket_membership_never_crosses_lunch_or_closing_boundaries() -> None:
    receipt = _reconcile()
    by_label = {
        bucket.source_label_at.strftime("%H:%M"): bucket for bucket in receipt.five_minute_buckets
    }

    assert by_label["09:30"].constituent_labels == ("09:30",)
    assert by_label["09:35"].constituent_labels == (
        "09:31",
        "09:32",
        "09:33",
        "09:34",
        "09:35",
    )
    assert by_label["11:30"].constituent_labels == (
        "11:26",
        "11:27",
        "11:28",
        "11:29",
        "11:30",
    )
    assert by_label["13:05"].constituent_labels == (
        "13:01",
        "13:02",
        "13:03",
        "13:04",
        "13:05",
    )
    assert by_label["15:00"].constituent_labels == (
        "14:56",
        "14:57",
        "14:58",
        "14:59",
        "15:00",
    )
    assert by_label["15:00"].phase_status == "CONTAINS_CLOSING_BOUNDARY"


def test_exact_descending_provider_order_is_normalized_but_mixed_order_rejects() -> None:
    descending = _payloads()
    descending[0]["data"]["items"].reverse()
    descending[1]["data"]["items"].reverse()
    receipt = _reconcile(descending)
    assert receipt.one_minute_rows[0].source_label_at.strftime("%H:%M") == "09:30"
    assert receipt.one_minute_rows[-1].source_label_at.strftime("%H:%M") == "15:00"

    mixed = _payloads()
    mixed[0]["data"]["items"][100], mixed[0]["data"]["items"][101] = (
        mixed[0]["data"]["items"][101],
        mixed[0]["data"]["items"][100],
    )
    with pytest.raises(ValueError, match="schedule"):
        _reconcile(mixed)


@pytest.mark.parametrize(
    ("payload_index", "row_index", "field_index", "value"),
    [
        (1, 1, 4, Decimal("10.2")),
        (1, 1, 6, 501),
        (2, 0, 3, Decimal("10.2")),
        (2, 0, 6, Decimal("241.01")),
        (1, 0, 6, 101),
    ],
)
def test_ohlc_and_volume_must_reconcile_exactly(
    payload_index: int,
    row_index: int,
    field_index: int,
    value: object,
) -> None:
    payloads = _payloads()
    payloads[payload_index]["data"]["items"][row_index][field_index] = value

    with pytest.raises(ValueError, match="OHLC|volume"):
        _reconcile(payloads)


def test_five_minute_amount_tolerance_is_inclusive_and_cannot_expand() -> None:
    assert FIVE_MINUTE_AMOUNT_ABSOLUTE_TOLERANCE_CNY == Decimal("50")
    assert AMOUNT_RELATIVE_TOLERANCE == Decimal("1e-8")
    boundary = _amount_at_upper_boundary(
        Decimal("5000"),
        FIVE_MINUTE_AMOUNT_ABSOLUTE_TOLERANCE_CNY,
    )
    at_boundary = _payloads()
    at_boundary[1]["data"]["items"][1][7] = boundary
    receipt = _reconcile(at_boundary)
    assert receipt.maximum_five_minute_amount_delta_cny == boundary - Decimal("5000")

    above_boundary = copy.deepcopy(at_boundary)
    above_boundary[1]["data"]["items"][1][7] = boundary + Decimal("0.000000001")
    with pytest.raises(ValueError, match="five-minute amount tolerance"):
        _reconcile(above_boundary)


def test_daily_amount_tolerance_is_inclusive_and_cannot_expand() -> None:
    assert DAILY_AMOUNT_ABSOLUTE_TOLERANCE_CNY == Decimal("150")
    boundary_cny = _amount_at_upper_boundary(
        Decimal("241000"),
        DAILY_AMOUNT_ABSOLUTE_TOLERANCE_CNY,
    )
    at_boundary = _payloads()
    at_boundary[2]["data"]["items"][0][7] = boundary_cny / Decimal("1000")
    receipt = _reconcile(at_boundary)
    assert receipt.daily_amount_delta_cny == boundary_cny - Decimal("241000")

    above_boundary = copy.deepcopy(at_boundary)
    above_boundary[2]["data"]["items"][0][7] = (boundary_cny + Decimal("0.000000001")) / Decimal(
        "1000"
    )
    with pytest.raises(ValueError, match="daily amount tolerance"):
        _reconcile(above_boundary)


@pytest.mark.parametrize(("field_index", "value"), [(8, Decimal("0.1")), (9, Decimal("0.1"))])
def test_nonzero_after_hours_fields_fail_closed(field_index: int, value: Decimal) -> None:
    payloads = _payloads()
    payloads[2]["data"]["items"][0][field_index] = value

    with pytest.raises(ValueError, match="after-hours"):
        _reconcile(payloads)


def test_null_or_zero_after_hours_fields_are_accepted_without_subtraction() -> None:
    payloads = _payloads()
    payloads[2]["data"]["items"][0][8:] = [Decimal("0.0"), Decimal("0.0")]
    receipt = _reconcile(payloads)
    assert receipt.daily_after_hours_status == "NULL_OR_ZERO_ONLY"


@pytest.mark.parametrize(
    "body_mutator",
    [
        lambda bodies: (
            b'{"code":0,"code":0,"data":{"fields":[],"items":[]},"msg":"success"}',
            bodies[1],
            bodies[2],
        ),
        lambda bodies: (
            bodies[0].replace(b"1000.0", b"NaN", 1),
            bodies[1],
            bodies[2],
        ),
        lambda bodies: (
            bodies[0].replace(b",100,", b",true,", 1),
            bodies[1],
            bodies[2],
        ),
    ],
)
def test_strict_json_rejects_duplicate_nonfinite_and_boolean_numbers(body_mutator) -> None:
    bodies = tuple(_raw(payload) for payload in _payloads())
    invalid = body_mutator(bodies)

    with pytest.raises(ValueError):
        reconcile_jiaoch_minute_raw_bodies_unbound(
            one_minute_response_body=invalid[0],
            five_minute_response_body=invalid[1],
            daily_response_body=invalid[2],
            requested_ts_code=_TS_CODE,
            execution_session=_SESSION,
            stable_security_id="security-600000",
            pit_security_descriptor_sha256="a" * 64,
            board="SSE_MAIN",
        )


def test_exact_fields_row_counts_and_timestamp_schedule_are_closed() -> None:
    invalid_payloads = []
    wrong_fields = _payloads()
    wrong_fields[1]["data"]["fields"] = list(reversed(_MINUTE_FIELDS))
    invalid_payloads.append(wrong_fields)
    missing_one_minute = _payloads()
    missing_one_minute[0]["data"]["items"].pop()
    invalid_payloads.append(missing_one_minute)
    extra_five_minute = _payloads()
    extra_five_minute[1]["data"]["items"].append(
        copy.deepcopy(extra_five_minute[1]["data"]["items"][-1])
    )
    invalid_payloads.append(extra_five_minute)
    extra_daily = _payloads()
    extra_daily[2]["data"]["items"].append(copy.deepcopy(extra_daily[2]["data"]["items"][0]))
    invalid_payloads.append(extra_daily)
    lunch_label = _payloads()
    lunch_label[0]["data"]["items"][121][1] = "2026-07-28 13:00:00"
    invalid_payloads.append(lunch_label)

    for payloads in invalid_payloads:
        with pytest.raises(ValueError):
            _reconcile(payloads)


@pytest.mark.parametrize(
    ("board", "ts_code"),
    [
        ("SSE_MAIN", "600000.SH"),
        ("SZSE_MAIN", "000001.SZ"),
        ("SZSE_CHINEXT", "300750.SZ"),
    ],
)
def test_only_main_board_and_chinext_pairs_are_accepted(board: str, ts_code: str) -> None:
    assert _reconcile(ts_code=ts_code, board=board).requested_ts_code == ts_code


@pytest.mark.parametrize(
    ("board", "ts_code"),
    [
        ("SSE_STAR", "688001.SH"),
        ("BSE", "830001.BJ"),
        ("SSE_MAIN", "510300.SH"),
        ("SZSE_MAIN", "300750.SZ"),
        ("SSE_MAIN", "000001.SZ"),
    ],
)
def test_star_bse_funds_and_board_code_mismatches_are_rejected(
    board: str,
    ts_code: str,
) -> None:
    with pytest.raises(ValueError, match="board|ts_code"):
        _reconcile(ts_code=ts_code, board=board)


def test_public_api_has_no_token_network_callback_or_authority_override() -> None:
    assert set(inspect.signature(reconcile_jiaoch_minute_raw_bodies_unbound).parameters) == {
        "one_minute_response_body",
        "five_minute_response_body",
        "daily_response_body",
        "requested_ts_code",
        "execution_session",
        "stable_security_id",
        "pit_security_descriptor_sha256",
        "board",
    }
    assert COLLECTION_SET_SCHEMA == "jiaoch-minute-collection-set/v1"
