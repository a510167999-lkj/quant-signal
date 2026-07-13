from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import jobs
from app.current_pool_history_source import (
    build_current_pool_history_summary,
    verify_current_pool_history_descriptor,
)
from app.current_pool_source import build_current_pool_descriptor
from app.current_pool_risk_source import build_current_pool_risk_descriptor
from app.research_pit_store import (
    MARKET_SESSION_DATASETS,
    MARKET_SESSION_ROW_CAPS,
    PITReceiptError,
    PITReceiptStore,
)


SESSIONS = ("2026-01-05", "2026-01-06")
BASE = datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)
DATASET_FIELDS = {
    "daily": [
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
    ],
    "adj_factor": ["ts_code", "trade_date", "adj_factor"],
    "stk_limit": ["trade_date", "ts_code", "pre_close", "up_limit", "down_limit"],
    "suspend_d": ["ts_code", "trade_date", "suspend_timing", "suspend_type"],
}


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _envelope(fields: list[str] | tuple[str, ...], rows: list[list[object]]) -> bytes:
    return json.dumps(
        {"code": 0, "msg": "", "data": {"fields": list(fields), "items": rows}},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _universe_fetch(exchange: str, list_status: str, fields: tuple[str, ...]):
    rows: list[dict[str, object]] = []
    if (exchange, list_status) == ("SSE", "L"):
        rows.append(
            {
                "ts_code": "600001.SH",
                "symbol": "600001",
                "name": "旧股",
                "exchange": "SSE",
                "market": "主板",
                "list_status": "L",
                "list_date": "19910101",
                "delist_date": None,
            }
        )
    if (exchange, list_status) == ("SZSE", "L"):
        rows.extend(
            [
                {
                    "ts_code": "300001.SZ",
                    "symbol": "300001",
                    "name": "新股",
                    "exchange": "SZSE",
                    "market": "创业板",
                    "list_status": "L",
                    "list_date": "20260106",
                    "delist_date": None,
                },
                {
                    "ts_code": "000001.SZ",
                    "symbol": "000001",
                    "name": "零历史",
                    "exchange": "SZSE",
                    "market": "主板",
                    "list_status": "L",
                    "list_date": "20260713",
                    "delist_date": None,
                },
            ]
        )
    return {
        "code": 0,
        "msg": "",
        "data": {
            "fields": list(fields),
            "items": [[row[field] for field in fields] for row in rows],
        },
    }


def _write_universe(tmp_path: Path) -> tuple[Path, dict]:
    report = build_current_pool_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:00:00+08:00",
        output_dir=tmp_path / "universe",
        fetch_partition=_universe_fetch,
    )
    path = Path(report["path"])
    return path, json.loads(path.read_text(encoding="utf-8"))


def _write_risk(tmp_path: Path, universe_path: Path) -> Path:
    def fetch_partition(api_name: str, params: dict[str, str], fields: tuple[str, ...]) -> dict:
        assert api_name in {"stock_st", "suspend_d", "namechange"}
        assert params
        return {
            "code": 0,
            "msg": "",
            "data": {"fields": list(fields), "items": []},
        }

    report = build_current_pool_risk_descriptor(
        as_of="2026-07-13",
        retrieved_at="2026-07-13T09:05:00+08:00",
        universe_path=universe_path,
        output_dir=tmp_path / "risk",
        fetch_partition=fetch_partition,
    )
    return Path(report["path"])


def _ingest_calendars(store: PITReceiptStore) -> None:
    for exchange in ("SSE", "SZSE"):
        rows = [
            [exchange, "20260105", 1, "20251231"],
            [exchange, "20260106", 1, "20260105"],
        ]
        store.ingest_tushare_response(
            dataset="trade_cal",
            partition_key=f"{exchange}:2026-01-05:2026-01-06",
            endpoint="trade_cal",
            params={
                "exchange": exchange,
                "start_date": "20260105",
                "end_date": "20260106",
            },
            raw_bytes=_envelope(["exchange", "cal_date", "is_open", "pretrade_date"], rows),
            http_status=200,
            retrieved_at="2026-01-06T18:00:00+08:00",
            row_cap=6000,
        )


