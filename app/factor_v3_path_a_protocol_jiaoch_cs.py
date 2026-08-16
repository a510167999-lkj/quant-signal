"""Path-A Jiaoch cross-section pulls. Not the frozen V3 733 field list."""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_research_protocol as proto
from app.factor_v3_path_a_protocol_daily_basic_value import (
    DEFAULT_CALENDAR_SPEC,
    _load_dotenv,
    _token,
    locked_trade_dates,
    scrub_nonfinite,
)
from app.jiaoch_live_market import JIAOCH_API_URL, _canonical_json
from app.research_pit_transport import UrllibTushareTransport
from app.storage import write_json

MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_ROWS = 12_000


class PathAJiaochCsError(ValueError):
    """Raised when a Path-A Jiaoch cross-section pull fails."""


def fetch_cs_day(
    *,
    api_name: str,
    fields: tuple[str, ...],
    trade_date: str,
    token: str,
    transport: UrllibTushareTransport,
    timeout_s: float = 45.0,
) -> dict[str, Any]:
    ymd = trade_date.replace("-", "")
    body = _canonical_json(
        {
            "api_name": api_name,
            "token": token,
            "params": {"trade_date": ymd, "ts_code": ""},
            "fields": ",".join(fields),
        }
    )
    response = transport.post(
        url=f"{JIAOCH_API_URL}/{api_name}",
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
        raise PathAJiaochCsError(f"{api_name} {trade_date} transport {response.status}")
    if token.encode("utf-8") in raw:
        raise PathAJiaochCsError(f"{api_name} {trade_date} credential echo")
    try:
        payload = scrub_nonfinite(json.loads(raw))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PathAJiaochCsError(f"{api_name} {trade_date} JSON") from exc
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise PathAJiaochCsError(
            f"{api_name} {trade_date} envelope {payload.get('code')!r}"
        )
    data = payload.get("data") or {}
    if data.get("fields") != list(fields):
        raise PathAJiaochCsError(
            f"{api_name} {trade_date} fields {data.get('fields')!r}"
        )
    items = data.get("items") or []
    if not isinstance(items, list) or not items or len(items) > MAX_ROWS:
        raise PathAJiaochCsError(f"{api_name} {trade_date} rows {len(items)}")
    rows = []
    seen: set[str] = set()
    code_key = "ts_code"
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            raise PathAJiaochCsError(f"{api_name} {trade_date} row")
        row = dict(zip(fields, item, strict=True))
        code = str(row.get(code_key) or "")
        if not code or code in seen:
            continue
        seen.add(code)
        rows.append(row)
    if not rows:
        raise PathAJiaochCsError(f"{api_name} {trade_date} empty after filter")
    return {
        "trade_date": trade_date,
        "api_name": api_name,
        "fields": list(fields),
        "row_count": len(rows),
        "rows": rows,
    }


def collect_cs(
    *,
    api_name: str,
    fields: tuple[str, ...],
    source_version: str,
    output_root: Path,
    repo_root: Path,
    dates: list[str],
    sleep_s: float = 0.15,
    max_attempts: int = 3,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = os.getenv("VPS_RUNTIME_ROLE", "")
        if role.strip().casefold() != "local_research":
            raise PathAJiaochCsError(f"requires local_research (got {role!r})")
    _load_dotenv(repo_root / ".env")
    token = _token()
    transport = UrllibTushareTransport(proxy_url=None)
    (output_root / "days").mkdir(parents=True, exist_ok=True)
    fetched = skipped = 0
    failed: list[str] = []
    for index, day in enumerate(dates, 1):
        path = output_root / "days" / f"{day}.json"
        if path.is_file():
            try:
                prev = json.loads(path.read_text(encoding="utf-8"))
                if (
                    prev.get("source_version") == source_version
                    and int(prev.get("row_count") or 0) > 0
                    and prev.get("fields") == list(fields)
                ):
                    skipped += 1
                    continue
            except (OSError, json.JSONDecodeError):
                pass
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                payload = fetch_cs_day(
                    api_name=api_name,
                    fields=fields,
                    trade_date=day,
                    token=token,
                    transport=transport,
                )
                payload["source_version"] = source_version
                write_json(str(path), payload)
                fetched += 1
                last_error = ""
                break
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                time.sleep(min(2.0 * attempt, 6.0))
        if last_error:
            failed.append(f"{day} {last_error}")
        if index % 20 == 0 or index == len(dates):
            print(
                f"{api_name} {index}/{len(dates)} fetched={fetched} "
                f"skipped={skipped} failed={len(failed)}",
                flush=True,
            )
        if sleep_s > 0 and last_error == "":
            time.sleep(sleep_s)
    report = {
        "api_name": api_name,
        "source_version": source_version,
        "fields": list(fields),
        "planned": len(dates),
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
        "complete": not failed and fetched + skipped == len(dates),
        "output_root": str(output_root),
    }
    write_json(str(output_root / "LATEST.json"), report)
    return report


def month_starts(dates: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[tuple[int, int]] = set()
    for day in dates:
        key = (int(day[:4]), int(day[5:7]))
        if key in seen:
            continue
        seen.add(key)
        out.append(day)
    return out


def index_turnover_from_733(
    *,
    repo_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Compact turnover_rate panel from the frozen 733 daily_basic bodies."""

    pub = repo_root / (
        "data/research_runs/audited_pit_factor_v3_daily_basic_collection_v2_"
        "development_733_http_publish/points-output"
    )
    sess_dir = pub / "daily_basic_collection_sessions"
    if not sess_dir.is_dir():
        raise PathAJiaochCsError("733 daily_basic sessions missing")
    (output_root / "days").mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0
    for path in sess_dir.glob("*.json"):
        sess = json.loads(path.read_text(encoding="utf-8"))
        day = str(sess.get("trade_date") or "")[:10]
        if not (proto.TRAIN_START <= day <= proto.HOLDOUT_END):
            continue
        dest = output_root / "days" / f"{day}.json"
        if dest.is_file():
            skipped += 1
            continue
        rel = sess.get("collection_set_relative_path") or sess.get("relative_path")
        if not rel:
            for value in sess.values():
                if isinstance(value, str) and "collection_sets" in value:
                    rel = value
                    break
        if not rel:
            raise PathAJiaochCsError(f"no collection set for {day}")
        cset = json.loads((pub / rel).read_text(encoding="utf-8"))
        raw = json.loads((pub / cset["attempts"][0]["raw_relative_path"]).read_text(encoding="utf-8"))
        raw = scrub_nonfinite(raw)
        names = raw["data"]["fields"]
        items = raw["data"]["items"]
        i_code = names.index("ts_code")
        i_to = names.index("turnover_rate")
        rows = []
        for item in items:
            if not isinstance(item, list):
                continue
            code = item[i_code]
            to = item[i_to]
            if type(to) is float and not math.isfinite(to):
                to = None
            rows.append({"ts_code": code, "turnover_rate": to})
        write_json(
            str(dest),
            {
                "trade_date": day,
                "source_version": "jiaoch-turnover-from-733/v1",
                "row_count": len(rows),
                "rows": rows,
            },
        )
        written += 1
        if written % 50 == 0:
            print(f"turnover_index written={written} skipped={skipped}", flush=True)
    report = {
        "source_version": "jiaoch-turnover-from-733/v1",
        "written": written,
        "skipped": skipped,
        "output_root": str(output_root),
    }
    write_json(str(output_root / "LATEST.json"), report)
    return report
