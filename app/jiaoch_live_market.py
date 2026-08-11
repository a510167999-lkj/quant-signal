"""Jiaoch-only live daily market adapter.

The research collectors keep their own sealed authority paths.  This module is
the small runtime adapter used by the recommendation service: it talks only to
the pinned Jiaoch host, keeps the two credential slots separate, rejects
credential echoes, and never falls back to another market source.
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from app.market_data import (
    MarketDataError,
    _date_strings,
    _today,
    _trim_frame,
    _tushare_ts_code,
    normalize_symbol,
)
from app.research_pit_sources import _validate_loopback_http_proxy
from app.research_pit_transport import UrllibTushareTransport


JIAOCH_API_URL = "http://jiaoch.site"
DAILY_FIELDS = (
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
ADJ_FACTOR_FIELDS = ("ts_code", "trade_date", "adj_factor")
BAK_BASIC_FIELDS = ("trade_date", "ts_code", "name", "industry", "list_date")
# stk_mins minute bars (used to synthesize daily OHLCV when /daily is empty).
STK_MINS_FIELDS = ("ts_code", "trade_time", "open", "close", "high", "low", "vol", "amount")
_MAX_BODY_BYTES = 32 * 1024 * 1024
_MAX_ROWS = 10_000
_TOKEN_ENV_BY_SLOT = {
    "points-primary": "JIAOCH_TOKEN",
    "historical-minute": "JIAOCH_STK_MINS_TOKEN",
}
_API_SLOT = {
    "daily": "historical-minute",
    "adj_factor": "points-primary",
    "bak_basic": "points-primary",
}


class JiaochLiveMarketError(MarketDataError):
    """Stable fail-closed error for the runtime Jiaoch adapter."""


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError) as exc:
        raise JiaochLiveMarketError("Jiaoch request JSON rejected") from exc


def _strict_json_loads(raw: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise JiaochLiveMarketError("Jiaoch response contains duplicate JSON keys")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise JiaochLiveMarketError(f"Jiaoch response contains non-finite JSON value: {value}")

    try:
        return json.loads(
            raw,
            object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise JiaochLiveMarketError("Jiaoch response is not valid JSON") from exc


def _contains_token(value: Any, token: str, *, depth: int = 0) -> bool:
    if not token:
        return False
    if depth > 64:
        raise JiaochLiveMarketError("Jiaoch response nesting depth exceeded")
    if isinstance(value, str):
        return token in value
    if isinstance(value, Mapping):
        return any(
            _contains_token(key, token, depth=depth + 1)
            or _contains_token(item, token, depth=depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return any(_contains_token(item, token, depth=depth + 1) for item in value)
    return False


def _wire_date(value: str | date) -> str:
    if isinstance(value, datetime):
        raise JiaochLiveMarketError("Jiaoch date must not be a datetime")
    if isinstance(value, date):
        return value.isoformat().replace("-", "")
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise JiaochLiveMarketError("Jiaoch date is invalid") from exc
    if parsed.isoformat() != text:
        raise JiaochLiveMarketError("Jiaoch date is not canonical")
    return parsed.isoformat().replace("-", "")


def _iso_date(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise JiaochLiveMarketError("Jiaoch response trade_date is invalid") from exc


def _finite_number(value: Any, *, field: str, positive: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise JiaochLiveMarketError(f"Jiaoch response {field} is not numeric") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        raise JiaochLiveMarketError(f"Jiaoch response {field} is invalid")
    return number


class JiaochHttpClient:
    """Bounded Jiaoch client with explicit credential-slot routing."""

    def __init__(
        self,
        *,
        transport: Any = None,
        points_token: str | None = None,
        historical_minute_token: str | None = None,
        proxy_url: str | None = None,
        api_url: str = JIAOCH_API_URL,
        max_body_bytes: int = _MAX_BODY_BYTES,
    ) -> None:
        if api_url != JIAOCH_API_URL:
            raise JiaochLiveMarketError("Jiaoch API host is fixed")
        configured_proxy = proxy_url if proxy_url is not None else str(os.getenv("JIAOCH_PROXY_URL") or "")
        if configured_proxy:
            try:
                configured_proxy = _validate_loopback_http_proxy(configured_proxy)
            except ValueError as exc:
                raise JiaochLiveMarketError("Jiaoch proxy configuration is invalid") from exc
        self._api_url = api_url
        self._max_body_bytes = int(max_body_bytes)
        if self._max_body_bytes <= 0 or self._max_body_bytes > _MAX_BODY_BYTES:
            raise JiaochLiveMarketError("Jiaoch response body limit is invalid")
        self._tokens = {
            "points-primary": points_token,
            "historical-minute": historical_minute_token,
        }
        self._transport = transport or UrllibTushareTransport(proxy_url=configured_proxy or None)

    def _token(self, slot: str) -> str:
        token = self._tokens.get(slot)
        if token is None:
            token = os.getenv(_TOKEN_ENV_BY_SLOT[slot], "")
        if not isinstance(token, str) or not token:
            raise JiaochLiveMarketError(f"{_TOKEN_ENV_BY_SLOT[slot]} is required")
        if any(ord(character) < 32 or ord(character) == 127 for character in token):
            raise JiaochLiveMarketError("Jiaoch credential is invalid")
        return token

    def fetch(
        self,
        api_name: str,
        *,
        params: Mapping[str, str],
        fields: Sequence[str],
    ) -> list[dict[str, Any]]:
        slot = _API_SLOT.get(api_name)
        if slot is None or tuple(fields) not in {DAILY_FIELDS, ADJ_FACTOR_FIELDS, BAK_BASIC_FIELDS}:
            raise JiaochLiveMarketError("Jiaoch live API route is not allowlisted")
        token = self._token(slot)
        body = _canonical_json(
            {
                "api_name": api_name,
                "token": token,
                "params": dict(params),
                "fields": ",".join(fields),
            }
        )
        try:
            timeout_seconds = float(os.getenv("JIAOCH_LIVE_TIMEOUT_SECONDS", "12"))
        except ValueError as exc:
            raise JiaochLiveMarketError("Jiaoch live timeout is invalid") from exc
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60:
            raise JiaochLiveMarketError("Jiaoch live timeout is invalid")
        response = self._transport.post(
            url=f"{self._api_url}/{api_name}",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "quant-signal-jiaoch-live/1",
            },
            body=body,
            timeout_s=timeout_seconds,
            max_body_bytes=self._max_body_bytes,
        )
        raw = response.body if isinstance(response.body, bytes) else b""
        if response.status != 200 or response.body_complete is not True or len(raw) > self._max_body_bytes:
            raise JiaochLiveMarketError("Jiaoch live response transport rejected")
        if token.encode("utf-8") in raw:
            raise JiaochLiveMarketError("Jiaoch response credential echo rejected")
        payload = _strict_json_loads(raw)
        if _contains_token(payload, token):
            raise JiaochLiveMarketError("Jiaoch response credential echo rejected")
        if (
            not isinstance(payload, Mapping)
            or set(payload) != {"code", "data", "msg"}
            or type(payload.get("code")) is not int
            or payload.get("code") != 0
            or payload.get("msg") != "success"
        ):
            raise JiaochLiveMarketError("Jiaoch live response envelope rejected")
        data = payload.get("data")
        expected_fields = list(fields)
        if (
            not isinstance(data, Mapping)
            or data.get("fields") != expected_fields
            or not isinstance(data.get("items"), list)
            or len(data["items"]) > _MAX_ROWS
        ):
            raise JiaochLiveMarketError("Jiaoch live response fields rejected")
        rows: list[dict[str, Any]] = []
        for values in data["items"]:
            if not isinstance(values, list) or len(values) != len(expected_fields):
                raise JiaochLiveMarketError("Jiaoch live response row rejected")
            rows.append(dict(zip(expected_fields, values, strict=True)))
        return rows

    def fetch_stk_mins(
        self,
        *,
        ts_code: str,
        start_date: str,
        end_date: str,
        freq: str = "5min",
    ) -> list[dict[str, Any]]:
        """Fetch historical minute bars from /stk_mins (historical-minute slot).

        stk_mins caps the number of rows per request; a wide date range returns
        only the most recent slice. We segment the range into monthly chunks so
        the full history is recovered.
        """

        token = self._token("historical-minute")
        timeout_seconds = float(os.getenv("JIAOCH_LIVE_TIMEOUT_SECONDS", "12"))
        chunks = _monthly_chunks(start_date, end_date)
        from concurrent.futures import ThreadPoolExecutor

        def _fetch_one(chunk: tuple[str, str]) -> list[dict[str, Any]]:
            chunk_start, chunk_end = chunk
            body = _canonical_json(
                {
                    "api_name": "stk_mins",
                    "token": token,
                    "params": {
                        "ts_code": ts_code,
                        "start_date": chunk_start,
                        "end_date": chunk_end,
                        "freq": freq,
                    },
                }
            )
            response = self._transport.post(
                url=f"{self._api_url}/stk_mins",
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    "Content-Type": "application/json; charset=utf-8",
                    "User-Agent": "quant-signal-jiaoch-live/1",
                },
                body=body,
                timeout_s=timeout_seconds,
                max_body_bytes=self._max_body_bytes,
            )
            raw = response.body if isinstance(response.body, bytes) else b""
            if response.status != 200 or response.body_complete is not True:
                raise JiaochLiveMarketError("Jiaoch stk_mins response transport rejected")
            payload = _strict_json_loads(raw)
            if not isinstance(payload, Mapping) or payload.get("code") != 0:
                msg = payload.get("msg", "") if isinstance(payload, Mapping) else ""
                raise JiaochLiveMarketError(f"Jiaoch stk_mins request failed: {msg}")
            data = payload.get("data")
            if not isinstance(data, Mapping) or not isinstance(data.get("items"), list):
                return []
            rows: list[dict[str, Any]] = []
            for values in data["items"]:
                if not isinstance(values, list) or len(values) != len(STK_MINS_FIELDS):
                    continue
                rows.append(dict(zip(STK_MINS_FIELDS, values, strict=True)))
            return rows

        all_rows: list[dict[str, Any]] = []
        max_workers = min(len(chunks), int(os.getenv("JIAOCH_STK_MINS_WORKERS", "2")))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for chunk_rows in pool.map(_fetch_one, chunks):
                all_rows.extend(chunk_rows)
        return all_rows


def _daily_frame(rows: Sequence[Mapping[str, Any]], *, symbol: str, market: str) -> pd.DataFrame:
    records = []
    seen_dates: set[str] = set()
    expected_code = _tushare_ts_code(symbol)
    for row in rows:
        if str(row.get("ts_code") or "").strip() != expected_code:
            continue
        trade_date = _iso_date(row.get("trade_date"))
        if trade_date in seen_dates:
            raise JiaochLiveMarketError("Jiaoch daily contains duplicate dates")
        seen_dates.add(trade_date)
        record = {
            "date": trade_date,
            "open": _finite_number(row.get("open"), field="open", positive=True),
            "high": _finite_number(row.get("high"), field="high", positive=True),
            "low": _finite_number(row.get("low"), field="low", positive=True),
            "close": _finite_number(row.get("close"), field="close", positive=True),
            # Tushare/Jiaoch daily volume and amount are lots and thousand CNY.
            "volume": _finite_number(row.get("vol"), field="vol") * 100.0,
            "amount": _finite_number(row.get("amount"), field="amount") * 1000.0,
        }
        if record["high"] < max(record["open"], record["close"]) or record["low"] > min(
            record["open"], record["close"]
        ):
            raise JiaochLiveMarketError("Jiaoch daily OHLC bounds rejected")
        records.append(record)
    if not records:
        raise JiaochLiveMarketError(f"Jiaoch daily returned no rows for {market}:{symbol}")
    frame = pd.DataFrame(records).sort_values("date").drop_duplicates("date", keep="last")
    return frame.reset_index(drop=True)


def _aggregate_daily_from_minutes(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Aggregate stk_mins 5-minute bars into daily OHLCV rows (DAILY_FIELDS format).

    For each trading day: open = first bar's open, close = last bar's close,
    high/low = max/min across all bars, vol/amount = sum.
    """

    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        trade_time = str(row.get("trade_time") or "")
        trade_date = trade_time[:10]  # YYYY-MM-DD
        if not trade_date:
            continue
        by_date.setdefault(trade_date, []).append(row)
    daily: list[dict[str, Any]] = []
    prev_close: float | None = None
    for trade_date in sorted(by_date):
        bars = by_date[trade_date]
        opens = [float(b["open"]) for b in bars if b.get("open") is not None]
        closes = [float(b["close"]) for b in bars if b.get("close") is not None]
        highs = [float(b["high"]) for b in bars if b.get("high") is not None]
        lows = [float(b["low"]) for b in bars if b.get("low") is not None]
        vols = [float(b["vol"]) for b in bars if b.get("vol") is not None]
        amounts = [float(b["amount"]) for b in bars if b.get("amount") is not None]
        if not opens or not closes:
            continue
        ts_code = str(bars[0].get("ts_code") or "")
        day_close = closes[-1]
        day_open = opens[0]
        # pre_close is the previous trading day's close; for the first day use
        # the day's own open as a fallback (no prior reference available).
        pre_close = prev_close if prev_close is not None else day_open
        change = day_close - pre_close
        pct_chg = (day_close / pre_close - 1) * 100 if pre_close else 0.0
        daily.append({
            "ts_code": ts_code,
            "trade_date": trade_date.replace("-", ""),
            "open": day_open,
            "high": max(highs) if highs else day_open,
            "low": min(lows) if lows else day_open,
            "close": day_close,
            "pre_close": pre_close,
            "change": change,
            "pct_chg": pct_chg,
            "vol": sum(vols),
            "amount": sum(amounts),
        })
        prev_close = day_close
    return daily


