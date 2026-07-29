from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.factor_v2_execution_stress_primitives import (
    MinuteAmount,
    select_half_open_one_minute_window,
)
from app.jiaoch_stk_mins_normalization import (
    VWAP_ABSOLUTE_TOLERANCE_CNY,
    JiaochStkMinsNormalizationReceipt,
    OpeningSpecialUnresolved,
    normalize_jiaoch_stk_mins_success_response,
)


_SESSION = date(2026, 7, 28)
_TS_CODE = "600000.SH"
_STABLE_SECURITY_ID = "security-600000"
_PIT_SECURITY_DESCRIPTOR_SHA256 = "a" * 64
_TOKEN = "unit-credential-token"
_FIELDS = [
    "ts_code",
    "trade_time",
    "open",
    "close",
    "high",
    "low",
    "vol",
    "amount",
]
_SHANGHAI = ZoneInfo("Asia/Shanghai")


def _labels(session: date = _SESSION) -> list[str]:
    prefix = session.isoformat()
    morning = [f"{prefix} 09:30:00"]
    morning.extend(
        f"{prefix} {hour:02d}:{minute:02d}:00"
        for hour, minute in (
            [(9, minute) for minute in range(31, 60)]
            + [(10, minute) for minute in range(60)]
            + [(11, minute) for minute in range(31)]
        )
    )
    afternoon = [
        f"{prefix} {hour:02d}:{minute:02d}:00"
        for hour, minute in (
            [(13, minute) for minute in range(1, 60)]
            + [(14, minute) for minute in range(60)]
            + [(15, 0)]
        )
    ]
    result = morning + afternoon
    assert len(result) == 241
    return result


def _item(
    label: str,
    *,
    ts_code: str = _TS_CODE,
    open_price: object = 10.0,
    close_price: object = 10.0,
    high_price: object = 10.1,
    low_price: object = 9.9,
    volume: object = 100,
    amount: object = 1_000.0,
) -> list[object]:
    return [
        ts_code,
        label,
        open_price,
        close_price,
        high_price,
        low_price,
        volume,
        amount,
    ]


def _payload(
    *,
    ts_code: str = _TS_CODE,
    session: date = _SESSION,
) -> dict[str, object]:
    return {
        "msg": "success",
        "data": {
            "items": [_item(label, ts_code=ts_code) for label in _labels(session)],
            "fields": list(_FIELDS),
        },
        "code": 0,
    }


def _raw(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=True,
    ).encode("utf-8")


def _normalize(
    payload: object | None = None,
    *,
    response_body: bytes | None = None,
    ts_code: str = _TS_CODE,
    session: date = _SESSION,
    stable_security_id: object = _STABLE_SECURITY_ID,
    pit_security_descriptor_sha256: object = _PIT_SECURITY_DESCRIPTOR_SHA256,
    board: object = "SSE_MAIN",
    credential_token: object = _TOKEN,
) -> JiaochStkMinsNormalizationReceipt:
    body = _raw(_payload(ts_code=ts_code, session=session) if payload is None else payload)
    if response_body is not None:
        body = response_body
    return normalize_jiaoch_stk_mins_success_response(
        response_body=body,
        credential_token=credential_token,
        requested_ts_code=ts_code,
        execution_session=session,
        stable_security_id=stable_security_id,
        pit_security_descriptor_sha256=pit_security_descriptor_sha256,
        board=board,
    )


def _mutated_item(
    *,
    row_index: int = 100,
    item_index: int,
    value: object,
) -> dict[str, object]:
    payload = _payload()
    payload["data"]["items"][row_index][item_index] = value
    return payload


