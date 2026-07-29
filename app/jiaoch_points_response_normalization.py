"""Pure, fail-closed previews of Jiaoch points-interface responses."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import hmac
import json
import math
import re
from typing import Any


__all__ = [
    "JiaochPointsNormalizationPreviewReceipt",
    "NormalizedDailyBasicRow",
    "NormalizedMoneyflowRow",
    "normalize_jiaoch_points_response_preview",
]


_SCHEMA = "jiaoch-points-response-normalization-preview/v1"
_NORMALIZATION_STATUS = "SOURCE_RESPONSE_NORMALIZED_UNBOUND"
_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_LOWER_HEX = frozenset("0123456789abcdef")
_DAILY_BASIC_FIELDS = (
    "ts_code",
    "trade_date",
    "turnover_rate",
    "turnover_rate_f",
    "free_share",
    "float_share",
    "total_mv",
    "circ_mv",
)
_MONEYFLOW_FIELDS = (
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
)
_FIELDS_BY_API = {
    "daily_basic": _DAILY_BASIC_FIELDS,
    "moneyflow": _MONEYFLOW_FIELDS,
}
_FIELD_UNITS_BY_API = {
    "daily_basic": (
        ("turnover_rate", "percent"),
        ("turnover_rate_f", "percent"),
        ("free_share", "ten_thousand_shares"),
        ("float_share", "ten_thousand_shares"),
        ("total_mv", "ten_thousand_cny"),
        ("circ_mv", "ten_thousand_cny"),
    ),
    "moneyflow": tuple(
        (
            field,
            "lot" if field.endswith("_vol") else "ten_thousand_cny",
        )
        for field in _MONEYFLOW_FIELDS[2:]
    ),
}
_SOURCE_SEMANTICS_BY_API = {
    "daily_basic": (
        ("permission_model", "points-interface"),
        ("response_grain", "single_trade_date_cross_section"),
    ),
    "moneyflow": (
        ("permission_model", "points-interface"),
        ("response_grain", "single_trade_date_cross_section"),
        (
            "net_mf",
            "source_native_l2_active_buy_sell_not_bucket_recomputed",
        ),
    ),
}
_MARKET_SEGMENT_PATTERNS = (
    ("SSE_MAIN", re.compile(r"(?:600|601|603|605)[0-9]{3}\.SH")),
    ("SSE_STAR", re.compile(r"(?:688|689)[0-9]{3}\.SH")),
    ("SZSE_MAIN", re.compile(r"(?:000|001|002|003)[0-9]{3}\.SZ")),
    ("SZSE_CHINEXT", re.compile(r"(?:300|301|302)[0-9]{3}\.SZ")),
    ("BSE", re.compile(r"(?:4|8|9)[0-9]{5}\.BJ")),
)
_TARGET_MARKET_SEGMENTS = frozenset({"SSE_MAIN", "SZSE_MAIN", "SZSE_CHINEXT"})
_SECRET_MARKER = re.compile(
    r"(?:api[\s_-]*key|authorization|bearer|credential|fingerprint|secret|token)",
    re.IGNORECASE,
)
_HEX_DIGEST = re.compile(r"(?:sha(?:1|224|256|384|512)[\s:_-]*)?[0-9a-f]{32,128}", re.I)
_BASE64_DIGEST = re.compile(r"[A-Za-z0-9+/_-]{43}={0,2}")


@dataclass(frozen=True)
class NormalizedDailyBasicRow:
    ts_code: str
    trade_date: str
    turnover_rate: float
    turnover_rate_f: float
    free_share: float
    float_share: float
    total_mv: float
    circ_mv: float
    market_segment: str
    research_scope_mainboard_chinext: bool


@dataclass(frozen=True)
class NormalizedMoneyflowRow:
    ts_code: str
    trade_date: str
    buy_sm_vol: float
    buy_sm_amount: float
    sell_sm_vol: float
    sell_sm_amount: float
    buy_md_vol: float
    buy_md_amount: float
    sell_md_vol: float
    sell_md_amount: float
    buy_lg_vol: float
    buy_lg_amount: float
    sell_lg_vol: float
    sell_lg_amount: float
    buy_elg_vol: float
    buy_elg_amount: float
    sell_elg_vol: float
    sell_elg_amount: float
    net_mf_vol: float
    net_mf_amount: float
    market_segment: str
    research_scope_mainboard_chinext: bool


NormalizedPointsRow = NormalizedDailyBasicRow | NormalizedMoneyflowRow


@dataclass(frozen=True)
class JiaochPointsNormalizationPreviewReceipt:
    schema: str
    normalization_status: str
    api_name: str
    trade_date: str
    raw_response_sha256: str
    source_fields: tuple[str, ...]
    field_units: tuple[tuple[str, str], ...]
    source_semantics: tuple[tuple[str, str], ...]
    source_row_count: int
    row_sort_order: tuple[str, str]
    canonical_rows_sha256: str
    preview_receipt_sha256: str
    rows: tuple[NormalizedPointsRow, ...]
    cross_section_completeness_verified: bool
    collection_set_verified: bool
    row_authority_status: str
    formal_materialization_eligible: bool
    source_authority_verified: bool
    rows_published: bool
    experiment_launch_eligible: bool
    embargo_consumed: bool
    final_oos_consumed: bool
    production_profile_registered: bool
    production_recommendation_eligible: bool


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError):
        raise ValueError("Jiaoch points normalization canonical JSON rejected") from None


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _strict_json_loads(raw: bytes) -> Any:
    def object_pairs(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("duplicate JSON key")
            output[key] = value
        return output

    def reject_constant(_value):
        raise ValueError("non-finite JSON constant")

    try:
        return json.loads(
            raw,
            object_pairs_hook=object_pairs,
            parse_float=_finite_json_float,
            parse_constant=reject_constant,
        )
    except (
        OverflowError,
        RecursionError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        raise ValueError("Jiaoch points response strict JSON rejected") from None


def _reject_secret_material(value: Any) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if type(current) is dict:
            pending.extend(current.values())
            pending.extend(current.keys())
            continue
        if type(current) is list:
            pending.extend(current)
            continue
        if type(current) is not str:
            continue
        if (
            _SECRET_MARKER.search(current)
            or _HEX_DIGEST.fullmatch(current)
            or _BASE64_DIGEST.fullmatch(current)
        ):
            raise ValueError("Jiaoch points response secret-shaped material rejected")


def _api_name(value: Any) -> str:
    if type(value) is not str or value not in _FIELDS_BY_API:
        raise ValueError("Jiaoch points expected api_name rejected")
    return value


def _trade_date(value: Any) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9]{8}", value) is None:
        raise ValueError("Jiaoch points expected trade_date rejected")
    try:
        parsed = datetime.strptime(value, "%Y%m%d")
    except ValueError:
        raise ValueError("Jiaoch points expected trade_date rejected") from None
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError("Jiaoch points expected trade_date rejected")
    return value


def _expected_sha256(value: Any) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in _LOWER_HEX for character in value)
    ):
        raise ValueError("Jiaoch points expected raw_sha256 rejected")
    return value


def _market_segment(value: Any) -> tuple[str, bool]:
    if type(value) is not str:
        raise ValueError("Jiaoch points row ts_code rejected")
    for segment, pattern in _MARKET_SEGMENT_PATTERNS:
        if pattern.fullmatch(value) is not None:
            return segment, segment in _TARGET_MARKET_SEGMENTS
    raise ValueError("Jiaoch points row ts_code rejected")


def _number(value: Any, *, nonnegative: bool) -> float:
    if type(value) not in (int, float):
        raise ValueError("Jiaoch points row number rejected")
    try:
        normalized = float(value)
    except (OverflowError, ValueError):
        raise ValueError("Jiaoch points row number rejected") from None
    if not math.isfinite(normalized) or (nonnegative and normalized < 0.0):
        raise ValueError("Jiaoch points row number rejected")
    return 0.0 if normalized == 0.0 else normalized


def _daily_basic_row(
    row: Any,
    *,
    expected_trade_date: str,
) -> NormalizedDailyBasicRow:
    if type(row) is not list or len(row) != len(_DAILY_BASIC_FIELDS):
        raise ValueError("Jiaoch daily_basic row width rejected")
    segment, in_scope = _market_segment(row[0])
    if type(row[1]) is not str or row[1] != expected_trade_date:
        raise ValueError("Jiaoch daily_basic row trade_date rejected")
    numbers = tuple(_number(value, nonnegative=True) for value in row[2:])
    return NormalizedDailyBasicRow(
        ts_code=row[0],
        trade_date=row[1],
        turnover_rate=numbers[0],
        turnover_rate_f=numbers[1],
        free_share=numbers[2],
        float_share=numbers[3],
        total_mv=numbers[4],
        circ_mv=numbers[5],
        market_segment=segment,
        research_scope_mainboard_chinext=in_scope,
    )


def _moneyflow_row(
    row: Any,
    *,
    expected_trade_date: str,
) -> NormalizedMoneyflowRow:
    if type(row) is not list or len(row) != len(_MONEYFLOW_FIELDS):
        raise ValueError("Jiaoch moneyflow row width rejected")
    segment, in_scope = _market_segment(row[0])
    if type(row[1]) is not str or row[1] != expected_trade_date:
        raise ValueError("Jiaoch moneyflow row trade_date rejected")
    buckets = tuple(_number(value, nonnegative=True) for value in row[2:18])
    net_values = tuple(_number(value, nonnegative=False) for value in row[18:])
    return NormalizedMoneyflowRow(
        ts_code=row[0],
        trade_date=row[1],
        buy_sm_vol=buckets[0],
        buy_sm_amount=buckets[1],
        sell_sm_vol=buckets[2],
        sell_sm_amount=buckets[3],
        buy_md_vol=buckets[4],
        buy_md_amount=buckets[5],
        sell_md_vol=buckets[6],
        sell_md_amount=buckets[7],
        buy_lg_vol=buckets[8],
        buy_lg_amount=buckets[9],
        sell_lg_vol=buckets[10],
        sell_lg_amount=buckets[11],
        buy_elg_vol=buckets[12],
        buy_elg_amount=buckets[13],
        sell_elg_vol=buckets[14],
        sell_elg_amount=buckets[15],
        net_mf_vol=net_values[0],
        net_mf_amount=net_values[1],
        market_segment=segment,
        research_scope_mainboard_chinext=in_scope,
    )


def _normalized_rows(
    items: Any,
    *,
    api_name: str,
    trade_date: str,
) -> tuple[NormalizedPointsRow, ...]:
    if type(items) is not list:
        raise ValueError("Jiaoch points response items rejected")
    normalize_row = _daily_basic_row if api_name == "daily_basic" else _moneyflow_row
    rows = tuple(
        normalize_row(row, expected_trade_date=trade_date)
        for row in items
    )
    identities = [(row.ts_code, row.trade_date) for row in rows]
    if len(identities) != len(set(identities)):
        raise ValueError("Jiaoch points response duplicate row identity rejected")
    return tuple(sorted(rows, key=lambda row: (row.trade_date, row.ts_code)))


def _strict_response_data(
    payload: Any,
    *,
    api_name: str,
) -> tuple[Any, tuple[str, ...]]:
    if type(payload) is not dict or set(payload) != {"code", "data", "msg"}:
        raise ValueError("Jiaoch points response envelope rejected")
    if type(payload["code"]) is not int or payload["code"] != 0:
        raise ValueError("Jiaoch points response success code rejected")
    if type(payload["msg"]) is not str or payload["msg"] != "success":
        raise ValueError("Jiaoch points response success message rejected")
    data = payload["data"]
    if type(data) is not dict or set(data) != {"fields", "items"}:
        raise ValueError("Jiaoch points response data envelope rejected")
    expected_fields = _FIELDS_BY_API[api_name]
    if type(data["fields"]) is not list or data["fields"] != list(expected_fields):
        raise ValueError("Jiaoch points response field contract rejected")
    return data["items"], expected_fields


def _preview_descriptor(
    *,
    api_name: str,
    trade_date: str,
    raw_response_sha256: str,
    source_fields: tuple[str, ...],
    field_units: tuple[tuple[str, str], ...],
    source_semantics: tuple[tuple[str, str], ...],
    source_row_count: int,
    canonical_rows_sha256: str,
) -> dict[str, Any]:
    return {
        "api_name": api_name,
        "canonical_rows_sha256": canonical_rows_sha256,
        "collection_set_verified": False,
        "cross_section_completeness_verified": False,
        "embargo_consumed": False,
        "experiment_launch_eligible": False,
        "field_units": [list(item) for item in field_units],
        "final_oos_consumed": False,
        "formal_materialization_eligible": False,
        "normalization_status": _NORMALIZATION_STATUS,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "raw_response_sha256": raw_response_sha256,
        "row_authority_status": "NOT_GRANTED",
        "row_sort_order": ["trade_date", "ts_code"],
        "rows_published": False,
        "schema": _SCHEMA,
        "source_authority_verified": False,
        "source_fields": list(source_fields),
        "source_row_count": source_row_count,
        "source_semantics": [list(item) for item in source_semantics],
        "trade_date": trade_date,
    }


def normalize_jiaoch_points_response_preview(
    *,
    response_body: bytes,
    expected_api_name: str,
    expected_trade_date: str,
    expected_raw_sha256: str,
) -> JiaochPointsNormalizationPreviewReceipt:
    """Normalize an opaque response without granting collection or row authority."""

    if (
        type(response_body) is not bytes
        or not response_body
        or len(response_body) > _MAX_RESPONSE_BYTES
    ):
        raise ValueError("Jiaoch points response body rejected")
    api_name = _api_name(expected_api_name)
    trade_date = _trade_date(expected_trade_date)
    expected_digest = _expected_sha256(expected_raw_sha256)
    actual_digest = hashlib.sha256(response_body).hexdigest()
    if not hmac.compare_digest(actual_digest, expected_digest):
        raise ValueError("Jiaoch points response raw_sha256 mismatch")

    payload = _strict_json_loads(response_body)
    _reject_secret_material(payload)
    items, source_fields = _strict_response_data(payload, api_name=api_name)
    rows = _normalized_rows(items, api_name=api_name, trade_date=trade_date)
    canonical_rows_sha256 = hashlib.sha256(
        _canonical_json([asdict(row) for row in rows])
    ).hexdigest()
    field_units = _FIELD_UNITS_BY_API[api_name]
    source_semantics = _SOURCE_SEMANTICS_BY_API[api_name]
    preview_receipt_sha256 = hashlib.sha256(
        _canonical_json(
            _preview_descriptor(
                api_name=api_name,
                trade_date=trade_date,
                raw_response_sha256=actual_digest,
                source_fields=source_fields,
                field_units=field_units,
                source_semantics=source_semantics,
                source_row_count=len(rows),
                canonical_rows_sha256=canonical_rows_sha256,
            )
        )
    ).hexdigest()
    return JiaochPointsNormalizationPreviewReceipt(
        schema=_SCHEMA,
        normalization_status=_NORMALIZATION_STATUS,
        api_name=api_name,
        trade_date=trade_date,
        raw_response_sha256=actual_digest,
        source_fields=source_fields,
        field_units=field_units,
        source_semantics=source_semantics,
        source_row_count=len(rows),
        row_sort_order=("trade_date", "ts_code"),
        canonical_rows_sha256=canonical_rows_sha256,
        preview_receipt_sha256=preview_receipt_sha256,
        rows=rows,
        cross_section_completeness_verified=False,
        collection_set_verified=False,
        row_authority_status="NOT_GRANTED",
        formal_materialization_eligible=False,
        source_authority_verified=False,
        rows_published=False,
        experiment_launch_eligible=False,
        embargo_consumed=False,
        final_oos_consumed=False,
        production_profile_registered=False,
        production_recommendation_eligible=False,
    )