def _monthly_chunks(start: str, end: str) -> list[tuple[str, str]]:
    """Split a YYYYMMDD date range into quarterly (3-month) chunks.

    stk_mins returns ~3000+ rows per request; a quarterly chunk (~63 trading
    days × 49 bars ≈ 3000) fits within that budget while minimizing HTTP
    round-trips (3 years → ~12 requests instead of ~36 monthly).
    """

    from datetime import date as _date, timedelta as _td

    def _parse(s: str) -> _date:
        return _date(int(s[:4]), int(s[4:6]), int(s[6:8]))

    def _fmt(d: _date) -> str:
        return d.strftime("%Y%m%d")

    s, e = _parse(start), _parse(end)
    chunks: list[tuple[str, str]] = []
    cur = s
    quarter_days = 91  # ~3 calendar months
    while cur <= e:
        chunk_end = min(cur + _td(days=quarter_days), e)
        chunks.append((_fmt(cur), _fmt(chunk_end)))
        cur = chunk_end + _td(days=1)
    return chunks


def _apply_qfq(
    frame: pd.DataFrame,
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_code: str,
) -> pd.DataFrame:
    factors: dict[str, float] = {}
    for row in rows:
        if str(row.get("ts_code") or "").strip() != expected_code:
            continue
        current = _iso_date(row.get("trade_date"))
        if current in factors:
            raise JiaochLiveMarketError("Jiaoch adj_factor contains duplicate dates")
        factors[current] = _finite_number(row.get("adj_factor"), field="adj_factor", positive=True)
    if not factors:
        raise JiaochLiveMarketError("Jiaoch adj_factor returned no rows")
    as_of = str(frame["date"].max())
    valid = [(day, factor) for day, factor in factors.items() if day <= as_of]
    if not valid:
        raise JiaochLiveMarketError("Jiaoch adj_factor has no causal as-of factor")
    as_of_day, as_of_factor = max(valid, key=lambda item: item[0])
    adjusted = frame.copy()
    ratios = []
    for day in adjusted["date"]:
        factor = factors.get(str(day))
        if factor is None:
            raise JiaochLiveMarketError("Jiaoch adj_factor coverage is incomplete")
        ratios.append(factor / as_of_factor)
    for column in ("open", "high", "low", "close"):
        adjusted[column] = adjusted[column] * ratios
    adjusted.attrs["adjustment_as_of_date"] = as_of_day
    return adjusted


