import logging
import os
import random
import time
from datetime import datetime
from typing import Callable, Optional, TypeVar
from zoneinfo import ZoneInfo

from app.network import market_data_proxy_scope
from app.storage import read_json, write_json


logger = logging.getLogger(__name__)
T = TypeVar("T")
LOW_RETRY_ENDPOINTS = {
    "stock_board_industry_name_em",
    "stock_board_industry_cons_em",
    "stock_board_industry_hist_em",
    "stock_zh_a_disclosure_report_cninfo",
    "stock_news_em",
    "stock_lhb_detail_em",
    "tool_trade_date_hist_sina",
}


def _now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(max(value, minimum), maximum)


def _status_path() -> str:
    return os.getenv("AKSHARE_STATUS_PATH", "data/akshare_status.json")


def _record_status(
    endpoint: str,
    status: str,
    attempts: int,
    elapsed_ms: int,
    error: Optional[BaseException] = None,
) -> None:
    try:
        path = _status_path()
        payload = read_json(path, {"updated_at": None, "endpoints": {}, "events": []})
        if not isinstance(payload, dict):
            payload = {"updated_at": None, "endpoints": {}, "events": []}
        endpoints = payload.get("endpoints")
        if not isinstance(endpoints, dict):
            endpoints = {}
        events = payload.get("events")
        if not isinstance(events, list):
            events = []

        event = {
            "updated_at": _now_iso(),
            "endpoint": endpoint,
            "status": status,
            "attempts": attempts,
            "elapsed_ms": elapsed_ms,
        }
        if error is not None:
            event["error_type"] = type(error).__name__
            event["error"] = str(error)[:300]

        endpoints[endpoint] = event
        events.append(event)
        payload["updated_at"] = event["updated_at"]
        payload["endpoints"] = endpoints
        payload["events"] = events[-100:]
        write_json(path, payload)
    except Exception:
        pass


def akshare_call(
    endpoint: str,
    operation: Callable[[], T],
    attempts: Optional[int] = None,
    base_delay_seconds: Optional[float] = None,
    jitter_seconds: Optional[float] = None,
    max_elapsed_seconds: Optional[float] = None,
) -> T:
    default_attempts = 2 if endpoint in LOW_RETRY_ENDPOINTS else 3
    max_attempts = attempts or _int_env("AKSHARE_MAX_RETRIES", default_attempts, 1, 8)
    if endpoint in LOW_RETRY_ENDPOINTS:
        max_attempts = min(max_attempts, 2)
    base_delay = (
        base_delay_seconds
        if base_delay_seconds is not None
        else _float_env("AKSHARE_RETRY_BASE_DELAY_SECONDS", 0.8, 0.0, 10.0)
    )
    jitter = (
        jitter_seconds
        if jitter_seconds is not None
        else _float_env("AKSHARE_RETRY_JITTER_SECONDS", 0.4, 0.0, 5.0)
    )
    started = time.monotonic()
    max_elapsed = (
        max_elapsed_seconds
        if max_elapsed_seconds is not None
        else _float_env("AKSHARE_MAX_ELAPSED_SECONDS", 0.0, 0.0, 600.0)
    )
    last_error: Optional[BaseException] = None

    with market_data_proxy_scope():
        for attempt in range(1, max_attempts + 1):
            try:
                result = operation()
                if attempt > 1:
                    _record_status(
                        endpoint,
                        "recovered",
                        attempt,
                        int((time.monotonic() - started) * 1000),
                        last_error,
                    )
                return result
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "akshare %s attempt %d/%d failed: %s",
                    endpoint,
                    attempt,
                    max_attempts,
                    exc,
                )
                if attempt >= max_attempts:
                    break
                if max_elapsed and (time.monotonic() - started) >= max_elapsed:
                    break
                delay = base_delay * (2 ** (attempt - 1))
                if jitter > 0:
                    delay += random.uniform(0, jitter)
                if max_elapsed:
                    remaining = max_elapsed - (time.monotonic() - started)
                    if remaining <= 0:
                        break
                    delay = min(delay, remaining)
                if delay > 0:
                    time.sleep(delay)

    _record_status(
        endpoint,
        "failed",
        max_attempts,
        int((time.monotonic() - started) * 1000),
        last_error,
    )
    logger.error(
        "akshare %s failed after %d attempts: %s", endpoint, max_attempts, last_error
    )
    if last_error is not None:
        raise last_error
    raise RuntimeError("AKShare call failed: %s" % endpoint)
