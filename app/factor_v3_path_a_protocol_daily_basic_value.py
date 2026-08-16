"""Path-A Jiaoch daily_basic value pull: PE / PE_TTM / PB / market cap.

Separate from the frozen Factor-V3 733 collection, which does not ask for
valuation fields. Not a v3 store. Not TCB.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_research_protocol as proto
from app.jiaoch_live_market import JIAOCH_API_URL, _canonical_json
from app.research_goal_contract import DATA_SOURCE_POLICY
from app.research_pit_transport import UrllibTushareTransport
from app.storage import write_json

STAGE_GOAL_ID = "path-a-protocol-daily-basic-value/v1"
SOURCE_VERSION = "jiaoch-daily-basic-value/v1"
VALUE_FIELDS = (
    "ts_code",
    "trade_date",
    "pe",
    "pe_ttm",
    "pb",
    "total_mv",
    "circ_mv",
)
DEFAULT_OUTPUT_ROOT = Path("data/research_cache/jiaoch_daily_basic_value_path_a")
DEFAULT_CALENDAR_SPEC = Path(
    "data/research_runs/audited_pit_factor_v3_daily_basic_collection_v2_development_733_http_publish/run-spec.json"
)
ENDPOINT = f"{JIAOCH_API_URL}/daily_basic"
MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_ROWS = 10_000


class PathADailyBasicValueError(ValueError):
    """Raised when the Path-A valuation pull cannot run."""


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _token() -> str:
    token = os.getenv("JIAOCH_TOKEN", "").strip()
    if not token:
        raise PathADailyBasicValueError("JIAOCH_TOKEN is required")
    return token


def scrub_nonfinite(value: Any) -> Any:
    if type(value) is float and not math.isfinite(value):
        return None
    if type(value) is list:
        return [scrub_nonfinite(item) for item in value]
    if type(value) is dict:
        return {key: scrub_nonfinite(item) for key, item in value.items()}
    return value


def locked_trade_dates(calendar_spec: Path) -> list[str]:
    if not calendar_spec.is_file():
        raise PathADailyBasicValueError(f"calendar spec missing: {calendar_spec}")
    payload = json.loads(calendar_spec.read_text(encoding="utf-8"))
    sessions = [str(day)[:10] for day in (payload.get("sessions") or [])]
    return [
        day
        for day in sessions
        if proto.TRAIN_START <= day <= proto.HOLDOUT_END
    ]


def day_path(output_root: Path, trade_date: str) -> Path:
    return output_root / "days" / f"{trade_date}.json"


def day_is_complete(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        payload.get("source_version") == SOURCE_VERSION
        and payload.get("fields") == list(VALUE_FIELDS)
        and int(payload.get("row_count") or 0) > 0
        and len(payload.get("rows") or []) == int(payload.get("row_count") or 0)
    )


def fetch_value_cross_section(
    trade_date: str,
    *,
    token: str,
    transport: UrllibTushareTransport,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    ymd = trade_date.replace("-", "")
    body = _canonical_json(
        {
            "api_name": "daily_basic",
            "token": token,
            "params": {"trade_date": ymd, "ts_code": ""},
            "fields": ",".join(VALUE_FIELDS),
        }
    )
    response = transport.post(
        url=ENDPOINT,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "quant-signal-jiaoch-live/1",
        },
        body=body,
        timeout_s=timeout_s,
        max_body_bytes=MAX_BODY_BYTES,
    )
    raw = response.body if isinstance(response.body, bytes) else b""
    if response.status != 200 or response.body_complete is not True:
        raise PathADailyBasicValueError(
            f"{trade_date} transport rejected status={response.status}"
        )
    if token.encode("utf-8") in raw:
        raise PathADailyBasicValueError(f"{trade_date} credential echo rejected")
    try:
        payload = scrub_nonfinite(json.loads(raw))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PathADailyBasicValueError(f"{trade_date} JSON rejected") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("code") != 0
        or payload.get("msg") != "success"
    ):
        raise PathADailyBasicValueError(
            f"{trade_date} envelope rejected code={payload.get('code')!r}"
        )
    data = payload.get("data") or {}
    if data.get("fields") != list(VALUE_FIELDS):
        raise PathADailyBasicValueError(
            f"{trade_date} fields {data.get('fields')!r} != {list(VALUE_FIELDS)}"
        )
    items = data.get("items") or []
    if not isinstance(items, list) or not items or len(items) > MAX_ROWS:
        raise PathADailyBasicValueError(f"{trade_date} row count rejected")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, list) or len(item) != len(VALUE_FIELDS):
            raise PathADailyBasicValueError(f"{trade_date} row rejected")
        row = dict(zip(VALUE_FIELDS, item, strict=True))
        code = str(row.get("ts_code") or "")
        if not code or code in seen:
            raise PathADailyBasicValueError(f"{trade_date} duplicate/empty ts_code")
        if str(row.get("trade_date") or "") != ymd:
            raise PathADailyBasicValueError(f"{trade_date} row date mismatch")
        seen.add(code)
        rows.append(row)
    return {
        "schema": "path-a-daily-basic-value-day/v1",
        "stage_goal_id": STAGE_GOAL_ID,
        "source_policy": DATA_SOURCE_POLICY,
        "source_version": SOURCE_VERSION,
        "trade_date": trade_date,
        "fields": list(VALUE_FIELDS),
        "row_count": len(rows),
        "rows": rows,
    }


def collect_path_a_daily_basic_value(
    *,
    repo_root: Path | None = None,
    output_root: Path | None = None,
    calendar_spec: Path | None = None,
    max_days: int = 0,
    sleep_s: float = 0.15,
    max_attempts: int = 3,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = os.getenv("VPS_RUNTIME_ROLE", "")
        if role.strip().casefold() != "local_research":
            raise PathADailyBasicValueError(
                f"requires VPS_RUNTIME_ROLE=local_research (got {role!r})"
            )
    root = (repo_root or Path.cwd()).resolve()
    _load_dotenv(root / ".env")
    token = _token()
    out = (
        Path(output_root).resolve()
        if output_root is not None
        else (root / DEFAULT_OUTPUT_ROOT).resolve()
    )
    spec = (
        Path(calendar_spec).resolve()
        if calendar_spec is not None
        else (root / DEFAULT_CALENDAR_SPEC)
    )
    dates = locked_trade_dates(spec)
    if max_days > 0:
        dates = dates[:max_days]
    if not dates:
        raise PathADailyBasicValueError("no locked trade dates")
    transport = UrllibTushareTransport(proxy_url=None)
    (out / "days").mkdir(parents=True, exist_ok=True)
    fetched = 0
    skipped = 0
    failed: list[str] = []
    for index, day in enumerate(dates, 1):
        path = day_path(out, day)
        if day_is_complete(path):
            skipped += 1
            continue
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                payload = fetch_value_cross_section(
                    day, token=token, transport=transport
                )
                write_json(str(path), payload)
                fetched += 1
                last_error = ""
                break
            except Exception as exc:  # noqa: BLE001 - retry then record
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(min(2.0 * attempt, 6.0))
        if last_error:
            failed.append(f"{day} {last_error}")
        if index % 20 == 0 or index == len(dates):
            print(
                f"value_pull {index}/{len(dates)} fetched={fetched} "
                f"skipped={skipped} failed={len(failed)}",
                flush=True,
            )
        if sleep_s > 0 and last_error == "":
            time.sleep(sleep_s)
    report = {
        "schema": "path-a-daily-basic-value-report/v1",
        "stage_goal_id": STAGE_GOAL_ID,
        "source_version": SOURCE_VERSION,
        "fields": list(VALUE_FIELDS),
        "start": dates[0],
        "end": dates[-1],
        "planned": len(dates),
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
        "complete": len(failed) == 0
        and (fetched + skipped) == len(dates),
        "output_root": str(out),
        "effective_strategy": False,
        "promotable": False,
        "vps_runtime_role": os.getenv("VPS_RUNTIME_ROLE", ""),
    }
    write_json(str(out / "LATEST.json"), report)
    return report
