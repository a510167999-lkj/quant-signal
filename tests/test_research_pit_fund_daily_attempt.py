"""Persistence + promotion-fail-closed tests for the ``fund_daily`` attempt path.

These tests exercise the store, unlike ``test_research_pit_fund_daily.py`` which
pins only the pure normalisation layer. ``fund_daily`` is NOT yet wired into a
generation stage, so:

* ``record_fetch_attempt`` must still capture a raw CAS + attempt row at the same
  rigour as every other dataset (canonical partition key, frozen fields,
  official row cap, credential scrub, byte limit, request hashes).
* ``promote_fetch_attempt`` must refuse — fail closed BEFORE any receipt/source
  row is written — with a durable ``generation_required`` terminal event.
* body validation (empty / duplicate / bad OHLC / cap-reached) lives in a pure,
  read-only ``validate_fund_daily_attempt`` helper that the future generation
  stage will reuse; ``record_fetch_attempt`` itself only captures and must NOT
  parse the response body.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from app.research_pit_store import (
    PITReceiptError,
    PITReceiptStore,
    validate_fund_daily_attempt,
)
from app.research_proxy_data import (
    ETF_PROXY_REQUIRED_SYMBOLS,
    FUND_DAILY_FIELDS,
    FUND_DAILY_ROW_CAP,
)

PROXY_SYMBOL = ETF_PROXY_REQUIRED_SYMBOLS[0]  # 510300.SH
THIRD_ETF = "510050.SH"  # real ETF, deliberately outside the frozen contract
STARTED_AT = "2024-01-02T15:59:59+08:00"
RETRIEVED_AT = "2024-01-02T16:00:00+08:00"
PARTITION_KEY = f"{PROXY_SYMBOL}:2024-01-02:2024-01-03"


# --- native response builders ------------------------------------------------


def _fund_row(
    ts_code: str = PROXY_SYMBOL,
    trade_date: str = "20240102",
    *,
    bad_high: bool = False,
) -> list:
    row = [
        ts_code,
        trade_date,
        10.0,  # open
        10.5,  # high
        9.8,  # low
        10.2,  # close
        10.0,  # pre_close
        0.2,  # change
        2.0,  # pct_chg
        1000.0,  # vol
        10000.0,  # amount
    ]
    if bad_high:
        row[3] = 9.0  # high below open/close -> invalid OHLC
    return row


def _response(items, *, fields=None, code=0, msg=""):
    return json.dumps(
        {
            "code": code,
            "msg": msg,
            "data": {"fields": list(fields or FUND_DAILY_FIELDS), "items": items},
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _params(ts_code: str = PROXY_SYMBOL, start: str = "2024-01-02", end: str = "2024-01-03"):
    return {"ts_code": ts_code, "start_date": start, "end_date": end}


def _wire_sha():
    return hashlib.sha256(b"fund_daily-wire-request").hexdigest()


def _semantics(**overrides):
    base = {
        "schema_version": "tushare-wire-request/v1",
        "dataset": "fund_daily",
        "partition_key": PARTITION_KEY,
        "api_name": "fund_daily",
        "method": "POST",
        "receipt_params": _params(),
        "wire_params": {
            "ts_code": PROXY_SYMBOL,
            "start_date": "20240102",
            "end_date": "20240103",
        },
        "fields": list(FUND_DAILY_FIELDS),
        "row_cap": FUND_DAILY_ROW_CAP,
    }
    base.update(overrides)
    return base


def _record(
    store,
    *,
    raw_bytes,
    params=None,
    fields=FUND_DAILY_FIELDS,
    partition_key=PARTITION_KEY,
    row_cap=FUND_DAILY_ROW_CAP,
    request_semantics=None,
):
    return store.record_fetch_attempt(
        dataset="fund_daily",
        partition_key=partition_key,
        endpoint="fund_daily",
        params=params or _params(),
        fields=fields,
        wire_request_sha256=_wire_sha(),
        raw_bytes=raw_bytes,
        http_status=200,
        started_at=STARTED_AT,
        retrieved_at=RETRIEVED_AT,
        elapsed_ns=1_000_000_000,
        row_cap=row_cap,
        body_complete=True,
        request_semantics=request_semantics,
    )


def _attempt(store, attempt_id):
    return next(row for row in store.fetch_attempts() if row["attempt_id"] == attempt_id)


# --- record: capture-grade persistence --------------------------------------


def test_record_persists_immutable_raw_cas_and_attempt_row(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    raw = _response([_fund_row(PROXY_SYMBOL, "20240102"), _fund_row(PROXY_SYMBOL, "20240103")])

    attempt = _record(store, raw_bytes=raw)

    assert attempt["outcome"] == "captured"
    assert attempt["dataset"] == "fund_daily"
    assert attempt["partition_key"] == PARTITION_KEY
    assert attempt["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert attempt["raw_bytes"] == len(raw)
    assert attempt["fields"] == list(FUND_DAILY_FIELDS)
    assert attempt["row_cap"] == FUND_DAILY_ROW_CAP
    assert attempt["params"] == {
        "ts_code": PROXY_SYMBOL,
        "start_date": "2024-01-02",
        "end_date": "2024-01-03",
    }
    # raw file is content-addressed and byte-for-byte immutable
    raw_path = store.root / attempt["raw_path"]
    assert raw_path.read_bytes() == raw
    assert raw_path.stem == attempt["raw_sha256"]
    assert len(list(store.raw_root.rglob("*.json"))) == 1


def test_record_scrubs_configured_token_from_body_params_and_fields(tmp_path, monkeypatch):
    token = "live-tushare-token-must-never-persist"
    monkeypatch.setenv("TUSHARE_TOKEN", token)
    store = PITReceiptStore(str(tmp_path / "store"))

    # token leaking into the response body
    with pytest.raises(PITReceiptError, match="credential|token"):
        _record(store, raw_bytes=_response([_fund_row()]) + token.encode("utf-8"))

    # token leaking into params (ts_code surface)
    with pytest.raises(PITReceiptError, match="credential|token"):
        _record(store, raw_bytes=_response([_fund_row()]), params=_params(ts_code=token))

    # token leaking into the frozen fields surface
    with pytest.raises(PITReceiptError, match="credential|token"):
        _record(
            store,
            raw_bytes=_response([_fund_row()]),
            fields=(*FUND_DAILY_FIELDS[:-1], token),
        )

    # nothing was persisted: no attempt row, no raw file
    assert store.fetch_attempts() == []
    assert list(store.raw_root.rglob("*.json")) == []


def test_record_rejects_third_etf_symbol(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with pytest.raises(PITReceiptError):
        _record(store, raw_bytes=_response([_fund_row()]), params=_params(ts_code=THIRD_ETF))
    assert store.fetch_attempts() == []


@pytest.mark.parametrize(
    "params",
    [
        {"ts_code": PROXY_SYMBOL, "start_date": "2024-01-02"},  # missing end_date
        {  # extra param
            "ts_code": PROXY_SYMBOL,
            "start_date": "2024-01-02",
            "end_date": "2024-01-03",
            "trade_date": "20240102",
        },
        {  # inverted window
            "ts_code": PROXY_SYMBOL,
            "start_date": "2024-01-03",
            "end_date": "2024-01-02",
        },
    ],
)
def test_record_rejects_non_contract_params(tmp_path, params):
    store = PITReceiptStore(str(tmp_path / "store"))
    with pytest.raises(PITReceiptError):
        _record(store, raw_bytes=_response([_fund_row()]), params=params)
    assert store.fetch_attempts() == []


def test_record_rejects_non_frozen_fields(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with pytest.raises(PITReceiptError):
        _record(
            store,
            raw_bytes=_response([_fund_row()]),
            fields=list(FUND_DAILY_FIELDS[:-1]) + ["surplus"],
        )
    assert store.fetch_attempts() == []


def test_record_rejects_non_canonical_partition_key(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with pytest.raises(PITReceiptError, match="partition"):
        _record(
            store,
            raw_bytes=_response([_fund_row()]),
            partition_key="not-the-canonical-key",
        )
    assert store.fetch_attempts() == []


def test_record_rejects_non_official_row_cap(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with pytest.raises(PITReceiptError, match="row cap"):
        _record(store, raw_bytes=_response([_fund_row()]), row_cap=FUND_DAILY_ROW_CAP - 1)
    assert store.fetch_attempts() == []


def test_record_rejects_wire_semantics_that_disagree_with_contract(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with pytest.raises(PITReceiptError, match="semantics"):
        _record(
            store,
            raw_bytes=_response([_fund_row()]),
            request_semantics=_semantics(fields=["not", "the", "contract"]),
        )
    assert store.fetch_attempts() == []


def test_record_accepts_a_correct_wire_semantics_envelope(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    attempt = _record(
        store,
        raw_bytes=_response([_fund_row()]),
        request_semantics=_semantics(),
    )
    assert attempt["outcome"] == "captured"
    assert attempt["request_semantics"]["schema_version"] == "tushare-wire-request/v1"


# --- validate_fund_daily_attempt: read-only body classification -------------


def test_validate_helper_classifies_a_complete_in_contract_body(tmp_path):
    raw = _response([_fund_row(PROXY_SYMBOL, "20240102"), _fund_row(PROXY_SYMBOL, "20240103")])
    result = validate_fund_daily_attempt(raw, _params(), FUND_DAILY_ROW_CAP)
    assert result == {"status": "valid", "row_count": 2}


def test_validate_helper_marks_cap_reached_at_official_row_cap(tmp_path):
    items = [_fund_row(PROXY_SYMBOL, f"2024{d:04d}") for d in range(1, FUND_DAILY_ROW_CAP + 1)]
    raw = _response(items)
    result = validate_fund_daily_attempt(raw, _params(), FUND_DAILY_ROW_CAP)
    assert result == {"status": "cap_reached", "row_count": FUND_DAILY_ROW_CAP}


def test_validate_helper_rejects_empty_items(tmp_path):
    result = validate_fund_daily_attempt(_response([]), _params(), FUND_DAILY_ROW_CAP)
    assert result["status"] == "invalid_body"
    assert result["row_count"] == 0


def test_validate_helper_rejects_duplicate_session(tmp_path):
    raw = _response([_fund_row(PROXY_SYMBOL, "20240102"), _fund_row(PROXY_SYMBOL, "20240102")])
    result = validate_fund_daily_attempt(raw, _params(), FUND_DAILY_ROW_CAP)
    assert result["status"] == "invalid_body"


def test_validate_helper_rejects_invalid_ohlc(tmp_path):
    raw = _response([_fund_row(PROXY_SYMBOL, "20240102", bad_high=True)])
    result = validate_fund_daily_attempt(raw, _params(), FUND_DAILY_ROW_CAP)
    assert result["status"] == "invalid_body"


def test_validate_helper_rejects_malformed_envelope(tmp_path):
    result = validate_fund_daily_attempt(b'{"unterminated":', _params(), FUND_DAILY_ROW_CAP)
    assert result["status"] == "invalid_body"


# --- promote: fail closed, idempotent, no legacy receipts -------------------


def test_promote_fund_daily_fails_closed_with_generation_required(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    attempt = _record(store, raw_bytes=_response([_fund_row()]))
    assert attempt["promotion_events"] == []

    result = store.promote_fetch_attempt(attempt["attempt_id"])

    body = json.dumps(result, ensure_ascii=False)
    assert "fund_daily generation required" in body
    assert result["attempt_id"] == attempt["attempt_id"]
    # no receipt / source row was written
    assert store.receipt_count() == 0
    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM receipts WHERE dataset='fund_daily'"
            ).fetchone()[0]
            == 0
        )
    persisted = _attempt(store, attempt["attempt_id"])
    assert persisted["terminal_status"] == "generation_required"
    assert [event["status"] for event in persisted["promotion_events"]] == [
        "generation_required"
    ]


def test_promote_fund_daily_is_idempotent_across_repeated_calls(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    attempt = _record(store, raw_bytes=_response([_fund_row()]))

    first = store.promote_fetch_attempt(attempt["attempt_id"])
    second = store.promote_fetch_attempt(attempt["attempt_id"])

    assert first == second
    assert store.receipt_count() == 0
    persisted = _attempt(store, attempt["attempt_id"])
    # only one terminal event ever
    assert [event["status"] for event in persisted["promotion_events"]] == [
        "generation_required"
    ]


def test_promote_fund_daily_cap_reached_body_still_refuses_and_writes_no_receipt(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    items = [_fund_row(PROXY_SYMBOL, f"2024{d:04d}") for d in range(1, FUND_DAILY_ROW_CAP + 1)]
    attempt = _record(store, raw_bytes=_response(items))

    result = store.promote_fetch_attempt(attempt["attempt_id"])

    assert "fund_daily generation required" in json.dumps(result, ensure_ascii=False)
    assert store.receipt_count() == 0


def test_fund_daily_attempt_and_refusal_survive_reopen(tmp_path):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    raw = _response([_fund_row()])
    attempt = _record(store, raw_bytes=raw)
    store.promote_fetch_attempt(attempt["attempt_id"])

    reopened = PITReceiptStore(str(root))
    reopened_attempt = _attempt(reopened, attempt["attempt_id"])
    assert reopened_attempt["outcome"] == "captured"
    assert reopened_attempt["terminal_status"] == "generation_required"
    assert reopened_attempt["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    # raw evidence is durable on disk
    assert (reopened.root / reopened_attempt["raw_path"]).read_bytes() == raw
    # still no legacy receipts after reopen
    assert reopened.receipt_count() == 0
    with sqlite3.connect(reopened.database_path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert list(connection.execute("PRAGMA foreign_key_check")) == []
