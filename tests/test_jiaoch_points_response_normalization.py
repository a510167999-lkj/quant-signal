from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
import hashlib
import inspect
import json
import math

import pytest

from app.jiaoch_points_response_normalization import (
    JiaochPointsNormalizationPreviewReceipt,
    NormalizedDailyBasicRow,
    NormalizedMoneyflowRow,
    normalize_jiaoch_points_response_preview,
)


TRADE_DATE = "20260728"
DAILY_BASIC_FIELDS = [
    "ts_code",
    "trade_date",
    "turnover_rate",
    "turnover_rate_f",
    "free_share",
    "float_share",
    "total_mv",
    "circ_mv",
]
MONEYFLOW_FIELDS = [
    "ts_code",
    "trade_date",
    "buy_sm_vol",
    "buy_sm_amount",
    "sell_sm_vol",
    "sell_sm_amount",
    "buy_md_vol",
    "buy_md_amount",
    "sell_md_vol",
    "sell_md_amount",
    "buy_lg_vol",
    "buy_lg_amount",
    "sell_lg_vol",
    "sell_lg_amount",
    "buy_elg_vol",
    "buy_elg_amount",
    "sell_elg_vol",
    "sell_elg_amount",
    "net_mf_vol",
    "net_mf_amount",
]
_UNSET = object()


def _daily_row(
    ts_code: object = "600000.SH",
    *,
    trade_date: object = TRADE_DATE,
    turnover_rate: object = 1.25,
    turnover_rate_f: object = 2.5,
    free_share: object = 120_000.0,
    float_share: object = 150_000.0,
    total_mv: object = 2_000_000.0,
    circ_mv: object = 1_500_000.0,
) -> list[object]:
    return [
        ts_code,
        trade_date,
        turnover_rate,
        turnover_rate_f,
        free_share,
        float_share,
        total_mv,
        circ_mv,
    ]


def _moneyflow_row(
    ts_code: object = "600000.SH",
    *,
    trade_date: object = TRADE_DATE,
) -> list[object]:
    return [
        ts_code,
        trade_date,
        100.0,
        1.0,
        90.0,
        0.9,
        80.0,
        0.8,
        70.0,
        0.7,
        60.0,
        0.6,
        50.0,
        0.5,
        40.0,
        0.4,
        30.0,
        0.3,
        -123.0,
        -4.5,
    ]


def _payload(api_name: str, rows: list[list[object]] | None = None) -> dict[str, object]:
    fields = DAILY_BASIC_FIELDS if api_name == "daily_basic" else MONEYFLOW_FIELDS
    default_row = _daily_row() if api_name == "daily_basic" else _moneyflow_row()
    return {
        "code": 0,
        "data": {
            "fields": list(fields),
            "items": [default_row] if rows is None else rows,
        },
        "msg": "success",
    }


def _raw(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=True,
    ).encode("utf-8")


def _normalize(
    api_name: str = "daily_basic",
    *,
    payload: object | None = None,
    response_body: object | None = None,
    expected_trade_date: object = TRADE_DATE,
    expected_raw_sha256: object = _UNSET,
) -> JiaochPointsNormalizationPreviewReceipt:
    body = _raw(_payload(api_name) if payload is None else payload)
    if response_body is not None:
        body = response_body
    digest = hashlib.sha256(body).hexdigest() if type(body) is bytes else "a" * 64
    return normalize_jiaoch_points_response_preview(
        response_body=body,
        expected_api_name=api_name,
        expected_trade_date=expected_trade_date,
        expected_raw_sha256=digest if expected_raw_sha256 is _UNSET else expected_raw_sha256,
    )


def test_daily_basic_normalizes_official_fields_units_and_unbound_receipt() -> None:
    receipt = _normalize()

    assert isinstance(receipt, JiaochPointsNormalizationPreviewReceipt)
    assert receipt.schema == "jiaoch-points-response-normalization-preview/v1"
    assert receipt.normalization_status == "SOURCE_RESPONSE_NORMALIZED_UNBOUND"
    assert receipt.api_name == "daily_basic"
    assert receipt.trade_date == TRADE_DATE
    assert receipt.source_fields == tuple(DAILY_BASIC_FIELDS)
    assert receipt.field_units == (
        ("turnover_rate", "percent"),
        ("turnover_rate_f", "percent"),
        ("free_share", "ten_thousand_shares"),
        ("float_share", "ten_thousand_shares"),
        ("total_mv", "ten_thousand_cny"),
        ("circ_mv", "ten_thousand_cny"),
    )
    assert receipt.source_semantics == (
        ("permission_model", "points-interface"),
        ("response_grain", "single_trade_date_cross_section"),
    )
    assert receipt.source_row_count == 1
    row = receipt.rows[0]
    assert isinstance(row, NormalizedDailyBasicRow)
    assert row == NormalizedDailyBasicRow(
        ts_code="600000.SH",
        trade_date=TRADE_DATE,
        turnover_rate=1.25,
        turnover_rate_f=2.5,
        free_share=120_000.0,
        float_share=150_000.0,
        total_mv=2_000_000.0,
        circ_mv=1_500_000.0,
        market_segment="SSE_MAIN",
        research_scope_mainboard_chinext=True,
    )
    assert receipt.raw_response_sha256 == hashlib.sha256(
        _raw(_payload("daily_basic"))
    ).hexdigest()
    assert len(receipt.canonical_rows_sha256) == 64
    assert len(receipt.preview_receipt_sha256) == 64