def test_normalizes_complete_end_labeled_session_and_keeps_opening_special() -> None:
    receipt = _normalize()

    assert isinstance(receipt, JiaochStkMinsNormalizationReceipt)
    assert receipt.stable_security_id == _STABLE_SECURITY_ID
    assert receipt.pit_security_descriptor_sha256 == _PIT_SECURITY_DESCRIPTOR_SHA256
    assert receipt.board == "SSE_MAIN"
    assert receipt.requested_ts_code == _TS_CODE
    assert receipt.execution_session == _SESSION
    assert receipt.source_row_count == 241
    assert isinstance(receipt.opening_special_unresolved, OpeningSpecialUnresolved)
    opening = receipt.opening_special_unresolved
    assert opening.source_label_at == datetime(2026, 7, 28, 9, 30, tzinfo=_SHANGHAI)
    assert opening.interval_start_at is None
    assert opening.interval_end_at is None
    assert opening.eligible_for_capacity is False
    assert len(receipt.normalized_minutes) == 240
    assert len(receipt.minute_amounts) == 240
    first = receipt.normalized_minutes[0]
    assert first.source_label_at == datetime(2026, 7, 28, 9, 31, tzinfo=_SHANGHAI)
    assert first.open_price_cny == 10.0
    assert first.volume_shares == 100
    assert first.amount_cny == 1_000.0
    assert first.minute_amount == MinuteAmount(
        stable_security_id=_STABLE_SECURITY_ID,
        execution_session=_SESSION,
        interval_start_at=datetime(2026, 7, 28, 9, 30, tzinfo=_SHANGHAI),
        interval_end_at=datetime(2026, 7, 28, 9, 31, tzinfo=_SHANGHAI),
        market_phase="continuous_auction",
        traded_amount=1_000.0,
        amount_unit="CNY",
    )
    assert receipt.minute_amounts[-3].market_phase == "closing_call_auction"
    assert receipt.minute_amounts[-1].interval_end_at == datetime(
        2026, 7, 28, 15, 0, tzinfo=_SHANGHAI
    )
    assert receipt.vwap_ohlc_quality_diagnostic.passed is True
    assert receipt.vwap_ohlc_quality_diagnostic.violation_count == 0
    assert receipt.vwap_ohlc_quality_diagnostic.max_outside_cny == 0.0


def test_normalized_minutes_drive_exact_entry_and_exit_windows() -> None:
    rows = _normalize().minute_amounts
    entry_results = [
        select_half_open_one_minute_window(
            rows=rows,
            stable_security_id=_STABLE_SECURITY_ID,
            side="buy",
            execution_session=_SESSION,
            execution_window="entry",
            window_start_time=time(9, 30),
            window_end_time=time(9, 35),
            timezone_name="Asia/Shanghai",
            include_call_auction=include_call,
            amount_unit="CNY",
        )
        for include_call in (False, True)
    ]
    assert [row.interval_end_at.strftime("%H:%M") for row in entry_results[0].selected_minutes] == [
        "09:31",
        "09:32",
        "09:33",
        "09:34",
        "09:35",
    ]
    assert entry_results[0] == entry_results[1]

    exit_without_call, exit_with_call = [
        select_half_open_one_minute_window(
            rows=rows,
            stable_security_id=_STABLE_SECURITY_ID,
            side="sell",
            execution_session=_SESSION,
            execution_window="exit",
            window_start_time=time(14, 55),
            window_end_time=time(15, 0),
            timezone_name="Asia/Shanghai",
            include_call_auction=include_call,
            amount_unit="CNY",
        )
        for include_call in (False, True)
    ]
    assert [
        row.interval_end_at.strftime("%H:%M") for row in exit_without_call.selected_minutes
    ] == ["14:56", "14:57"]
    assert [row.interval_end_at.strftime("%H:%M") for row in exit_with_call.selected_minutes] == [
        "14:56",
        "14:57",
        "14:58",
        "14:59",
        "15:00",
    ]

    incomplete = tuple(row for row in rows if row.interval_end_at.strftime("%H:%M") != "14:58")
    with pytest.raises(ValueError, match="missing observed one-minute interval"):
        select_half_open_one_minute_window(
            rows=incomplete,
            stable_security_id=_STABLE_SECURITY_ID,
            side="sell",
            execution_session=_SESSION,
            execution_window="exit",
            window_start_time=time(14, 55),
            window_end_time=time(15, 0),
            timezone_name="Asia/Shanghai",
            include_call_auction=False,
            amount_unit="CNY",
        )