def _rows_for(dataset: str, trade_date: str) -> list[list[object]]:
    symbols = ["600001.SH", "688001.SH"]
    if trade_date == "2026-01-06":
        symbols.append("300001.SZ")
    wire_date = trade_date.replace("-", "")
    if dataset == "daily":
        return [
            [symbol, wire_date, 10.0, 10.5, 9.8, 10.2, 9.9, 0.3, 3.03, 1000, 10100]
            for symbol in symbols
        ]
    if dataset == "adj_factor":
        return [[symbol, wire_date, 1.5] for symbol in symbols]
    if dataset == "stk_limit":
        return [[wire_date, symbol, 10.0, 11.0, 9.0] for symbol in symbols]
    return []


def _record_attempt(
    store: PITReceiptStore,
    dataset: str,
    trade_date: str,
    retrieved_at: str,
):
    semantics = {
        "schema_version": "tushare-wire-request/v1",
        "dataset": dataset,
        "partition_key": trade_date,
        "api_name": dataset,
        "method": "POST",
        "url": "https://jiaoch.site",
        "wire_params": {"trade_date": trade_date.replace("-", "")},
        "receipt_params": {"trade_date": trade_date},
        "fields": DATASET_FIELDS[dataset],
        "row_cap": MARKET_SESSION_ROW_CAPS[dataset],
    }
    wire_sha256 = hashlib.sha256(
        f"{dataset}:{trade_date}:{retrieved_at}".encode("utf-8")
    ).hexdigest()
    return store.record_fetch_attempt(
        dataset=dataset,
        partition_key=trade_date,
        endpoint=dataset,
        params={"trade_date": trade_date},
        fields=DATASET_FIELDS[dataset],
        wire_request_sha256=wire_sha256,
        request_body_sha256=wire_sha256,
        request_semantics=semantics,
        request_semantics_sha256=_canonical_sha256(semantics),
        raw_bytes=_envelope(DATASET_FIELDS[dataset], _rows_for(dataset, trade_date)),
        http_status=200,
        started_at=(datetime.fromisoformat(retrieved_at) - timedelta(seconds=1)).isoformat(),
        retrieved_at=retrieved_at,
        elapsed_ns=1_000_000_000,
        row_cap=MARKET_SESSION_ROW_CAPS[dataset],
        body_complete=True,
    )


def _publish_session(store: PITReceiptStore, trade_date: str, started: datetime) -> dict:
    generation = store.begin_or_resume_market_session_generation(
        started, trade_date, vintage="historical_backfill"
    )
    for index, dataset in enumerate(MARKET_SESSION_DATASETS, 1):
        attempt = _record_attempt(
            store,
            dataset,
            trade_date,
            (started + timedelta(seconds=index)).isoformat(),
        )
        store.stage_market_session_attempt(
            generation["generation_id"], dataset, attempt["attempt_id"]
        )
    return store.publish_market_session_generation(generation["generation_id"])


def _prepare_store(tmp_path: Path, *, publish_second: bool = True) -> Path:
    store_dir = tmp_path / "store"
    store = PITReceiptStore(str(store_dir))
    _ingest_calendars(store)
    _publish_session(store, SESSIONS[0], BASE)
    if publish_second:
        _publish_session(store, SESSIONS[1], BASE + timedelta(days=1))
    return store_dir


def _build(tmp_path: Path, *, publish_second: bool = True) -> tuple[dict, dict, Path]:
    universe_path, universe = _write_universe(tmp_path)
    store_dir = _prepare_store(tmp_path, publish_second=publish_second)
    report = build_current_pool_history_summary(
        universe_path=universe_path,
        store_dir=store_dir,
        history_start=SESSIONS[0],
        history_end=SESSIONS[1],
        as_of="2026-07-13",
        output_dir=tmp_path / "history",
    )
    payload = json.loads(Path(report["path"]).read_text(encoding="utf-8"))
    return report, payload, universe_path


def _resign(payload: dict) -> dict:
    candidate = json.loads(json.dumps(payload))
    unsigned = {key: value for key, value in candidate.items() if key != "descriptor_sha256"}
    candidate["descriptor_sha256"] = _canonical_sha256(unsigned)
    return candidate