class JiaochMarketDataProvider:
    """Jiaoch-only daily provider implementing the platform ``history`` API."""

    def __init__(
        self,
        *,
        cache_ttl_seconds: int = 1800,
        disk_cache_path: str | None = None,
        client: JiaochHttpClient | None = None,
        identity_loader: Callable[[], Mapping[str, Mapping[str, Any]]] | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.cache_ttl_seconds = max(60, int(cache_ttl_seconds))
        self.disk_cache_path = disk_cache_path or os.getenv("MARKET_DATA_CACHE_PATH", "")
        self.client = client or JiaochHttpClient()
        self.identity_loader = identity_loader
        self._now_provider = now_provider or _today
        self._cache: dict[tuple[str, str, int, str], tuple[pd.DataFrame, datetime, str]] = {}
        self._memory_lock = RLock()
        self._disk_lock = RLock()

    def history(
        self,
        symbol: str,
        market: str,
        lookback_days: int = 360,
        adjust: str = "qfq",
    ) -> tuple[pd.DataFrame, str]:
        normalized = normalize_symbol(symbol, market)
        if market not in {"a", "etf"} or not normalized.isdigit() or len(normalized) != 6:
            raise JiaochLiveMarketError("Jiaoch live symbol is invalid")
        if adjust not in {"", "qfq"}:
            raise JiaochLiveMarketError("Jiaoch live provider supports only raw and qfq data")
        key = (market, normalized, int(lookback_days), adjust)
        now = self._now_provider()
        with self._memory_lock:
            cached = self._cache.get(key)
            if cached and cached[1] > now:
                return cached[0].copy(), cached[2]
        start_date, end_date = _date_strings(lookback_days)
        try:
            ts_code = _tushare_ts_code(normalized)
            # The Jiaoch mirror does not serve /daily directly; synthesize daily
            # OHLCV by aggregating stk_mins 5-minute bars.
            min_rows = self.client.fetch_stk_mins(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                freq="5min",
            )
            daily_rows = _aggregate_daily_from_minutes(min_rows)
            if not daily_rows:
                raise JiaochLiveMarketError(
                    "Jiaoch stk_mins returned no rows for daily synthesis"
                )
            frame = _daily_frame(daily_rows, symbol=normalized, market=market)
            if adjust == "qfq":
                factor_rows = self.client.fetch(
                    "adj_factor",
                    params={
                        "ts_code": ts_code,
                        "start_date": start_date,
                        "end_date": end_date,
                    },
                    fields=ADJ_FACTOR_FIELDS,
                )
                frame = _apply_qfq(frame, factor_rows, expected_code=ts_code)
            frame = _trim_frame(frame, start_date, end_date)
            source = "Jiaoch stk_mins daily qfq" if adjust == "qfq" else "Jiaoch stk_mins daily raw"
            self._write_cache(normalized, market, adjust, frame, source)
        except Exception as exc:
            cached = self._read_cache(
                normalized,
                market,
                adjust,
                start_date=start_date,
                end_date=end_date,
            )
            if cached is None:
                if isinstance(exc, JiaochLiveMarketError):
                    raise
                raise JiaochLiveMarketError("Jiaoch live history request failed") from exc
            frame, source = cached
            source = "Jiaoch SQLite daily cache stale fallback"
        with self._memory_lock:
            self._cache[key] = (
                frame.copy(),
                now + timedelta(seconds=self.cache_ttl_seconds),
                source,
            )
        return frame.copy(), source

    def snapshot(self, use_cache_on_error: bool = True) -> list[dict[str, Any]]:
        """Fetch one Jiaoch daily cross-section and frozen identity view."""

        moment = self._now_provider().astimezone(ZoneInfo("Asia/Shanghai"))
        last_error: Exception | None = None
        for offset in range(0, 8):
            candidate = moment.date() - timedelta(days=offset)
            wire_date = _wire_date(candidate)
            try:
                daily_rows = self.client.fetch(
                    "daily",
                    params={"trade_date": wire_date},
                    fields=DAILY_FIELDS,
                )
                if daily_rows:
                    if any(_iso_date(row.get("trade_date")) != candidate.isoformat() for row in daily_rows):
                        raise JiaochLiveMarketError("Jiaoch daily snapshot date binding rejected")
                    return self._snapshot_rows(candidate.isoformat(), daily_rows)
            except Exception as exc:
                last_error = exc
        if use_cache_on_error:
            raise JiaochLiveMarketError("Jiaoch live snapshot unavailable; non-Jiaoch cache is forbidden") from last_error
        raise JiaochLiveMarketError("Jiaoch live snapshot unavailable") from last_error

    def _snapshot_rows(self, as_of: str, daily_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        identities = self.identity_loader() if self.identity_loader else self._load_identities(as_of)
        result = []
        seen_symbols: set[str] = set()
        for row in daily_rows:
            ts_code = str(row.get("ts_code") or "").strip().upper()
            if len(ts_code) != 9 or ts_code[6] != "." or ts_code[:6] not in identities:
                continue
            open_price = _finite_number(row.get("open"), field="open", positive=True)
            high_price = _finite_number(row.get("high"), field="high", positive=True)
            low_price = _finite_number(row.get("low"), field="low", positive=True)
            symbol = ts_code[:6]
            identity = identities[symbol]
            close = _finite_number(row.get("close"), field="close", positive=True)
            if high_price < max(open_price, close) or low_price > min(open_price, close):
                raise JiaochLiveMarketError("Jiaoch snapshot OHLC bounds rejected")
            amount = _finite_number(row.get("amount"), field="amount") * 1000.0
            volume = _finite_number(row.get("vol"), field="vol") * 100.0
            change = _finite_number(row.get("pct_chg"), field="pct_chg")
            if symbol in seen_symbols:
                raise JiaochLiveMarketError("Jiaoch snapshot contains duplicate symbols")
            seen_symbols.add(symbol)
            result.append(
                {
                    "symbol": symbol,
                    "market": "a",
                    "name": str(identity.get("name") or symbol),
                    "latest": close,
                    "amount": amount,
                    "change_pct": change,
                    "volume": volume,
                    "timestamp": as_of,
                    "market_snapshot_source": "Jiaoch daily+bak_basic",
                }
            )
        if not result:
            raise JiaochLiveMarketError("Jiaoch snapshot has no recognized A-share rows")
        return result

    def _load_identities(self, as_of: str) -> Mapping[str, Mapping[str, Any]]:
        rows = self.client.fetch(
            "bak_basic",
            params={"trade_date": _wire_date(as_of)},
            fields=BAK_BASIC_FIELDS,
        )
        result: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            if _iso_date(row.get("trade_date")) != as_of:
                raise JiaochLiveMarketError("Jiaoch bak_basic date binding rejected")
            ts_code = str(row.get("ts_code") or "").strip().upper()
            if len(ts_code) == 9 and ts_code[6] == "." and ts_code[:6].isdigit():
                if ts_code[:6] in result:
                    raise JiaochLiveMarketError("Jiaoch bak_basic contains duplicate identities")
                result[ts_code[:6]] = dict(row)
        if not result:
            raise JiaochLiveMarketError("Jiaoch bak_basic returned no identities")
        return result

    def _connect_cache(self) -> sqlite3.Connection | None:
        if not self.disk_cache_path:
            return None
        path = Path(self.disk_cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(path), timeout=30)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_bars (
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                adjust TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                amount REAL,
                source TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (market, symbol, adjust, date)
            )
            """
        )
        return connection

    def _read_cache(
        self,
        symbol: str,
        market: str,
        adjust: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> tuple[pd.DataFrame, str] | None:
        with self._disk_lock:
            connection = self._connect_cache()
            if connection is None:
                return None
            try:
                rows = connection.execute(
                    """
                    SELECT date, open, high, low, close, volume, amount, source
                    FROM daily_bars
                    WHERE market = ? AND symbol = ? AND adjust = ?
                    ORDER BY date
                    """,
                    (market, symbol, adjust),
                ).fetchall()
            finally:
                connection.close()
        if len(rows) < 60:
            return None
        sources = {str(row[7] or "").strip() for row in rows}
        if not sources or any(not value.lower().startswith("jiaoch") for value in sources):
            return None
        frame = pd.DataFrame(
            [row[:7] for row in rows],
            columns=["date", "open", "high", "low", "close", "volume", "amount"],
        )
        if start_date and end_date:
            start_iso = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
            end_iso = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
            frame = frame[(frame["date"] >= start_iso) & (frame["date"] <= end_iso)].reset_index(drop=True)
            if len(frame) < 60:
                return None
        return frame, sorted(sources)[0]

    def _write_cache(
        self,
        symbol: str,
        market: str,
        adjust: str,
        frame: pd.DataFrame,
        source: str,
    ) -> None:
        with self._disk_lock:
            connection = self._connect_cache()
            if connection is None:
                return
            try:
                updated_at = self._now_provider().isoformat()
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO daily_bars (
                        market, symbol, adjust, date, open, high, low, close,
                        volume, amount, source, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            market,
                            symbol,
                            adjust,
                            str(row["date"])[:10],
                            float(row["open"]),
                            float(row["high"]),
                            float(row["low"]),
                            float(row["close"]),
                            float(row.get("volume", 0) or 0),
                            float(row["amount"]) if row.get("amount") is not None else None,
                            source,
                            updated_at,
                        )
                        for row in frame.to_dict(orient="records")
                    ],
                )
                connection.commit()
            finally:
                connection.close()

JIAOCH_MARKET_HISTORY_METHOD = JiaochMarketDataProvider.history
JIAOCH_MARKET_SNAPSHOT_METHOD = JiaochMarketDataProvider.snapshot
JIAOCH_MARKET_PROVIDER_TYPE = JiaochMarketDataProvider


__all__ = (
    "ADJ_FACTOR_FIELDS",
    "BAK_BASIC_FIELDS",
    "DAILY_FIELDS",
    "JiaochHttpClient",
    "JiaochLiveMarketError",
    "JiaochMarketDataProvider",
    "JIAOCH_MARKET_HISTORY_METHOD",
    "JIAOCH_MARKET_PROVIDER_TYPE",
    "JIAOCH_MARKET_SNAPSHOT_METHOD",
)
