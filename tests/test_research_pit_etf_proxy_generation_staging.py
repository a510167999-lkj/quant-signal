"""ETF proxy ``fund_daily`` shard staging for one generation.

These tests pin ``PITReceiptStore.stage_etf_proxy_generation_attempt``: the
strict, atomic, fail-closed path that promotes one captured ``fund_daily``
attempt into an immutable ETF proxy shard + normalized rows + a single
``etf_proxy_staged`` promotion event. They deliberately do NOT exercise
publishing, the collector, artifact copy, or backtest -- only staging.

The stager must re-derive every canonical invariant from the persisted attempt
and the frozen contract (no trust, no drift). Foreign keys stay ON; we never
``PRAGMA foreign_keys=OFF`` and never monkeypatch the store -- corruption is
simulated by direct SQL mutation of already-stored rows, the same way a torn
write would look on disk.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app import research_pit_store
from app.research_pit_store import PITReceiptError, PITReceiptStore
from app.research_proxy_data import (
    ETF_PROXY_REQUIRED_SYMBOLS,
    FUND_DAILY_FIELDS,
    FUND_DAILY_ROW_CAP,
)

# WHY a fixed +08:00 base: the staging window reasons about elapsed wall-clock
# seconds between the generation's started_at and the attempt's retrieved_at.
# A pinned base hits the < / == / > 3600 boundaries exactly without drift.
BASE_NOW = datetime(2024, 1, 3, 16, 0, 0, tzinfo=timezone(timedelta(hours=8)))
START = "2024-01-02"
END = "2024-03-31"


def _later(seconds: int) -> datetime:
    return BASE_NOW + timedelta(seconds=seconds)


def _row(symbol: str, date: str, close: float = 10.0, pre_close: float = 9.9) -> dict:
    """A fund_daily row that satisfies the OHLC geometry the normalizer enforces."""

    return {
        "ts_code": symbol,
        "trade_date": date,
        "open": close,
        "high": close + 0.1,
        "low": close - 0.1,
        "close": close,
        "pre_close": pre_close,
        "change": close - pre_close,
        "pct_chg": (close - pre_close) / pre_close * 100.0,
        "vol": 1000.0,
        "amount": 10000.0,
    }


def _canonical_semantics(partition_key: str, params: dict) -> dict:
    """The exact canonical wire-request semantics record_fetch_attempt pins."""

    return {
        "schema_version": "tushare-wire-request/v1",
        "dataset": "fund_daily",
        "partition_key": partition_key,
        "api_name": "fund_daily",
        "method": "POST",
        "receipt_params": dict(params),
        "wire_params": research_pit_store._canonical_wire_params("fund_daily", params),
        "fields": list(FUND_DAILY_FIELDS),
        "row_cap": FUND_DAILY_ROW_CAP,
    }


def _envelope(rows: list[dict]) -> bytes:
    fields = list(FUND_DAILY_FIELDS)
    items = [[row[field] for field in fields] for row in rows]
    return json.dumps(
        {"code": 0, "msg": "", "data": {"fields": fields, "items": items}}
    ).encode("utf-8")


def _record_attempt(
    store: PITReceiptStore,
    *,
    symbol: str,
    start: str = START,
    end: str = END,
    retrieved_at: datetime,
    rows: list[dict],
    http_status: int = 200,
    body_complete: bool = True,
    error_kind: str | None = None,
    raw_override: bytes | None = None,
) -> str:
    """Record one fully-canonical fund_daily attempt; return its attempt_id."""

    params = {"ts_code": symbol, "start_date": start, "end_date": end}
    partition_key = research_pit_store._canonical_partition_key("fund_daily", params)
    raw_bytes = raw_override if raw_override is not None else _envelope(rows)
    attempt = store.record_fetch_attempt(
        dataset="fund_daily",
        partition_key=partition_key,
        endpoint="fund_daily",
        params=params,
        fields=list(FUND_DAILY_FIELDS),
        wire_request_sha256=hashlib.sha256(b"wire").hexdigest(),
        raw_bytes=raw_bytes,
        http_status=http_status,
        started_at=retrieved_at,
        retrieved_at=retrieved_at,
        row_cap=FUND_DAILY_ROW_CAP,
        body_complete=body_complete,
        error_kind=error_kind,
        request_semantics=_canonical_semantics(partition_key, params),
    )
    return attempt["attempt_id"]


def _begin(store: PITReceiptStore, *, start: str = START, end: str = END) -> dict:
    return store.begin_or_resume_etf_proxy_generation(BASE_NOW, start, end)


# ---------------------------------------------------------------------------
# Success: row shape, order, promotion event
# ---------------------------------------------------------------------------


def test_stages_first_symbol_persists_shard_rows_and_event(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    rows = [_row(symbol, "2024-01-02"), _row(symbol, "2024-01-03", close=10.5)]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(30), rows=rows
    )

    result = store.stage_etf_proxy_generation_attempt(
        generation["generation_id"], symbol, attempt_id
    )

    assert result["status"] == "staged"
    assert result["staged_symbols"] == [symbol]
    # final OOS must stay False forever -- staging never flips it.
    assert result["final_oos_eligible"] is False

    with store._connect() as connection:
        shard = dict(
            connection.execute(
                "SELECT * FROM etf_proxy_generation_shards "
                "WHERE generation_id = ? AND symbol = ?",
                (generation["generation_id"], symbol),
            ).fetchone()
        )
    attempt = store._fetch_attempt(attempt_id)
    assert shard["attempt_id"] == attempt_id
    assert shard["request_semantics_sha256"] == attempt["request_semantics_sha256"]
    assert shard["raw_sha256"] == attempt["raw_sha256"]
    assert shard["raw_bytes"] == attempt["raw_bytes"]
    assert shard["row_count"] == len(rows)
    assert shard["normalized_sha256"] != shard["raw_sha256"]

    # Persisted rows carry every frozen fund_daily field, in trade_date order.
    with store._connect() as connection:
        persisted = [
            dict(row)
            for row in connection.execute(
                "SELECT ts_code, trade_date, open, high, low, close, pre_close, "
                "change, pct_chg, vol, amount "
                "FROM etf_proxy_generation_rows "
                "WHERE generation_id = ? AND ts_code = ? ORDER BY trade_date",
                (generation["generation_id"], symbol),
            )
        ]
        event = dict(
            connection.execute(
                "SELECT status, details_json FROM fetch_promotion_events "
                "WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        )
    assert [row["trade_date"] for row in persisted] == ["2024-01-02", "2024-01-03"]
    for persisted_row, source in zip(persisted, rows):
        for field in FUND_DAILY_FIELDS:
            assert persisted_row[field] == source[field]

    details = json.loads(event["details_json"])
    assert event["status"] == "etf_proxy_staged"
    assert details["generation_id"] == generation["generation_id"]
    assert details["symbol"] == symbol
    assert details["raw_sha256"] == attempt["raw_sha256"]
    assert details["normalized_sha256"] == shard["normalized_sha256"]


def test_two_symbols_stage_independently_and_keep_frozen_order(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    # Stage in reverse frozen order to prove the view sorts, not appends.
    second = ETF_PROXY_REQUIRED_SYMBOLS[1]
    first = ETF_PROXY_REQUIRED_SYMBOLS[0]
    att_second = _record_attempt(
        store, symbol=second, retrieved_at=_later(10), rows=[_row(second, START)]
    )
    att_first = _record_attempt(
        store, symbol=first, retrieved_at=_later(20), rows=[_row(first, START)]
    )
    store.stage_etf_proxy_generation_attempt(generation["generation_id"], second, att_second)
    store.stage_etf_proxy_generation_attempt(generation["generation_id"], first, att_first)

    resumed = store.begin_or_resume_etf_proxy_generation(_later(30), START, END)
    assert resumed["staged_symbols"] == list(ETF_PROXY_REQUIRED_SYMBOLS)
    with store._connect() as connection:
        shard_count = connection.execute(
            "SELECT COUNT(*) FROM etf_proxy_generation_shards "
            "WHERE generation_id = ?",
            (generation["generation_id"],),
        ).fetchone()[0]
    assert shard_count == 2


# ---------------------------------------------------------------------------
# Idempotency: same attempt reused, different attempt conflicts
# ---------------------------------------------------------------------------


def test_same_attempt_is_idempotent_and_returns_reused(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(15), rows=[_row(symbol, START)]
    )
    first = store.stage_etf_proxy_generation_attempt(
        generation["generation_id"], symbol, attempt_id
    )
    second = store.stage_etf_proxy_generation_attempt(
        generation["generation_id"], symbol, attempt_id
    )

    assert first["status"] == "staged"
    assert second["status"] == "reused"
    with store._connect() as connection:
        shards = connection.execute(
            "SELECT COUNT(*) FROM etf_proxy_generation_shards "
            "WHERE generation_id = ? AND symbol = ?",
            (generation["generation_id"], symbol),
        ).fetchone()[0]
        row_count = connection.execute(
            "SELECT COUNT(*) FROM etf_proxy_generation_rows "
            "WHERE generation_id = ? AND ts_code = ?",
            (generation["generation_id"], symbol),
        ).fetchone()[0]
        events = connection.execute(
            "SELECT COUNT(*) FROM fetch_promotion_events WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()[0]
    assert shards == 1
    assert row_count == 1
    assert events == 1


def test_different_attempt_for_same_symbol_is_a_conflict(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    att_a = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    att_b = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(20), rows=[_row(symbol, START)]
    )
    store.stage_etf_proxy_generation_attempt(generation["generation_id"], symbol, att_a)

    with pytest.raises(PITReceiptError, match="conflict"):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, att_b
        )


@pytest.mark.parametrize(
    "mutator",
    [
        # Each tamper breaks ONE reused invariant; the reuse path must fail closed.
        lambda shard: {**shard, "normalized_sha256": "0" * 64},
        lambda shard: {**shard, "raw_sha256": "0" * 64},
        lambda shard: {**shard, "row_count": shard["row_count"] + 1},
        lambda shard: {**shard, "raw_bytes": shard["raw_bytes"] + 1},
    ],
)
def test_reuse_rejects_shard_tamper(tmp_path, mutator):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    store.stage_etf_proxy_generation_attempt(generation["generation_id"], symbol, attempt_id)
    with store._connect() as connection:
        shard = dict(
            connection.execute(
                "SELECT * FROM etf_proxy_generation_shards "
                "WHERE generation_id = ? AND symbol = ?",
                (generation["generation_id"], symbol),
            ).fetchone()
        )
        tampered = mutator(shard)
        connection.execute(
            "UPDATE etf_proxy_generation_shards "
            "SET normalized_sha256=?, raw_sha256=?, row_count=?, raw_bytes=? "
            "WHERE generation_id = ? AND symbol = ?",
            (
                tampered["normalized_sha256"],
                tampered["raw_sha256"],
                tampered["row_count"],
                tampered["raw_bytes"],
                generation["generation_id"],
                symbol,
            ),
        )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_reuse_rejects_persisted_rows_tamper(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store,
        symbol=symbol,
        retrieved_at=_later(10),
        rows=[_row(symbol, START), _row(symbol, "2024-01-03", close=11.0)],
    )
    store.stage_etf_proxy_generation_attempt(generation["generation_id"], symbol, attempt_id)
    with store._connect() as connection:
        # Flip one OHLC value so the recomputed normalized hash no longer matches
        # the persisted shard hash.
        connection.execute(
            "UPDATE etf_proxy_generation_rows SET close = close + 100.0 "
            "WHERE generation_id = ? AND ts_code = ? AND trade_date = ?",
            (generation["generation_id"], symbol, START),
        )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_reuse_rejects_event_tamper(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    store.stage_etf_proxy_generation_attempt(generation["generation_id"], symbol, attempt_id)
    with store._connect() as connection:
        connection.execute(
            "UPDATE fetch_promotion_events SET details_json = ? WHERE attempt_id = ?",
            (
                json.dumps(
                    {
                        "generation_id": generation["generation_id"],
                        "symbol": symbol,
                        "raw_sha256": "0" * 64,
                        "normalized_sha256": "0" * 64,
                    }
                ),
                attempt_id,
            ),
        )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_reuse_rejects_raw_file_tamper(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    store.stage_etf_proxy_generation_attempt(generation["generation_id"], symbol, attempt_id)
    attempt = store._fetch_attempt(attempt_id)
    path = store._safe_raw_path(attempt["raw_path"])
    path.write_bytes(path.read_bytes() + b"\x00")  # break the CAS hash
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


# ---------------------------------------------------------------------------
# One attempt may not span symbol / generation
# ---------------------------------------------------------------------------


def test_attempt_may_not_span_symbol_or_generation(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    sym0 = ETF_PROXY_REQUIRED_SYMBOLS[0]
    sym1 = ETF_PROXY_REQUIRED_SYMBOLS[1]
    attempt_id = _record_attempt(
        store, symbol=sym0, retrieved_at=_later(10), rows=[_row(sym0, START)]
    )
    store.stage_etf_proxy_generation_attempt(generation["generation_id"], sym0, attempt_id)

    # The same attempt cannot be re-staged under the other symbol.
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], sym1, attempt_id
        )

    # Nor under a different generation (same symbol) -- the attempt's terminal
    # event already binds it.
    other = store.begin_or_resume_etf_proxy_generation(
        BASE_NOW, "2024-04-01", "2024-06-30"
    )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            other["generation_id"], sym0, attempt_id
        )


# ---------------------------------------------------------------------------
# Wrong inputs: symbol / range / generation / inactive
# ---------------------------------------------------------------------------


def test_wrong_symbol_argument_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], "510050.SH", "attempt-anything"
        )


def test_unknown_generation_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with pytest.raises(PITReceiptError, match="does not exist"):
        store.stage_etf_proxy_generation_attempt(
            "etf-proxy-99999999999999999999",
            ETF_PROXY_REQUIRED_SYMBOLS[0],
            "attempt-x",
        )


def test_inactive_generation_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE etf_proxy_generations SET status='abandoned', "
            "terminal_at=?, terminal_reason='force_new' WHERE generation_id=?",
            (_later(11).isoformat(), generation["generation_id"]),
        )
    with pytest.raises(PITReceiptError, match="inactive"):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_attempt_symbol_mismatch_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    sym0 = ETF_PROXY_REQUIRED_SYMBOLS[0]
    sym1 = ETF_PROXY_REQUIRED_SYMBOLS[1]
    # Attempt captured for sym1 but presented under sym0's slot.
    attempt_id = _record_attempt(
        store, symbol=sym1, retrieved_at=_later(10), rows=[_row(sym1, START)]
    )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], sym0, attempt_id
        )


def test_attempt_date_range_mismatch_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store,
        symbol=symbol,
        start="2024-02-01",
        end="2024-02-29",
        retrieved_at=_later(10),
        rows=[_row(symbol, "2024-02-01")],
    )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


# ---------------------------------------------------------------------------
# Staging window: [-1 rejected, 3600 accepted, 3601 rejected]
# ---------------------------------------------------------------------------


def test_window_minus_one_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(-1), rows=[_row(symbol, START)]
    )
    with pytest.raises(PITReceiptError, match="window"):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_window_exactly_3600_accepted(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(3600), rows=[_row(symbol, START)]
    )
    result = store.stage_etf_proxy_generation_attempt(
        generation["generation_id"], symbol, attempt_id
    )
    assert result["status"] == "staged"


def test_window_3601_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(3601), rows=[_row(symbol, START)]
    )
    with pytest.raises(PITReceiptError, match="window"):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


# ---------------------------------------------------------------------------
# Raw / semantics / HTTP / body / event lineage failures on first stage
# ---------------------------------------------------------------------------


def test_raw_file_tamper_rejected_on_first_stage(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    attempt = store._fetch_attempt(attempt_id)
    path = store._safe_raw_path(attempt["raw_path"])
    path.write_bytes(path.read_bytes() + b"\x00")
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


@pytest.mark.parametrize(
    "mutator",
    [
        # Break one frozen semantics field, keeping the stored sha self-consistent
        # so the failure is the specific lineage check, not the sha self-check.
        lambda s: {**s, "wire_params": {"ts_code": "159915.SZ", "start_date": "20240102", "end_date": "20240331"}},
        lambda s: {**s, "fields": list(s["fields"]) + ["extra"]},
        lambda s: {**s, "row_cap": s["row_cap"] - 1},
        lambda s: {**s, "api_name": "daily"},
        lambda s: {**s, "partition_key": "159915.SZ:2024-01-02:2024-03-31"},
        lambda s: {**s, "method": "GET"},
        lambda s: {**s, "schema_version": "tushare-wire-request/v2"},
    ],
)
def test_semantics_lineage_mismatch_rejected(tmp_path, mutator):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    with store._connect() as connection:
        row = dict(
            connection.execute(
                "SELECT request_semantics_json FROM fetch_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        )
        semantics = json.loads(row["request_semantics_json"])
        semantics = mutator(semantics)
        digest = research_pit_store._sha256(semantics)
        connection.execute(
            "UPDATE fetch_attempts SET request_semantics_json = ?, "
            "request_semantics_sha256 = ? WHERE attempt_id = ?",
            (json.dumps(semantics), digest, attempt_id),
        )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_semantics_sha_self_check_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    # Corrupt only the stored sha: the json is unchanged so the per-field checks
    # would pass, but the self-consistency hash check must catch it first.
    with store._connect() as connection:
        connection.execute(
            "UPDATE fetch_attempts SET request_semantics_sha256 = ? "
            "WHERE attempt_id = ?",
            ("0" * 64, attempt_id),
        )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_attempt_row_corruptions_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]

    corruptions = {
        "http_status": "UPDATE fetch_attempts SET http_status = 404 WHERE attempt_id = ?",
        "body_complete": "UPDATE fetch_attempts SET body_complete = 0 WHERE attempt_id = ?",
        "error_kind": "UPDATE fetch_attempts SET error_kind = 'api_error' WHERE attempt_id = ?",
        "row_cap": "UPDATE fetch_attempts SET row_cap = 4999 WHERE attempt_id = ?",
        "endpoint": "UPDATE fetch_attempts SET endpoint = 'daily' WHERE attempt_id = ?",
        "dataset": "UPDATE fetch_attempts SET dataset = 'daily' WHERE attempt_id = ?",
        "partition_key": "UPDATE fetch_attempts SET partition_key = '159915.SZ:2024-01-02:2024-03-31' WHERE attempt_id = ?",
    }
    for label, sql in corruptions.items():
        attempt_id = _record_attempt(
            store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
        )
        with store._connect() as connection:
            connection.execute(sql, (attempt_id,))
        with pytest.raises(PITReceiptError):
            store.stage_etf_proxy_generation_attempt(
                generation["generation_id"], symbol, attempt_id
            )


def test_prior_terminal_event_rejected(tmp_path):
    """An attempt already marked generation_required may never be staged."""

    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    store.promote_fetch_attempt(attempt_id)  # marks fund_daily as generation_required
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


# ---------------------------------------------------------------------------
# Body shape failures: empty / duplicate / bad OHLC / cap reached
# ---------------------------------------------------------------------------


def test_empty_body_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[]
    )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_duplicate_session_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store,
        symbol=symbol,
        retrieved_at=_later(10),
        rows=[_row(symbol, START), _row(symbol, START, close=11.0)],
    )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_bad_ohlc_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    bad = _row(symbol, START)
    bad["high"] = bad["open"] - 1.0  # high below open/close
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[bad]
    )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_cap_reached_rejected(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    # A wide enough window to hold FUND_DAILY_ROW_CAP unique calendar dates.
    wide_start = "2006-01-02"
    wide_end = "2024-12-31"
    generation = store.begin_or_resume_etf_proxy_generation(BASE_NOW, wide_start, wide_end)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    base = datetime(2006, 1, 2)
    rows = [
        _row(symbol, (base + timedelta(days=offset)).date().isoformat(), close=10.0)
        for offset in range(FUND_DAILY_ROW_CAP)
    ]
    attempt_id = _record_attempt(
        store,
        symbol=symbol,
        start=wide_start,
        end=wide_end,
        retrieved_at=_later(10),
        rows=rows,
    )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


# ---------------------------------------------------------------------------
# Referee P1: reuse must re-run the FULL canonical validator, not a weak shadow
# ---------------------------------------------------------------------------


def _stage_one(
    store: PITReceiptStore, generation: dict, symbol: str, rows: list[dict] | None = None
) -> str:
    """Record + stage one canonical single-row attempt; return its attempt_id."""

    attempt_id = _record_attempt(
        store,
        symbol=symbol,
        retrieved_at=_later(10),
        rows=rows or [_row(symbol, START)],
    )
    store.stage_etf_proxy_generation_attempt(generation["generation_id"], symbol, attempt_id)
    return attempt_id


def test_reuse_rejects_self_consistent_semantics_mutation(tmp_path):
    """Gap 1: a self-consistent-but-wrong semantics (method=GET) with BOTH the
    attempt sha AND the shard sha re-keyed must still be rejected on reuse,
    because reuse re-runs the full per-field canonical semantics validator -- a
    mutually-self-consistent hash pair must not be trusted on its own."""

    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _stage_one(store, generation, symbol)
    with store._connect() as connection:
        row = dict(
            connection.execute(
                "SELECT request_semantics_json FROM fetch_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        )
        semantics = json.loads(row["request_semantics_json"])
        semantics["method"] = "GET"
        digest = research_pit_store._sha256(semantics)
        connection.execute(
            "UPDATE fetch_attempts SET request_semantics_json = ?, "
            "request_semantics_sha256 = ? WHERE attempt_id = ?",
            (json.dumps(semantics), digest, attempt_id),
        )
        # Re-key the shard too, so attempt/shard stay mutually self-consistent.
        # The only thing that can catch this is the canonical per-field check.
        connection.execute(
            "UPDATE etf_proxy_generation_shards SET request_semantics_sha256 = ? "
            "WHERE generation_id = ? AND symbol = ?",
            (digest, generation["generation_id"], symbol),
        )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE fetch_attempts SET http_status = 500 WHERE attempt_id = ?",
        "UPDATE fetch_attempts SET body_complete = 0 WHERE attempt_id = ?",
        "UPDATE fetch_attempts SET error_kind = 'api_error' WHERE attempt_id = ?",
    ],
)
def test_reuse_rejects_attempt_promotability_tamper(tmp_path, sql):
    """Gap 2: reuse must re-check http_status / body_complete / error_kind
    rather than trust the staged attempt flags."""

    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _stage_one(store, generation, symbol)
    with store._connect() as connection:
        connection.execute(sql, (attempt_id,))
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_reuse_rejects_params_range_mutation(tmp_path):
    """Gap 2: a params_json range mutation (re-keyed so stored hashes stay
    self-consistent) must be rejected because reuse re-binds params to the
    generation's frozen date range and the shard symbol."""

    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _stage_one(store, generation, symbol)
    with store._connect() as connection:
        row = dict(
            connection.execute(
                "SELECT params_json FROM fetch_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        )
        params = json.loads(row["params_json"])
        # Shrink only the end_date to a window that STILL contains the staged
        # row's trade_date (2024-01-02), so normalization still succeeds and the
        # persisted shard hash stays self-consistent. The only remaining check
        # is the validator's binding of params to the generation's frozen range.
        params["start_date"] = "2024-01-02"
        params["end_date"] = "2024-02-15"
        new_partition = research_pit_store._canonical_partition_key("fund_daily", params)
        connection.execute(
            "UPDATE fetch_attempts SET params_json = ?, partition_key = ? "
            "WHERE attempt_id = ?",
            (research_pit_store._canonical_json(params), new_partition, attempt_id),
        )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_reuse_rejects_shard_retrieved_at_tamper(tmp_path):
    """Gap 3: every shard lineage field must equal the attempt, retrieved_at
    included -- not just raw/hash/count."""

    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _stage_one(store, generation, symbol)
    with store._connect() as connection:
        connection.execute(
            "UPDATE etf_proxy_generation_shards SET retrieved_at = ? "
            "WHERE generation_id = ? AND symbol = ?",
            (_later(9999).isoformat(), generation["generation_id"], symbol),
        )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


@pytest.mark.parametrize(
    "offset, should_reject",
    # WHY drift BOTH columns identically: a torn write that moves
    # fetch_attempts.retrieved_at and the shard's retrieved_at to the same
    # out-of-window instant stays mutually self-consistent, so the shard/attempt
    # lineage-equality check on reuse passes -- only a window re-check on the
    # reuse path can catch it. Exactly +3600 stays reused.
    [(3601, True), (-1, True), (3600, False)],
)
def test_reuse_enforces_staging_window_on_both_retrieved_at(
    tmp_path, offset, should_reject
):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _stage_one(store, generation, symbol)
    drifted = _later(offset).isoformat()
    with store._connect() as connection:
        connection.execute(
            "UPDATE fetch_attempts SET retrieved_at = ? WHERE attempt_id = ?",
            (drifted, attempt_id),
        )
        connection.execute(
            "UPDATE etf_proxy_generation_shards SET retrieved_at = ? "
            "WHERE generation_id = ? AND symbol = ?",
            (drifted, generation["generation_id"], symbol),
        )
    if should_reject:
        with pytest.raises(PITReceiptError, match="window"):
            store.stage_etf_proxy_generation_attempt(
                generation["generation_id"], symbol, attempt_id
            )
    else:
        result = store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )
        assert result["status"] == "reused"


def test_reuse_rejects_event_details_extra_key(tmp_path):
    """Gap 4: the staged event details must equal the EXACT canonical dict --
    an added key is a tamper, not a no-op (no partial .get() matching)."""

    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _stage_one(store, generation, symbol)
    with store._connect() as connection:
        row = dict(
            connection.execute(
                "SELECT details_json FROM fetch_promotion_events WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        )
        details = json.loads(row["details_json"])
        details["extra"] = "anything"
        connection.execute(
            "UPDATE fetch_promotion_events SET details_json = ? WHERE attempt_id = ?",
            (json.dumps(details), attempt_id),
        )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


@pytest.mark.parametrize("field", ["contract_sha256", "scope_key"])
def test_first_stage_rejects_generation_lineage_tamper(tmp_path, field):
    """Gap 5: before the first stage writes, the generation's frozen scope_key
    and contract_sha256 must be recomputed from start/end and constant-time
    compared; a mutated value is rejected."""

    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    with store._connect() as connection:
        connection.execute(
            f"UPDATE etf_proxy_generations SET {field} = ? WHERE generation_id = ?",
            ("0" * 64, generation["generation_id"]),
        )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


@pytest.mark.parametrize(
    "field",
    ["params_json", "request_semantics_json"],
)
def test_malformed_attempt_json_fails_closed(tmp_path, field):
    """Gap 7: malformed params_json / request_semantics_json must surface as a
    PITReceiptError, never leak JSONDecodeError/TypeError."""

    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    with store._connect() as connection:
        connection.execute(
            f"UPDATE fetch_attempts SET {field} = ? WHERE attempt_id = ?",
            ("{not-json", attempt_id),
        )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def _mutate_semantics_to_list(connection, attempt_id):
    payload = []
    connection.execute(
        "UPDATE fetch_attempts SET request_semantics_json = ?, "
        "request_semantics_sha256 = ? WHERE attempt_id = ?",
        (json.dumps(payload), research_pit_store._sha256(payload), attempt_id),
    )


def _mutate_params_to_list(connection, attempt_id):
    connection.execute(
        "UPDATE fetch_attempts SET params_json = ? WHERE attempt_id = ?",
        (json.dumps([]), attempt_id),
    )


def _mutate_semantics_row_cap_nonint(connection, attempt_id):
    row = dict(
        connection.execute(
            "SELECT request_semantics_json FROM fetch_attempts WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()
    )
    semantics = json.loads(row["request_semantics_json"])
    semantics["row_cap"] = "not-int"
    connection.execute(
        "UPDATE fetch_attempts SET request_semantics_json = ?, "
        "request_semantics_sha256 = ? WHERE attempt_id = ?",
        (json.dumps(semantics), research_pit_store._sha256(semantics), attempt_id),
    )


def _mutate_attempt_row_cap_nonint(connection, attempt_id):
    connection.execute(
        "UPDATE fetch_attempts SET row_cap = ? WHERE attempt_id = ?",
        ("not-int", attempt_id),
    )


def _mutate_attempt_body_complete_nonint(connection, attempt_id):
    connection.execute(
        "UPDATE fetch_attempts SET body_complete = ? WHERE attempt_id = ?",
        ("not-int", attempt_id),
    )


@pytest.mark.parametrize(
    "mutator",
    [
        _mutate_semantics_to_list,
        _mutate_params_to_list,
        _mutate_semantics_row_cap_nonint,
        _mutate_attempt_row_cap_nonint,
        _mutate_attempt_body_complete_nonint,
    ],
)
def test_malformed_types_fail_closed(tmp_path, mutator):
    """Gap B: type-corrupted attempt columns must surface as a PITReceiptError,
    never leak AttributeError/TypeError/ValueError. Semantics mutations re-key
    the stored sha so the failure is the type path, not a sha self-check."""

    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    with store._connect() as connection:
        mutator(connection, attempt_id)
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE fetch_attempts SET body_complete = 1.5 WHERE attempt_id = ?",
        "UPDATE fetch_attempts SET http_status = 200.9 WHERE attempt_id = ?",
        "UPDATE fetch_attempts SET row_cap = 5000.9 WHERE attempt_id = ?",
    ],
)
def test_fractional_integer_columns_fail_closed(tmp_path, sql):
    """Referee P2: a torn write that drifts an INTEGER column to a fractional
    REAL must surface as a PITReceiptError. Bare ``int()`` would silently
    truncate (1.5 -> 1, 200.9 -> 200, 5000.9 -> 5000) and let the corrupted
    row pass every equality / range check, so ``_require_int`` must validate
    losslessly -- sqlite INTEGER affinity stores 1.5 as REAL, it does not
    round it away."""

    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    with store._connect() as connection:
        connection.execute(sql, (attempt_id,))
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_fractional_attempt_raw_bytes_fails_closed(tmp_path):
    """Referee P2: a fractional ``raw_bytes`` must be rejected BEFORE the raw
    file is size-compared. ``_verify_raw_file`` compares ``int(raw_bytes)``
    against the real file size, so ``N + 0.9`` would truncate to ``N`` and
    match the file exactly -- the byte-count receipt would pass for a
    corrupted column. The ETF validator must strictly validate ``raw_bytes``
    first, then let the size check reuse that lossless value."""

    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE fetch_attempts SET raw_bytes = raw_bytes + 0.9 "
            "WHERE attempt_id = ?",
            (attempt_id,),
        )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


def test_reuse_rejects_shard_raw_bytes_fraction(tmp_path):
    """Referee P2: on reuse the shard lineage ``raw_bytes`` is bound to the
    attempt's. A torn write that drifts ONLY the shard column to a fractional
    REAL must fail closed; bare ``int()`` on both sides would truncate to the
    same integer and the corrupted shard would be silently reused."""

    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _stage_one(store, generation, symbol)
    with store._connect() as connection:
        connection.execute(
            "UPDATE etf_proxy_generation_shards SET raw_bytes = raw_bytes + 0.9 "
            "WHERE generation_id = ? AND symbol = ?",
            (generation["generation_id"], symbol),
        )
    with pytest.raises(PITReceiptError):
        store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrent_same_attempt_is_idempotent(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_id = _record_attempt(
        store, symbol=symbol, retrieved_at=_later(10), rows=[_row(symbol, START)]
    )

    def call():
        return store.stage_etf_proxy_generation_attempt(
            generation["generation_id"], symbol, attempt_id
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(call) for _ in range(16)]
        results = [future.result() for future in futures]

    statuses = {result["status"] for result in results}
    assert statuses <= {"staged", "reused"}
    assert "staged" in statuses  # at least one real write happened
    with store._connect() as connection:
        shards = connection.execute(
            "SELECT COUNT(*) FROM etf_proxy_generation_shards "
            "WHERE generation_id = ? AND symbol = ?",
            (generation["generation_id"], symbol),
        ).fetchone()[0]
        events = connection.execute(
            "SELECT COUNT(*) FROM fetch_promotion_events WHERE attempt_id = ?",
            (attempt_id,),
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT COUNT(*) FROM etf_proxy_generation_rows "
            "WHERE generation_id = ? AND ts_code = ?",
            (generation["generation_id"], symbol),
        ).fetchone()[0]
    assert shards == 1
    assert events == 1
    assert rows == 1


def test_concurrent_different_attempts_only_one_wins(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    generation = _begin(store)
    symbol = ETF_PROXY_REQUIRED_SYMBOLS[0]
    attempt_ids = [
        _record_attempt(
            store, symbol=symbol, retrieved_at=_later(10 + offset), rows=[_row(symbol, START)]
        )
        for offset in range(2)
    ]

    errors: list[BaseException] = []

    def call(attempt_id: str):
        try:
            return store.stage_etf_proxy_generation_attempt(
                generation["generation_id"], symbol, attempt_id
            )
        except PITReceiptError as exc:
            errors.append(exc)
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(call, attempt_id) for attempt_id in attempt_ids]
        results = [future.result() for future in futures]

    staged = [result for result in results if result is not None]
    assert len(staged) == 1  # exactly one attempt won the symbol slot
    assert staged[0]["status"] == "staged"
    # The loser must have raised an explicit conflict, not silently no-op'd.
    assert len(errors) == 1
    assert "conflict" in str(errors[0]) or "staged" in str(errors[0])
    with store._connect() as connection:
        shards = connection.execute(
            "SELECT COUNT(*) FROM etf_proxy_generation_shards "
            "WHERE generation_id = ? AND symbol = ?",
            (generation["generation_id"], symbol),
        ).fetchone()[0]
        winner = connection.execute(
            "SELECT attempt_id FROM etf_proxy_generation_shards "
            "WHERE generation_id = ? AND symbol = ?",
            (generation["generation_id"], symbol),
        ).fetchone()[0]
    assert shards == 1
    assert winner in attempt_ids