def test_builds_complete_bar_intersection_including_zero_and_new_listing_under_90(
    tmp_path: Path,
) -> None:
    report, payload, universe_path = _build(tmp_path)
    universe = json.loads(universe_path.read_text(encoding="utf-8"))

    assert report["created"] is True
    assert payload["schema"] == "current-pool-history-summary/v1"
    assert payload["source_id"] == "jiaoch"
    assert payload["as_of"] == "2026-07-13"
    assert payload["universe_descriptor_sha256"] == universe["descriptor_sha256"]
    assert payload["history_start"] == "2026-01-05"
    assert payload["history_end"] == "2026-01-06"
    assert payload["open_session_count"] == 2
    assert len(payload["market_generation_refs"]) == 2
    assert all(
        set(ref)
        == {
            "trade_date",
            "generation_id",
            "manifest_sha256",
            "lineage_sha256",
            "vintage",
        }
        for ref in payload["market_generation_refs"]
    )
    assert payload["current_universe_bias"] is True
    assert payload["development_only"] is True
    assert payload["live_proof"] is False
    assert payload["production_recommendation_eligible"] is False
    assert payload["replay_eligible"] is False

    assert payload["items"] == [
        {
            "ts_code": "000001.SZ",
            "bar_count": 0,
            "daily_row_count": 0,
            "adj_factor_row_count": 0,
            "stk_limit_row_count": 0,
            "first_complete_trade_date": None,
            "last_complete_trade_date": None,
        },
        {
            "ts_code": "300001.SZ",
            "bar_count": 1,
            "daily_row_count": 1,
            "adj_factor_row_count": 1,
            "stk_limit_row_count": 1,
            "first_complete_trade_date": "2026-01-06",
            "last_complete_trade_date": "2026-01-06",
        },
        {
            "ts_code": "600001.SH",
            "bar_count": 2,
            "daily_row_count": 2,
            "adj_factor_row_count": 2,
            "stk_limit_row_count": 2,
            "first_complete_trade_date": "2026-01-05",
            "last_complete_trade_date": "2026-01-06",
        },
    ]
    verified = verify_current_pool_history_descriptor(payload, universe)
    assert verified == {
        "descriptor_sha256": payload["descriptor_sha256"],
        "item_count": 3,
        "open_session_count": 2,
    }


def test_missing_published_market_generation_fails_closed(tmp_path: Path) -> None:
    universe_path, _ = _write_universe(tmp_path)
    store_dir = _prepare_store(tmp_path, publish_second=False)

    with pytest.raises(PITReceiptError, match="missing a published generation.*2026-01-06"):
        build_current_pool_history_summary(
            universe_path=universe_path,
            store_dir=store_dir,
            history_start=SESSIONS[0],
            history_end=SESSIONS[1],
            as_of="2026-07-13",
            output_dir=tmp_path / "history",
        )


def test_store_extra_symbol_is_not_added_to_current_universe_summary(tmp_path: Path) -> None:
    _report, payload, _universe_path = _build(tmp_path)

    assert {item["ts_code"] for item in payload["items"]} == {
        "600001.SH",
        "300001.SZ",
        "000001.SZ",
    }
    assert "688001.SH" not in json.dumps(payload)


def test_verifier_rejects_tamper_extra_item_and_noncanonical_order(tmp_path: Path) -> None:
    _report, payload, universe_path = _build(tmp_path)
    universe = json.loads(universe_path.read_text(encoding="utf-8"))

    tampered = json.loads(json.dumps(payload))
    tampered["items"][0]["bar_count"] = 90
    with pytest.raises(ValueError, match="history summary descriptor rejected"):
        verify_current_pool_history_descriptor(tampered, universe)

    extra = json.loads(json.dumps(payload))
    extra["items"].append(
        {
            "ts_code": "002999.SZ",
            "bar_count": 0,
            "daily_row_count": 0,
            "adj_factor_row_count": 0,
            "stk_limit_row_count": 0,
            "first_complete_trade_date": None,
            "last_complete_trade_date": None,
        }
    )
    with pytest.raises(ValueError, match="history summary descriptor rejected"):
        verify_current_pool_history_descriptor(_resign(extra), universe)

    reversed_items = json.loads(json.dumps(payload))
    reversed_items["items"].reverse()
    with pytest.raises(ValueError, match="history summary descriptor rejected"):
        verify_current_pool_history_descriptor(_resign(reversed_items), universe)