def test_requires_exact_envelope_data_keys_success_code_and_field_order() -> None:
    valid = _payload()
    invalid_payloads = []
    for key in ("code", "data", "msg"):
        candidate = copy.deepcopy(valid)
        candidate.pop(key)
        invalid_payloads.append(candidate)
    candidate = copy.deepcopy(valid)
    candidate["extra"] = None
    invalid_payloads.append(candidate)
    for code in (True, 1, 0.0, "0", None):
        candidate = copy.deepcopy(valid)
        candidate["code"] = code
        invalid_payloads.append(candidate)
    for message in ("Success", "", True, None):
        candidate = copy.deepcopy(valid)
        candidate["msg"] = message
        invalid_payloads.append(candidate)
    for data in ([], None, "data"):
        candidate = copy.deepcopy(valid)
        candidate["data"] = data
        invalid_payloads.append(candidate)
    candidate = copy.deepcopy(valid)
    candidate["data"]["extra"] = None
    invalid_payloads.append(candidate)
    candidate = copy.deepcopy(valid)
    candidate["data"].pop("items")
    invalid_payloads.append(candidate)
    candidate = copy.deepcopy(valid)
    candidate["data"]["fields"] = list(reversed(_FIELDS))
    invalid_payloads.append(candidate)
    candidate = copy.deepcopy(valid)
    candidate["data"]["fields"].append("extra")
    invalid_payloads.append(candidate)
    candidate = copy.deepcopy(valid)
    candidate["data"]["items"] = {}
    invalid_payloads.append(candidate)

    for invalid in invalid_payloads:
        with pytest.raises(ValueError):
            _normalize(invalid)

    assert _normalize(valid).source_row_count == 241


def test_reuses_strict_json_guards_for_duplicate_nonfinite_depth_and_token_echo() -> None:
    invalid_bodies = [
        b'{"code":0,"code":0,"data":{},"msg":"success"}',
        b'{"code":0,"data":{"fields":[],"items":[]},"msg":NaN}',
        b'{"code":0,"data":{"fields":[],"items":[]},"msg":1e999}',
        (
            b'{"code":0,"data":{"fields":[],"items":'
            + b"[" * 80
            + b"0"
            + b"]" * 80
            + b'},"msg":"success"}'
        ),
        b'{"code":0,"data":{"fields":[],"items":[]},"msg":"unit-credential-token"}',
        b'{"code":0,"data":{"fields":[],"items":[]},"msg":"unit-credential-\\u0074oken"}',
    ]

    for body in invalid_bodies:
        with pytest.raises(ValueError) as exc_info:
            _normalize(response_body=body)
        assert _TOKEN not in str(exc_info.value)


def test_each_item_must_be_a_json_array_with_exactly_eight_values() -> None:
    invalid_payloads = []
    for row in (
        _item(_labels()[0])[:-1],
        _item(_labels()[0]) + ["extra"],
        {"ts_code": _TS_CODE},
        "row",
        None,
    ):
        candidate = _payload()
        candidate["data"]["items"][0] = row
        invalid_payloads.append(candidate)

    for invalid in invalid_payloads:
        with pytest.raises(ValueError):
            _normalize(invalid)

    with pytest.raises(ValueError):
        _normalize(response_body="not-bytes")


def test_each_row_ts_code_must_strictly_match_the_request() -> None:
    for value in ("600001.SH", "600000.SZ", "", 600000, True, None):
        with pytest.raises(ValueError):
            _normalize(_mutated_item(item_index=0, value=value))


def test_trade_time_requires_exact_second_zero_and_requested_session() -> None:
    for value in (
        "2026-07-28 10:09:01",
        "2026-07-27 10:09:00",
        "2026-07-28T10:09:00",
        "2026-7-28 10:09:00",
        "2026-07-28 10:09:00+08:00",
        "2026-07-28 10:09",
        20260728100900,
        True,
        None,
    ):
        with pytest.raises(ValueError):
            _normalize(_mutated_item(item_index=1, value=value))


