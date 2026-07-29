"""Pure, fail-closed normalization for successful Jiaoch ``stk_mins`` responses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import hashlib
import math
from typing import Any
import re
from zoneinfo import ZoneInfo

from app.factor_v2_execution_stress_primitives import MinuteAmount
from app.jiaoch_historical_minute_source import (
    _contains_semantic_token as _contains_source_token,
)
from app.jiaoch_historical_minute_source import (
    _strict_json_loads as _strict_source_json_loads,
)


__all__ = [
    "VWAP_ABSOLUTE_TOLERANCE_CNY",
    "JiaochStkMinsNormalizationReceipt",
    "NormalizedStkMinsMinute",
    "OpeningSpecialUnresolved",
    "normalize_jiaoch_stk_mins_success_response",
]


VWAP_ABSOLUTE_TOLERANCE_CNY = 1e-6
_SCHEMA = "jiaoch-stk-mins-schema-normalization/v1"
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
_SUPPORTED_BOARD_PATTERNS = {
    "SSE_MAIN": re.compile(r"(?:600|601|603|605)[0-9]{3}\.SH"),
    "SZSE_MAIN": re.compile(r"(?:000|001|002|003)[0-9]{3}\.SZ"),
    "SZSE_CHINEXT": re.compile(r"(?:300|301)[0-9]{3}\.SZ"),
}
_MAX_RESPONSE_BYTES = 1024 * 1024
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class OpeningSpecialUnresolved:
    stable_security_id: str
    execution_session: date
    source_ts_code: str
    source_label_at: datetime
    open_price_cny: float
    close_price_cny: float
    high_price_cny: float
    low_price_cny: float
    volume_shares: int
    amount_cny: float
    resolution_status: str
    interval_start_at: None
    interval_end_at: None
    eligible_for_capacity: bool


@dataclass(frozen=True)
class NormalizedStkMinsMinute:
    source_ts_code: str
    source_label_at: datetime
    open_price_cny: float
    close_price_cny: float
    high_price_cny: float
    low_price_cny: float
    volume_shares: int
    amount_cny: float
    minute_amount: MinuteAmount


@dataclass(frozen=True)
class JiaochStkMinsNormalizationReceipt:
    schema: str
    source_interface: str
    source_frequency: str
    response_body_sha256: str
    stable_security_id: str
    pit_security_descriptor_sha256: str
    board: str
    requested_ts_code: str
    execution_session: date
    source_row_count: int
    opening_special_unresolved: OpeningSpecialUnresolved
    normalized_minutes: tuple[NormalizedStkMinsMinute, ...]
    source_authority_status: str
    minute_amount_authority_status: str
    verification_status: str
    production_effect: str
    verified: bool
    rows_published: bool
    embargo_consumed: bool
    final_oos_consumed: bool
    production_profile_registered: bool
    production_recommendation_eligible: bool

    @property
    def minute_amounts(self) -> tuple[MinuteAmount, ...]:
        return tuple(row.minute_amount for row in self.normalized_minutes)


@dataclass(frozen=True)
class _SourceBar:
    ts_code: str
    source_label_at: datetime
    open_price_cny: float
    close_price_cny: float
    high_price_cny: float
    low_price_cny: float
    volume_shares: int
    amount_cny: float


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


def _expected_source_labels(session: date) -> tuple[datetime, ...]:
    morning = [datetime.combine(session, time(9, 30), _SHANGHAI)]
    cursor = datetime.combine(session, time(9, 31), _SHANGHAI)
    morning_end = datetime.combine(session, time(11, 30), _SHANGHAI)
    while cursor <= morning_end:
        morning.append(cursor)
        cursor += timedelta(minutes=1)

    afternoon = []
    cursor = datetime.combine(session, time(13, 1), _SHANGHAI)
    afternoon_end = datetime.combine(session, time(15, 0), _SHANGHAI)
    while cursor <= afternoon_end:
        afternoon.append(cursor)
        cursor += timedelta(minutes=1)
    return tuple(morning + afternoon)


def _source_label(value: Any, *, session: date, row_index: int) -> datetime:
    if type(value) is not str:
        raise ValueError(f"items[{row_index}].trade_time rejected")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise ValueError(f"items[{row_index}].trade_time rejected") from exc
    if (
        parsed.strftime("%Y-%m-%d %H:%M:%S") != value
        or parsed.second != 0
        or parsed.microsecond != 0
        or parsed.date() != session
    ):
        raise ValueError(f"items[{row_index}].trade_time rejected")
    return parsed.replace(tzinfo=_SHANGHAI)


def _positive_float(value: Any, *, field: str) -> float:
    if type(value) is not float or not math.isfinite(value) or value <= 0.0:
        raise ValueError(f"{field} must be a finite positive float")
    return value


def _volume(value: Any, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{field} must be a nonnegative integer")
    return value


def _amount(value: Any, *, field: str) -> float:
    if type(value) is not float or not math.isfinite(value) or value < 0.0:
        raise ValueError(f"{field} must be a finite nonnegative float")
    return value


def _validated_source_bar(
    row: Any,
    *,
    row_index: int,
    requested_ts_code: str,
    session: date,
) -> _SourceBar:
    if type(row) is not list or len(row) != len(_FIELDS):
        raise ValueError(f"items[{row_index}] must contain exactly eight values")
    ts_code = row[0]
    if type(ts_code) is not str or ts_code != requested_ts_code:
        raise ValueError(f"items[{row_index}].ts_code does not match the request")
    label = _source_label(row[1], session=session, row_index=row_index)
    open_price = _positive_float(row[2], field=f"items[{row_index}].open")
    close_price = _positive_float(row[3], field=f"items[{row_index}].close")
    high_price = _positive_float(row[4], field=f"items[{row_index}].high")
    low_price = _positive_float(row[5], field=f"items[{row_index}].low")
    if (
        low_price > high_price
        or not low_price <= open_price <= high_price
        or not low_price <= close_price <= high_price
    ):
        raise ValueError(f"items[{row_index}] OHLC bounds rejected")
    volume = _volume(row[6], field=f"items[{row_index}].vol")
    amount = _amount(row[7], field=f"items[{row_index}].amount")
    if (volume == 0) != (amount == 0.0):
        raise ValueError(f"items[{row_index}] volume and amount zero state differs")
    if volume > 0:
        try:
            vwap = amount / volume
        except OverflowError as exc:
            raise ValueError(f"items[{row_index}] VWAP rejected") from exc
        if not (
            low_price - VWAP_ABSOLUTE_TOLERANCE_CNY
            <= vwap
            <= high_price + VWAP_ABSOLUTE_TOLERANCE_CNY
        ):
            raise ValueError(f"items[{row_index}] VWAP falls outside OHLC")
    return _SourceBar(
        ts_code=ts_code,
        source_label_at=label,
        open_price_cny=open_price,
        close_price_cny=close_price,
        high_price_cny=high_price,
        low_price_cny=low_price,
        volume_shares=volume,
        amount_cny=amount,
    )


def _strict_success_items(response_body: Any, *, credential_token: str) -> list[Any]:
    if (
        type(response_body) is not bytes
        or not response_body
        or len(response_body) > _MAX_RESPONSE_BYTES
    ):
        raise ValueError("stk_mins response body rejected")
    encoded_token = credential_token.encode("utf-8")
    if encoded_token in response_body:
        raise ValueError("stk_mins credential echo rejected")
    try:
        envelope = _strict_source_json_loads(response_body)
    except (TypeError, ValueError) as exc:
        raise ValueError("stk_mins strict JSON response rejected") from exc
    if _contains_source_token(envelope, credential_token):
        raise ValueError("stk_mins credential echo rejected")
    if type(envelope) is not dict or set(envelope) != {"code", "data", "msg"}:
        raise ValueError("stk_mins envelope rejected")
    if type(envelope["code"]) is not int or envelope["code"] != 0:
        raise ValueError("stk_mins success code rejected")
    if type(envelope["msg"]) is not str or envelope["msg"] != "success":
        raise ValueError("stk_mins success message rejected")
    data = envelope["data"]
    if type(data) is not dict or set(data) != {"fields", "items"}:
        raise ValueError("stk_mins data envelope rejected")
    if type(data["fields"]) is not list or data["fields"] != _FIELDS:
        raise ValueError("stk_mins field contract rejected")
    items = data["items"]
    if type(items) is not list or len(items) != 241:
        raise ValueError("stk_mins requires exactly 241 source rows")
    return items


def _opening_special(
    bar: _SourceBar,
    *,
    stable_security_id: str,
    session: date,
) -> OpeningSpecialUnresolved:
    return OpeningSpecialUnresolved(
        stable_security_id=stable_security_id,
        execution_session=session,
        source_ts_code=bar.ts_code,
        source_label_at=bar.source_label_at,
        open_price_cny=bar.open_price_cny,
        close_price_cny=bar.close_price_cny,
        high_price_cny=bar.high_price_cny,
        low_price_cny=bar.low_price_cny,
        volume_shares=bar.volume_shares,
        amount_cny=bar.amount_cny,
        resolution_status="opening_special_unresolved",
        interval_start_at=None,
        interval_end_at=None,
        eligible_for_capacity=False,
    )


def _normalized_minute(
    bar: _SourceBar,
    *,
    stable_security_id: str,
    session: date,
) -> NormalizedStkMinsMinute:
    phase = (
        "closing_call_auction"
        if bar.source_label_at.time() >= time(14, 58)
        else "continuous_auction"
    )
    minute = MinuteAmount(
        stable_security_id=stable_security_id,
        execution_session=session,
        interval_start_at=bar.source_label_at - timedelta(minutes=1),
        interval_end_at=bar.source_label_at,
        market_phase=phase,
        traded_amount=bar.amount_cny,
        amount_unit="CNY",
    )
    return NormalizedStkMinsMinute(
        source_ts_code=bar.ts_code,
        source_label_at=bar.source_label_at,
        open_price_cny=bar.open_price_cny,
        close_price_cny=bar.close_price_cny,
        high_price_cny=bar.high_price_cny,
        low_price_cny=bar.low_price_cny,
        volume_shares=bar.volume_shares,
        amount_cny=bar.amount_cny,
        minute_amount=minute,
    )


def normalize_jiaoch_stk_mins_success_response(
    *,
    response_body: bytes,
    credential_token: str,
    requested_ts_code: str,
    execution_session: date,
    stable_security_id: str,
    pit_security_descriptor_sha256: str,
    board: str,
) -> JiaochStkMinsNormalizationReceipt:
    """Normalize one complete trading session without asserting source authority."""

    token = _bounded_text(credential_token, field="credential_token")
    security_id = _bounded_text(stable_security_id, field="stable_security_id")
    descriptor_sha = _sha256(
        pit_security_descriptor_sha256,
        field="pit_security_descriptor_sha256",
    )
    session = _execution_session(execution_session)
    normalized_board, ts_code = _security_binding(board, requested_ts_code)
    items = _strict_success_items(response_body, credential_token=token)
    bars = tuple(
        _validated_source_bar(
            row,
            row_index=index,
            requested_ts_code=ts_code,
            session=session,
        )
        for index, row in enumerate(items)
    )
    if tuple(bar.source_label_at for bar in bars) != _expected_source_labels(session):
        raise ValueError("stk_mins source labels are not the exact 241-label schedule")

    opening = _opening_special(
        bars[0],
        stable_security_id=security_id,
        session=session,
    )
    normalized_minutes = tuple(
        _normalized_minute(
            bar,
            stable_security_id=security_id,
            session=session,
        )
        for bar in bars[1:]
    )
    return JiaochStkMinsNormalizationReceipt(
        schema=_SCHEMA,
        source_interface="stk_mins",
        source_frequency="1min",
        response_body_sha256=hashlib.sha256(response_body).hexdigest(),
        stable_security_id=security_id,
        pit_security_descriptor_sha256=descriptor_sha,
        board=normalized_board,
        requested_ts_code=ts_code,
        execution_session=session,
        source_row_count=len(bars),
        opening_special_unresolved=opening,
        normalized_minutes=normalized_minutes,
        source_authority_status="UNBOUND",
        minute_amount_authority_status="UNBOUND",
        verification_status="SCHEMA_NORMALIZED_NOT_SOURCE_VERIFIED",
        production_effect="NONE",
        verified=False,
        rows_published=False,
        embargo_consumed=False,
        final_oos_consumed=False,
        production_profile_registered=False,
        production_recommendation_eligible=False,
    )