def test_content_addressed_write_is_atomic_and_idempotent(tmp_path: Path) -> None:
    first, payload, universe_path = _build(tmp_path)
    second = build_current_pool_history_summary(
        universe_path=universe_path,
        store_dir=tmp_path / "store",
        history_start=SESSIONS[0],
        history_end=SESSIONS[1],
        as_of="2026-07-13",
        output_dir=tmp_path / "history",
    )

    assert second == {**first, "created": False}
    assert Path(second["path"]).name == f"{payload['descriptor_sha256']}.json"
    assert list((tmp_path / "history").glob("*.tmp")) == []


def test_cli_builds_history_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    universe_path, _universe = _write_universe(tmp_path)
    store_dir = _prepare_store(tmp_path)

    result = jobs.main(
        [
            "research-current-pool-build-history-summary",
            "--universe-path",
            str(universe_path),
            "--store-dir",
            str(store_dir),
            "--start-date",
            SESSIONS[0],
            "--end-date",
            SESSIONS[1],
            "--as-of",
            "2026-07-13",
            "--output-dir",
            str(tmp_path / "history"),
        ]
    )

    assert result == 0
    report = json.loads(capsys.readouterr().out)
    assert report["created"] is True
    assert Path(report["path"]).exists()


def test_rejects_universe_hash_or_filename_tamper(tmp_path: Path) -> None:
    universe_path, universe = _write_universe(tmp_path)
    store_dir = _prepare_store(tmp_path)
    universe["items"][0]["name"] = "被篡改"
    universe_path.write_text(json.dumps(universe, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="universe descriptor rejected"):
        build_current_pool_history_summary(
            universe_path=universe_path,
            store_dir=store_dir,
            history_start=SESSIONS[0],
            history_end=SESSIONS[1],
            as_of="2026-07-13",
            output_dir=tmp_path / "history",
        )


def test_verifier_rejects_generation_topology_or_root_tamper(tmp_path: Path) -> None:
    _report, payload, universe_path = _build(tmp_path)
    universe = json.loads(universe_path.read_text(encoding="utf-8"))

    bad_root = _resign({**payload, "market_generation_root_sha256": "0" * 64})
    with pytest.raises(ValueError, match="history summary descriptor rejected"):
        verify_current_pool_history_descriptor(bad_root, universe)

    bad_order = json.loads(json.dumps(payload))
    bad_order["market_generation_refs"].reverse()
    with pytest.raises(ValueError, match="history summary descriptor rejected"):
        verify_current_pool_history_descriptor(_resign(bad_order), universe)


def test_shared_cli_loader_rejects_tampered_signed_history_summary(tmp_path: Path) -> None:
    _report, payload, _universe_path = _build(tmp_path)
    payload["items"][0]["bar_count"] = 90
    path = tmp_path / "history" / f"{payload['descriptor_sha256']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="current-pool input descriptor rejected"):
        jobs._load_current_pool_descriptor(str(path), "current-pool-history-summary/v1")


def test_current_pool_audit_deep_verifies_signed_history_summary(
    tmp_path: Path,
) -> None:
    report, payload, universe_path = _build(tmp_path)
    risk_path = _write_risk(tmp_path, universe_path)
    argv = [
        "research-current-pool-audit",
        "--universe-path",
        str(universe_path),
        "--history-summary-path",
        str(report["path"]),
        "--risk-path",
        str(risk_path),
        "--output-dir",
        str(tmp_path / "audit"),
    ]
    assert jobs.main(argv) == 0

    tampered = _resign({**payload, "market_generation_root_sha256": "0" * 64})
    history_path = tmp_path / "history" / f"{tampered['descriptor_sha256']}.json"
    history_path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    argv[argv.index(str(report["path"]))] = str(history_path)

    with pytest.raises(ValueError, match="current-pool input descriptor rejected"):
        jobs.main(argv)


def test_history_end_cannot_exceed_as_of(tmp_path: Path) -> None:
    universe_path, _universe = _write_universe(tmp_path)
    store_dir = _prepare_store(tmp_path)

    with pytest.raises(ValueError, match="history_end cannot exceed as_of"):
        build_current_pool_history_summary(
            universe_path=universe_path,
            store_dir=store_dir,
            history_start=SESSIONS[0],
            history_end=SESSIONS[1],
            as_of="2026-01-05",
            output_dir=tmp_path / "history",
        )