def test_moneyflow_freezes_lot_and_ten_thousand_cny_units_without_recomputing_net() -> None:
    receipt = _normalize("moneyflow")

    assert receipt.field_units == tuple(
        (field, "lot" if field.endswith("_vol") else "ten_thousand_cny")
        for field in MONEYFLOW_FIELDS[2:]
    )
    assert receipt.source_semantics == (
        ("permission_model", "points-interface"),
        ("response_grain", "single_trade_date_cross_section"),
        (
            "net_mf",
            "source_native_l2_active_buy_sell_not_bucket_recomputed",
        ),
    )
    row = receipt.rows[0]
    assert isinstance(row, NormalizedMoneyflowRow)
    assert row.net_mf_vol == -123.0
    assert row.net_mf_amount == -4.5
    assert row.net_mf_vol != (
        row.buy_sm_vol
        + row.buy_md_vol
        + row.buy_lg_vol
        + row.buy_elg_vol
        - row.sell_sm_vol
        - row.sell_md_vol
        - row.sell_lg_vol
        - row.sell_elg_vol
    )


def test_preserves_main_chinext_star_and_bse_rows_but_marks_only_target_scope() -> None:
    rows = [
        _daily_row("830001.BJ"),
        _daily_row("688001.SH"),
        _daily_row("300001.SZ"),
        _daily_row("600000.SH"),
    ]

    receipt = _normalize(payload=_payload("daily_basic", rows))

    assert [row.ts_code for row in receipt.rows] == [
        "300001.SZ",
        "600000.SH",
        "688001.SH",
        "830001.BJ",
    ]
    assert [row.market_segment for row in receipt.rows] == [
        "SZSE_CHINEXT",
        "SSE_MAIN",
        "SSE_STAR",
        "BSE",
    ]
    assert [row.research_scope_mainboard_chinext for row in receipt.rows] == [
        True,
        True,
        False,
        False,
    ]
    assert receipt.source_row_count == 4