def test_ohlc_values_require_exact_finite_positive_float_types() -> None:
    for item_index in range(2, 6):
        for value in (10, True, "10.0", 0.0, -1.0, math.nan, math.inf, -math.inf):
            with pytest.raises(ValueError):
                _normalize(_mutated_item(item_index=item_index, value=value))


def test_ohlc_values_must_stay_between_low_and_high() -> None:
    invalid_rows = [
        _item(_labels()[100], open_price=9.8),
        _item(_labels()[100], close_price=9.8),
        _item(_labels()[100], open_price=10.2),
        _item(_labels()[100], close_price=10.2),
        _item(_labels()[100], high_price=9.8, low_price=10.2),
    ]
    for row in invalid_rows:
        payload = _payload()
        payload["data"]["items"][100] = row
        with pytest.raises(ValueError):
            _normalize(payload)


def test_volume_requires_exact_nonnegative_integer_type() -> None:
    for value in (100.0, True, "100", -1, math.nan, None):
        with pytest.raises(ValueError):
            _normalize(_mutated_item(item_index=6, value=value))


def test_amount_requires_exact_nonnegative_finite_float_type() -> None:
    for value in (1_000, True, "1000.0", -1.0, math.nan, math.inf, None):
        with pytest.raises(ValueError):
            _normalize(_mutated_item(item_index=7, value=value))


def test_volume_and_amount_zero_state_must_be_consistent() -> None:
    for volume, amount in ((0, 1_000.0), (100, 0.0)):
        payload = _payload()
        payload["data"]["items"][100][6:] = [volume, amount]
        with pytest.raises(ValueError):
            _normalize(payload)

    payload = _payload()
    payload["data"]["items"][100][6:] = [0, 0.0]
    receipt = _normalize(payload)
    assert receipt.normalized_minutes[99].volume_shares == 0
    assert receipt.normalized_minutes[99].amount_cny == 0.0


def test_vwap_ohlc_is_diagnostic_but_obvious_unit_mismatch_still_rejects() -> None:
    assert VWAP_ABSOLUTE_TOLERANCE_CNY == 1e-6
    payload = _payload()
    for row_index in range(7):
        payload["data"]["items"][row_index + 1][7] = 989.9998
    payload["data"]["items"][100] = _item(
        _labels()[100],
        open_price=9.14,
        close_price=9.14,
        high_price=9.14,
        low_price=9.13,
        volume=907_200,
        amount=8_269_905.0,
    )

    receipt = _normalize(payload)
    diagnostic = receipt.vwap_ohlc_quality_diagnostic
    assert diagnostic.check_id == "implied_vwap_within_minute_ohlc"
    assert diagnostic.schema_acceptance_effect == "DIAGNOSTIC_ONLY"
    assert diagnostic.evaluated_positive_volume_rows == 241
    assert diagnostic.passed is False
    assert diagnostic.violation_count == 8
    assert diagnostic.max_outside_cny == pytest.approx(
        0.014143518518519,
        abs=1e-12,
    )
    assert receipt.source_authority_status == "UNBOUND"
    assert len(receipt.minute_amounts) == 240

    for amount in (1.0, 100_000.0):
        with pytest.raises(ValueError):
            _normalize(_mutated_item(item_index=7, value=amount))


def test_requires_explicit_stable_id_and_lowercase_pit_descriptor_sha() -> None:
    for value in ("", " ", "security\n600000", 1, True, None):
        with pytest.raises(ValueError):
            _normalize(stable_security_id=value)
    for value in ("a" * 63, "A" * 64, "g" * 64, 1, True, None):
        with pytest.raises(ValueError):
            _normalize(pit_security_descriptor_sha256=value)


