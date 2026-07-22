"""Coverage audit binding of per-session market generations.

WHY: ``PITReceiptStore.audit_coverage`` must prove every commonly-open
session in the audited range has a published, active, deep-verified market
session generation frozen at its latest head — fail-closed for a missing
day, a tampered stored lineage, or a tampered underlying row. The market
generation evidence (generation_id / manifest / lineage / vintage) must
flow into both the ``coverage_audit_sha256`` and the
``receipt_manifest_sha256`` so any change to a single day's evidence is
detectable. These tests are self-contained (own fixtures) so the shared
audit fixture is not coupled to them.
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from app.research_pit_store import (
    AuditedPointInTimeUniverse,
    MARKET_SESSION_DATASETS,
    MARKET_SESSION_ROW_CAPS,
    NORMALIZED_FIELDS,
    PITReceiptError,
    PITReceiptStore,
    _sha256,
)

STOCK_FIELDS = list(NORMALIZED_FIELDS["stock_basic"])

MARKET_DATASET_FIELDS = {
    "daily": list(NORMALIZED_FIELDS["daily"]),
    "adj_factor": list(NORMALIZED_FIELDS["adj_factor"]),
    "stk_limit": list(NORMALIZED_FIELDS["stk_limit"]),
    "suspend_d": list(NORMALIZED_FIELDS["suspend_d"]),
}

CLOSE_BASE = datetime(2024, 1, 2, 16, 0, tzinfo=timezone.utc) + timedelta(hours=8)


def _stock_response(rows):
    return json.dumps(
        {
            "request_id": "test",
            "code": 0,
            "msg": "",
            "data": {"fields": STOCK_FIELDS, "items": rows},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _calendar_response(rows):
    return json.dumps(
        {
            "request_id": "test",
            "code": 0,
            "msg": "",
            "data": {
                "fields": ["exchange", "cal_date", "is_open", "pretrade_date"],
                "items": rows,
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _daily_response(trade_date, rows):
    return json.dumps(
        {
            "request_id": "test",
            "code": 0,
            "msg": "",
            "data": {
                "fields": ["trade_date", "ts_code", "name", "industry", "list_date"],
                "items": [
                    [trade_date, *row, "20100101"] if len(row) == 3 else [trade_date, *row]
                    for row in rows
                ],
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _market_body(dataset, trade_date, rows):
    return json.dumps(
        {
            "request_id": f"market-{dataset}-{trade_date}",
            "code": 0,
            "msg": "",
            "data": {"fields": MARKET_DATASET_FIELDS[dataset], "items": rows},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _market_default_rows(dataset, trade_date, *, provider_suspension=True):
    wire = trade_date.replace("-", "")
    if dataset == "daily":
        return [["600001.SH", wire, 10.0, 10.5, 9.8, 10.2, 9.9, 0.3, 3.03, 1000, 10100]]
    if dataset == "adj_factor":
        return [["600001.SH", wire, 1.5]]
    if dataset == "stk_limit":
        return [[wire, "600001.SH", 10.0, 11.0, 9.0]]
    if provider_suspension and dataset == "suspend_d" and trade_date == "2024-01-02":
        return [["000002.SZ", wire, "09:30:00", "S"]]
    return []


def _record_market_attempt(
    store, dataset, trade_date, retrieved_at, *, provider_suspension=True
):
    semantics = {
        "schema_version": "tushare-wire-request/v1",
        "dataset": dataset,
        "partition_key": trade_date,
        "api_name": dataset,
        "method": "POST",
        "url": "https://api.tushare.pro",
        "wire_params": {"trade_date": trade_date.replace("-", "")},
        "receipt_params": {"trade_date": trade_date},
        "fields": MARKET_DATASET_FIELDS[dataset],
        "row_cap": MARKET_SESSION_ROW_CAPS[dataset],
    }
    raw = _market_body(
        dataset,
        trade_date,
        _market_default_rows(
            dataset, trade_date, provider_suspension=provider_suspension
        ),
    )
    wire_sha256 = hashlib.sha256(
        f"{dataset}:{trade_date}:{retrieved_at}".encode("utf-8")
    ).hexdigest()
    semantics_sha256 = hashlib.sha256(
        json.dumps(
            semantics, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return store.record_fetch_attempt(
        dataset=dataset,
        partition_key=trade_date,
        endpoint=dataset,
        params={"trade_date": trade_date},
        fields=MARKET_DATASET_FIELDS[dataset],
        wire_request_sha256=wire_sha256,
        request_body_sha256=wire_sha256,
        request_semantics=semantics,
        request_semantics_sha256=semantics_sha256,
        raw_bytes=raw,
        http_status=200,
        started_at=(
            datetime.fromisoformat(retrieved_at) - timedelta(seconds=1)
        ).isoformat(),
        retrieved_at=retrieved_at,
        elapsed_ns=1_000_000_000,
        row_cap=MARKET_SESSION_ROW_CAPS[dataset],
        body_complete=True,
    )


def _publish_market_session(store, trade_date, started, *, provider_suspension=True):
    generation = store.begin_or_resume_market_session_generation(started, trade_date)
    for index, dataset in enumerate(MARKET_SESSION_DATASETS, 1):
        attempt = _record_market_attempt(
            store,
            dataset,
            trade_date,
            (started + timedelta(seconds=index)).isoformat(),
            provider_suspension=provider_suspension,
        )
        store.stage_market_session_attempt(generation["generation_id"], dataset, attempt["attempt_id"])
    return store.publish_market_session_generation(generation["generation_id"])


def _ingest_stock_and_calendar(store):
    generation = store.begin_or_resume_stock_basic_generation(
        "2024-01-01T16:00:00+08:00"
    )
    for exchange in ("SSE", "SZSE"):
        for status in ("L", "D", "P", "G"):
            rows = []
            if (exchange, status) == ("SSE", "L"):
                rows = [
                    ["600001.SH", "600001", "A", "SSE", "主板", "L", "20100101", None]
                ]
            elif (exchange, status) == ("SZSE", "D"):
                rows = [
                    [
                        "000002.SZ",
                        "000002",
                        "B",
                        "SZSE",
                        "主板",
                        "D",
                        "20100101",
                        "20240103",
                    ]
                ]
            partition = f"{exchange}:{status}"
            params = {"exchange": exchange, "list_status": status}
            semantics = {
                "schema_version": "tushare-wire-request/v1",
                "dataset": "stock_basic",
                "partition_key": partition,
                "api_name": "stock_basic",
                "method": "POST",
                "url": "https://api.tushare.pro",
                "wire_params": dict(params),
                "receipt_params": dict(params),
                "fields": STOCK_FIELDS,
                "row_cap": 6000,
            }
            attempt = store.record_fetch_attempt(
                dataset="stock_basic",
                partition_key=partition,
                endpoint="stock_basic",
                params=params,
                fields=STOCK_FIELDS,
                wire_request_sha256=hashlib.sha256(
                    f"stock-generation:{partition}".encode()
                ).hexdigest(),
                raw_bytes=_stock_response(rows),
                http_status=200,
                started_at="2024-01-01T15:59:59+08:00",
                retrieved_at="2024-01-01T16:00:00+08:00",
                elapsed_ns=1_000_000,
                row_cap=6000,
                body_complete=True,
                request_semantics=semantics,
            )
            store.stage_stock_basic_attempt(
                generation["generation_id"], partition, attempt["attempt_id"]
            )
        store.ingest_tushare_response(
            dataset="trade_cal",
            partition_key=f"{exchange}:2024-01-02:2024-01-03",
            endpoint="trade_cal",
            params={"exchange": exchange, "start_date": "20240102", "end_date": "20240103"},
            raw_bytes=_calendar_response(
                [
                    [exchange, "20240102", 1, "20231229"],
                    [exchange, "20240103", 1, "20240102"],
                ]
            ),
            http_status=200,
            retrieved_at="2024-01-01T16:00:00+08:00",
            row_cap=10000,
        )
    store.publish_stock_basic_generation(generation["generation_id"])
    for trade_date, rows in (
        ("2024-01-02", [["600001.SH", "A", "银行"], ["000002.SZ", "B", "地产"]]),
        ("2024-01-03", [["600001.SH", "A", "银行"]]),
    ):
        store.ingest_tushare_response(
            dataset="bak_basic",
            partition_key=trade_date,
            endpoint="bak_basic",
            params={"trade_date": trade_date.replace("-", "")},
            raw_bytes=_daily_response(trade_date.replace("-", ""), rows),
            http_status=200,
            retrieved_at=f"{trade_date}T16:00:00+08:00",
            row_cap=7000,
        )


def _build_full_store(
    tmp_path, *, omit_day_two_market=False, provider_suspension=True
):
    store = PITReceiptStore(str(tmp_path / "store"))
    _ingest_stock_and_calendar(store)
    first = _publish_market_session(
        store,
        "2024-01-02",
        CLOSE_BASE,
        provider_suspension=provider_suspension,
    )
    second = None
    if not omit_day_two_market:
        second = _publish_market_session(
            store, "2024-01-03", CLOSE_BASE + timedelta(days=1)
        )
    return store, first, second


def _cninfo_pdf(text):
    encoded_text = text.encode("utf-16-be").hex().upper()
    content = f"BT /F1 12 Tf 72 720 Td <{encoded_text}> Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        (
            b"<< /Length "
            + str(len(content)).encode("ascii")
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        ),
        (
            b"<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light "
            b"/Encoding /UniGB-UCS2-H /DescendantFonts [6 0 R] >>"
        ),
        (
            b"<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
            b"/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> >>"
        ),
    ]
    payload = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(payload))
        payload.extend(f"{index} 0 obj\n".encode("ascii"))
        payload.extend(obj)
        payload.extend(b"\nendobj\n")
    xref_offset = len(payload)
    payload.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    payload.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        payload.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    payload.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return bytes(payload)


def _ingest_official_interval(store, *, ts_code="000002.SZ"):
    symbol = ts_code.split(".", 1)[0]
    return store.ingest_cninfo_suspension_interval(
        ts_code=ts_code,
        start_raw_bytes=_cninfo_pdf(
            f"证券代码：{symbol} 公告编号：2024-001 某某股份有限公司 "
            "关于重大事项的停牌公告 "
            f"公司股票（证券代码：{symbol}）自 2024 年 1 月 2 日开市时起开始停牌。"
        ),
        start_source_url=(
            "https://static.cninfo.com.cn/finalpage/2024-01-02/1000000001.PDF"
        ),
        start_published_at="2024-01-02T00:00:00+08:00",
        start_retrieved_at="2024-01-02T00:05:00+08:00",
        resume_raw_bytes=_cninfo_pdf(
            f"证券代码：{symbol} 公告编号：2024-002 某某股份有限公司 "
            "关于重大事项暨公司股票复牌的公告 "
            f"公司股票（证券代码：{symbol}）将于 2024 年 1 月 3 日开市起复牌。"
        ),
        resume_source_url=(
            "https://static.cninfo.com.cn/finalpage/2024-01-03/1000000002.PDF"
        ),
        resume_published_at="2024-01-03T00:00:00+08:00",
        resume_retrieved_at="2024-01-03T00:05:00+08:00",
    )


def _market_receipt_sha256(ref):
    return _sha256(
        {
            "generation_id": ref["generation_id"],
            "manifest_sha256": ref["manifest_sha256"],
            "lineage_sha256": ref["lineage_sha256"],
            "vintage": ref["vintage"],
        }
    )


def test_two_day_audit_binds_per_session_market_generation_coverage(tmp_path):
    store, first, second = _build_full_store(tmp_path)

    audit = store.audit_coverage(
        start_date="2024-01-02",
        end_date="2024-01-03",
        calendar_exchanges=("SSE", "SZSE"),  # 测试 fixture 注入了两市 calendar
    )

    assert audit["status"] == "passed"
    assert audit["final_oos_eligible"] is False
    assert audit["market_generation_count"] == 2
    refs = audit["market_generation_refs"]
    assert [ref["trade_date"] for ref in refs] == ["2024-01-02", "2024-01-03"]
    assert all(
        set(ref)
        == {"trade_date", "generation_id", "manifest_sha256", "lineage_sha256", "vintage"}
        for ref in refs
    )
    assert refs[0]["generation_id"] == first["generation_id"]
    assert refs[1]["generation_id"] == second["generation_id"]
    assert refs[0]["manifest_sha256"] == first["manifest_sha256"]
    assert refs[0]["lineage_sha256"] == first["lineage_sha256"]
    assert refs[0]["vintage"] == "live_forward"
    assert audit["market_generation_root_sha256"] == _sha256(refs)
    # WHY: the manifest count must reflect the two market generation refs
    # alongside the calendar, daily, and stock generation receipts (2+2+1+2=7).
    assert audit["verified_receipt_count"] == 7


def test_audit_fails_when_one_day_market_generation_is_missing(tmp_path):
    store, _first, _second = _build_full_store(tmp_path, omit_day_two_market=True)

    with pytest.raises(PITReceiptError, match="missing"):
        store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")


def test_audit_fails_when_market_stored_lineage_is_tampered(tmp_path):
    store, first, _second = _build_full_store(tmp_path)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE market_session_generations SET lineage_sha256 = ? "
            "WHERE generation_id = ?",
            ("0" * 64, first["generation_id"]),
        )

    with pytest.raises(PITReceiptError, match="lineage"):
        store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")


def test_audit_fails_when_market_underlying_row_is_tampered(tmp_path):
    store, first, _second = _build_full_store(tmp_path)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE market_session_generation_rows_daily SET close = 999.0 "
            "WHERE generation_id = ?",
            (first["generation_id"],),
        )

    with pytest.raises(PITReceiptError):
        store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")


def test_changing_one_day_market_evidence_changes_audit_and_manifest(tmp_path):
    store, first, _second = _build_full_store(tmp_path)
    before = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")
    assert before["market_generation_refs"][0]["generation_id"] == first["generation_id"]

    # Republish day one with fresh evidence (new retrieval instants → new head).
    republished = _publish_market_session(
        store, "2024-01-02", CLOSE_BASE + timedelta(days=3)
    )
    assert republished["generation_id"] != first["generation_id"]
    after = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    assert after["market_generation_refs"][0]["generation_id"] == republished["generation_id"]
    assert after["market_generation_refs"][0]["manifest_sha256"] != first["manifest_sha256"]
    assert after["coverage_audit_sha256"] != before["coverage_audit_sha256"]
    assert after["receipt_manifest_sha256"] != before["receipt_manifest_sha256"]


def test_official_interval_explains_only_missing_daily_inside_half_open_range(tmp_path):
    store, _first, _second = _build_full_store(
        tmp_path, provider_suspension=False
    )
    with pytest.raises(PITReceiptError, match=r"daily=\['000002.SZ'\]"):
        store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    interval = _ingest_official_interval(store)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    assert interval["start_date"] == "2024-01-02"
    assert interval["resume_date"] == "2024-01-03"
    assert audit["official_suspension_interval_count"] == 1
    assert audit["official_suspension_refs"][0]["evidence_root_sha256"] == interval[
        "evidence_root_sha256"
    ]
    assert audit["official_suspension_evidence_root_sha256"] == _sha256(
        audit["official_suspension_refs"]
    )
    first = audit["market_reconciliations"][0]
    assert first["provider_suspension_count"] == 0
    assert first["official_suspension_count"] == 1
    assert first["missing_daily_explained_by_provider_count"] == 0
    assert first["missing_daily_explained_by_official_count"] == 1
    assert first["unexplained_missing_daily_count"] == 0
    second = audit["market_reconciliations"][1]
    assert second["official_suspension_count"] == 0


def test_official_interval_conflicting_with_daily_bar_fails_closed(tmp_path):
    store, _first, _second = _build_full_store(tmp_path)
    _ingest_official_interval(store, ts_code="600001.SH")

    with pytest.raises(PITReceiptError, match="conflicts with daily market row"):
        store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")


def test_official_interval_raw_pdf_tamper_fails_closed(tmp_path):
    store, _first, _second = _build_full_store(
        tmp_path, provider_suspension=False
    )
    _ingest_official_interval(store)
    with sqlite3.connect(store.database_path) as connection:
        raw_path = connection.execute(
            "SELECT raw_path FROM official_notice_receipts "
            "WHERE notice_role = 'start'"
        ).fetchone()[0]
    (store.root / raw_path).write_bytes(b"tampered")

    with pytest.raises(PITReceiptError, match="official suspension raw"):
        store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")


def test_published_artifact_carries_and_replays_official_suspension_evidence(tmp_path):
    store, _first, _second = _build_full_store(
        tmp_path, provider_suspension=False
    )
    interval = _ingest_official_interval(store)
    audit = store.audit_coverage(start_date="2024-01-02", end_date="2024-01-03")

    artifact = store.publish_universe_artifact(
        str(tmp_path / "published"),
        start_date="2024-01-02",
        end_date="2024-01-03",
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )

    with sqlite3.connect(artifact["path"]) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM official_notice_receipts"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT evidence_root_sha256 FROM official_suspension_intervals"
        ).fetchone()[0] == interval["evidence_root_sha256"]
        raw_paths = [
            row[0]
            for row in connection.execute(
                "SELECT raw_path FROM official_notice_receipts ORDER BY notice_role"
            )
        ]
    artifact_root = Path(artifact["path"]).parent
    assert all(artifact_root.joinpath(path).is_file() for path in raw_paths)
    universe = AuditedPointInTimeUniverse.from_legacy_unbound_file(
        artifact["path"],
        expected_coverage_audit_sha256=audit["coverage_audit_sha256"],
    )
    try:
        assert universe.manifest["official_suspensions"] == {
            "count": 1,
            "root_sha256": audit["official_suspension_evidence_root_sha256"],
            "refs": audit["official_suspension_refs"],
        }
    finally:
        universe.close()