def test_row_sorting_and_canonical_rows_hash_are_independent_of_source_order() -> None:
    ascending = [_daily_row("300001.SZ"), _daily_row("600000.SH")]
    descending = list(reversed(ascending))

    first = _normalize(payload=_payload("daily_basic", ascending))
    second = _normalize(payload=_payload("daily_basic", descending))

    assert first.rows == second.rows
    assert first.canonical_rows_sha256 == second.canonical_rows_sha256
    expected_rows_bytes = json.dumps(
        [asdict(row) for row in first.rows],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert first.canonical_rows_sha256 == hashlib.sha256(expected_rows_bytes).hexdigest()
    assert first.raw_response_sha256 != second.raw_response_sha256
    assert first.preview_receipt_sha256 != second.preview_receipt_sha256


def test_preview_receipt_never_claims_collection_row_or_production_authority() -> None:
    receipt = _normalize()

    assert receipt.cross_section_completeness_verified is False
    assert receipt.collection_set_verified is False
    assert receipt.row_authority_status == "NOT_GRANTED"
    assert receipt.formal_materialization_eligible is False
    assert receipt.source_authority_verified is False
    assert receipt.rows_published is False
    assert receipt.experiment_launch_eligible is False
    assert receipt.embargo_consumed is False
    assert receipt.final_oos_consumed is False
    assert receipt.production_profile_registered is False
    assert receipt.production_recommendation_eligible is False


def test_public_entrypoint_has_no_token_transport_callback_or_collection_context() -> None:
    parameters = inspect.signature(normalize_jiaoch_points_response_preview).parameters

    assert tuple(parameters) == (
        "response_body",
        "expected_api_name",
        "expected_trade_date",
        "expected_raw_sha256",
    )
    assert not {
        "token",
        "credential",
        "transport",
        "callback",
        "collection_set",
    }.intersection(parameters)


def test_output_rows_and_receipt_are_frozen() -> None:
    receipt = _normalize()

    with pytest.raises(FrozenInstanceError):
        receipt.rows[0].turnover_rate = 9.0
    with pytest.raises(FrozenInstanceError):
        receipt.row_authority_status = "GRANTED"


@pytest.mark.parametrize(
    "response_body",
    [
        b'{"code":0,"code":0,"data":{"fields":[],"items":[]},"msg":"success"}',
        b'{"code":0,"data":{"fields":[],"items":[]},"msg":NaN}',
        b'{"code":0,"data":{"fields":[],"items":[]},"msg":Infinity}',
        b'{"code":0,"data":{"fields":[],"items":[]},"msg":1e999}',
    ],
)
def test_strict_json_rejects_duplicate_keys_nan_and_infinity(response_body: bytes) -> None:
    with pytest.raises(ValueError):
        _normalize(response_body=response_body)


def test_requires_exact_success_envelope_and_data_keys() -> None:
    valid = _payload("daily_basic")
    invalid_payloads: list[object] = []
    for key in ("code", "data", "msg"):
        candidate = json.loads(json.dumps(valid))
        candidate.pop(key)
        invalid_payloads.append(candidate)
    candidate = json.loads(json.dumps(valid))
    candidate["unknown"] = None
    invalid_payloads.append(candidate)
    for value in (True, 1, 0.0, "0", None):
        candidate = json.loads(json.dumps(valid))
        candidate["code"] = value
        invalid_payloads.append(candidate)
    for value in ("ok", "", True, None):
        candidate = json.loads(json.dumps(valid))
        candidate["msg"] = value
        invalid_payloads.append(candidate)
    for value in (None, [], "data"):
        candidate = json.loads(json.dumps(valid))
        candidate["data"] = value
        invalid_payloads.append(candidate)
    for key in ("fields", "items"):
        candidate = json.loads(json.dumps(valid))
        candidate["data"].pop(key)
        invalid_payloads.append(candidate)
    candidate = json.loads(json.dumps(valid))
    candidate["data"]["unknown"] = None
    invalid_payloads.append(candidate)

    for payload in invalid_payloads:
        with pytest.raises(ValueError):
            _normalize(payload=payload)


def test_rejects_field_order_drift_unknown_fields_and_row_width_drift() -> None:
    invalid_payloads = []
    reversed_fields = _payload("daily_basic")
    reversed_fields["data"]["fields"].reverse()
    invalid_payloads.append(reversed_fields)
    unknown_field = _payload("daily_basic")
    unknown_field["data"]["fields"][-1] = "unknown"
    invalid_payloads.append(unknown_field)
    short = _payload("daily_basic")
    short["data"]["items"][0].pop()
    invalid_payloads.append(short)
    long = _payload("daily_basic")
    long["data"]["items"][0].append(1.0)
    invalid_payloads.append(long)
    mapping_row = _payload("daily_basic")
    mapping_row["data"]["items"][0] = {"ts_code": "600000.SH"}
    invalid_payloads.append(mapping_row)

    for payload in invalid_payloads:
        with pytest.raises(ValueError):
            _normalize(payload=payload)


def test_rejects_duplicate_identity_and_non_target_trade_date() -> None:
    duplicate = _payload(
        "daily_basic",
        [_daily_row("600000.SH"), _daily_row("600000.SH")],
    )
    wrong_date = _payload("daily_basic", [_daily_row(trade_date="20260727")])

    with pytest.raises(ValueError):
        _normalize(payload=duplicate)
    with pytest.raises(ValueError):
        _normalize(payload=wrong_date)


@pytest.mark.parametrize(
    "ts_code",
    [
        "510300.SH",
        "159919.SZ",
        "900901.SH",
        "200001.SZ",
        "600000.SZ",
        "000001.SH",
        "600000",
        "60000.SH",
        "ABCDEF.SH",
        "",
        600000,
        True,
        None,
    ],
)
def test_rejects_illegal_non_a_share_or_mismatched_ts_code(ts_code: object) -> None:
    with pytest.raises(ValueError):
        _normalize(payload=_payload("daily_basic", [_daily_row(ts_code)]))


@pytest.mark.parametrize("field_index", range(2, 8))
@pytest.mark.parametrize(
    "value",
    [-0.1, math.nan, math.inf, -math.inf, True, False, "1.0", None],
)
def test_daily_basic_metrics_are_finite_nonnegative_numbers(
    field_index: int,
    value: object,
) -> None:
    payload = _payload("daily_basic")
    payload["data"]["items"][0][field_index] = value

    with pytest.raises(ValueError):
        _normalize(payload=payload)


def test_daily_basic_accepts_json_integer_numbers_and_normalizes_to_float() -> None:
    receipt = _normalize(
        payload=_payload(
            "daily_basic",
            [[
                "600000.SH",
                TRADE_DATE,
                1,
                2,
                120_000,
                150_000,
                2_000_000,
                1_500_000,
            ]],
        )
    )

    assert all(
        type(value) is float
        for value in (
            receipt.rows[0].turnover_rate,
            receipt.rows[0].turnover_rate_f,
            receipt.rows[0].free_share,
            receipt.rows[0].float_share,
            receipt.rows[0].total_mv,
            receipt.rows[0].circ_mv,
        )
    )


@pytest.mark.parametrize("field_index", range(2, 18))
@pytest.mark.parametrize("value", [-0.1, math.nan, math.inf, True, "1.0", None])
def test_moneyflow_bucket_values_are_finite_nonnegative_numbers(
    field_index: int,
    value: object,
) -> None:
    payload = _payload("moneyflow")
    payload["data"]["items"][0][field_index] = value

    with pytest.raises(ValueError):
        _normalize("moneyflow", payload=payload)


@pytest.mark.parametrize("field_index", (18, 19))
@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, True, "1.0", None])
def test_moneyflow_net_values_are_finite_signed_numbers(
    field_index: int,
    value: object,
) -> None:
    payload = _payload("moneyflow")
    payload["data"]["items"][0][field_index] = value

    with pytest.raises(ValueError):
        _normalize("moneyflow", payload=payload)