def test_accepts_only_supported_a_share_board_and_code_pairs() -> None:
    cases = (
        ("SSE_MAIN", "600000.SH"),
        ("SZSE_MAIN", "000001.SZ"),
        ("SZSE_CHINEXT", "300001.SZ"),
        ("SZSE_CHINEXT", "302001.SZ"),
    )
    for board, ts_code in cases:
        receipt = _normalize(
            _payload(ts_code=ts_code),
            board=board,
            ts_code=ts_code,
            stable_security_id=f"security-{ts_code}",
        )
        assert receipt.board == board
        assert receipt.requested_ts_code == ts_code


def test_rejects_star_bse_b_shares_funds_cdr_and_board_code_mismatch() -> None:
    cases = (
        ("SSE_STAR", "688001.SH"),
        ("BSE", "830001.BJ"),
        ("SSE_MAIN", "900901.SH"),
        ("SZSE_MAIN", "200001.SZ"),
        ("SSE_MAIN", "510300.SH"),
        ("SZSE_MAIN", "159919.SZ"),
        ("SSE_MAIN", "689009.SH"),
        ("SSE_MAIN", "000001.SZ"),
        ("SZSE_MAIN", "300001.SZ"),
        ("SZSE_CHINEXT", "002001.SZ"),
        ("SSE_MAIN", "600000"),
    )
    for board, ts_code in cases:
        with pytest.raises(ValueError):
            _normalize(
                _payload(ts_code=ts_code),
                board=board,
                ts_code=ts_code,
            )
    for board in ("sse_main", "", 1, True, None):
        with pytest.raises(ValueError):
            _normalize(board=board)


def test_requires_exact_unique_ascending_241_label_schedule_without_fill() -> None:
    invalid_payloads = []
    missing = _payload()
    missing["data"]["items"].pop(100)
    invalid_payloads.append(missing)
    extra = _payload()
    extra["data"]["items"].append(copy.deepcopy(extra["data"]["items"][-1]))
    invalid_payloads.append(extra)
    duplicate = _payload()
    duplicate["data"]["items"][100][1] = duplicate["data"]["items"][99][1]
    invalid_payloads.append(duplicate)
    out_of_order = _payload()
    out_of_order["data"]["items"][99], out_of_order["data"]["items"][100] = (
        out_of_order["data"]["items"][100],
        out_of_order["data"]["items"][99],
    )
    invalid_payloads.append(out_of_order)
    lunch = _payload()
    lunch["data"]["items"][121][1] = "2026-07-28 13:00:00"
    invalid_payloads.append(lunch)
    after_close = _payload()
    after_close["data"]["items"][-1][1] = "2026-07-28 15:01:00"
    invalid_payloads.append(after_close)

    for invalid in invalid_payloads:
        with pytest.raises(ValueError):
            _normalize(invalid)


def test_phase_mapping_is_end_label_based_at_closing_boundary() -> None:
    rows = _normalize().normalized_minutes
    phases = {
        row.source_label_at.strftime("%H:%M"): row.minute_amount.market_phase
        for row in rows
        if row.source_label_at.strftime("%H:%M") in {"14:57", "14:58", "15:00"}
    }
    assert phases == {
        "14:57": "continuous_auction",
        "14:58": "closing_call_auction",
        "15:00": "closing_call_auction",
    }


def test_synthetic_normalization_never_claims_source_or_production_authority() -> None:
    receipt = _normalize()
    assert receipt.source_authority_status == "UNBOUND"
    assert receipt.minute_amount_authority_status == "UNBOUND"
    assert receipt.verification_status == "SCHEMA_NORMALIZED_NOT_SOURCE_VERIFIED"
    assert receipt.production_effect == "NONE"
    assert receipt.verified is False
    assert receipt.rows_published is False
    assert receipt.embargo_consumed is False
    assert receipt.final_oos_consumed is False
    assert receipt.production_profile_registered is False
    assert receipt.production_recommendation_eligible is False
    serialized = asdict(receipt)
    assert serialized["source_authority_status"] != "BOUND"
    assert serialized["verification_status"] != "GREEN"