def test_recomputes_and_requires_exact_lowercase_raw_sha256() -> None:
    body = _raw(_payload("daily_basic"))
    actual = hashlib.sha256(body).hexdigest()

    assert _normalize(response_body=body, expected_raw_sha256=actual).raw_response_sha256 == actual
    for value in ("0" * 64, actual.upper(), actual[:-1], "g" * 64, 1, True, None):
        with pytest.raises(ValueError):
            _normalize(response_body=body, expected_raw_sha256=value)


@pytest.mark.parametrize(
    ("api_name", "trade_date"),
    [
        ("unknown", TRADE_DATE),
        ("DAILY_BASIC", TRADE_DATE),
        (True, TRADE_DATE),
        ("daily_basic", "2026-07-28"),
        ("daily_basic", "20260230"),
        ("daily_basic", 20260728),
        ("daily_basic", True),
    ],
)
def test_expected_api_name_and_trade_date_are_exact_closed_bindings(
    api_name: object,
    trade_date: object,
) -> None:
    body = _raw(_payload("daily_basic"))
    with pytest.raises(ValueError):
        normalize_jiaoch_points_response_preview(
            response_body=body,
            expected_api_name=api_name,
            expected_trade_date=trade_date,
            expected_raw_sha256=hashlib.sha256(body).hexdigest(),
        )


@pytest.mark.parametrize("response_body", ["{}", bytearray(b"{}"), memoryview(b"{}"), b""])
def test_response_body_requires_nonempty_bytes(response_body: object) -> None:
    with pytest.raises(ValueError):
        _normalize(response_body=response_body)


@pytest.mark.parametrize(
    "secret_key",
    [
        "token",
        "api_key",
        "credential",
        "authorization",
        "token_hash",
        "credential_fingerprint",
    ],
)
def test_rejects_secret_plaintext_hash_or_fingerprint_shapes_without_echo(
    secret_key: str,
) -> None:
    secret = "do-not-echo-this-secret"
    payload = _payload("daily_basic")
    payload[secret_key] = secret if not secret_key.endswith("hash") else "a" * 64

    with pytest.raises(ValueError) as exc_info:
        _normalize(payload=payload)
    assert secret not in str(exc_info.value)
    assert "a" * 64 not in str(exc_info.value)


def test_rejects_hash_and_fingerprint_strings_even_when_nested() -> None:
    bodies = [
        (
            b'{"code":0,"data":{"fields":'
            + json.dumps(DAILY_BASIC_FIELDS).encode()
            + b',"items":[]},"msg":"'
            + b"a" * 64
            + b'"}'
        ),
        (
            b'{"code":0,"data":{"fields":'
            + json.dumps(DAILY_BASIC_FIELDS).encode()
            + b',"items":[]},"msg":"sha256:'
            + b"a" * 64
            + b'"}'
        ),
        (
            b'{"code":0,"data":{"fields":'
            + json.dumps(DAILY_BASIC_FIELDS).encode()
            + b',"items":[]},"msg":"credential_fingerprint:opaque"}'
        ),
    ]

    for body in bodies:
        with pytest.raises(ValueError) as exc_info:
            _normalize(response_body=body)
        assert "a" * 64 not in str(exc_info.value)
