"""Database contract tests for immutable ETF proxy generations."""

from __future__ import annotations

import inspect
import sqlite3

import pytest

from app import research_pit_store


PITReceiptStore = research_pit_store.PITReceiptStore


EXPECTED_TABLES = {
    "etf_proxy_generations",
    "etf_proxy_generation_shards",
    "etf_proxy_generation_rows",
    "etf_proxy_generation_head",
}


def _generation_values(
    generation_id: str,
    scope_key: str,
    *,
    vintage: str = "historical_backfill",
    final_oos_eligible: int = 0,
) -> tuple[object, ...]:
    sequence = int(generation_id.rsplit("-", 1)[-1])
    return (
        sequence,
        generation_id,
        scope_key,
        "2024-01-02",
        "2024-01-03",
        "a" * 64,
        "collecting",
        vintage,
        final_oos_eligible,
        "2024-01-03T16:00:00+08:00",
    )


def _insert_generation(
    connection: sqlite3.Connection,
    generation_id: str,
    scope_key: str,
    *,
    vintage: str = "historical_backfill",
    final_oos_eligible: int = 0,
) -> None:
    connection.execute(
        """
        INSERT INTO etf_proxy_generations (
            generation_sequence, generation_id, scope_key, start_date, end_date,
            contract_sha256, status, vintage, final_oos_eligible, started_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _generation_values(
            generation_id,
            scope_key,
            vintage=vintage,
            final_oos_eligible=final_oos_eligible,
        ),
    )


def test_etf_proxy_generation_constants_are_frozen():
    assert (
        getattr(research_pit_store, "ETF_PROXY_GENERATION_SCHEMA_VERSION", None)
        == "etf-proxy-generations/v1"
    )
    assert getattr(research_pit_store, "ETF_PROXY_GENERATION_WINDOW_SECONDS", None) == 3600
    assert getattr(research_pit_store, "ETF_PROXY_GENERATION_VINTAGES", None) == (
        "historical_backfill",
        "live_forward",
    )


def test_store_initialization_creates_etf_proxy_generation_schema(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with store._connect() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert EXPECTED_TABLES <= tables

        generation_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(etf_proxy_generations)")
        }
        assert generation_columns == {
            "generation_sequence",
            "generation_id",
            "scope_key",
            "start_date",
            "end_date",
            "contract_sha256",
            "status",
            "vintage",
            "final_oos_eligible",
            "started_at",
            "terminal_at",
            "terminal_reason",
            "manifest_sha256",
            "lineage_sha256",
        }

        shard_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(etf_proxy_generation_shards)")
        }
        assert shard_columns == {
            "generation_id",
            "symbol",
            "attempt_id",
            "request_semantics_sha256",
            "retrieved_at",
            "raw_sha256",
            "raw_bytes",
            "normalized_sha256",
            "row_count",
        }

        row_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(etf_proxy_generation_rows)")
        }
        assert row_columns == {
            "generation_id",
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
        }

        head_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(etf_proxy_generation_head)")
        }
        assert head_columns == {
            "scope_key",
            "generation_id",
            "manifest_sha256",
            "lineage_sha256",
            "published_at",
        }


def test_rows_have_composite_foreign_key_to_exact_symbol_shard(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with store._connect() as connection:
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(etf_proxy_generation_rows)"
        ).fetchall()
        shard_fk = [
            row
            for row in foreign_keys
            if row["table"] == "etf_proxy_generation_shards"
        ]
        assert {(row["from"], row["to"]) for row in shard_fk} == {
            ("generation_id", "generation_id"),
            ("ts_code", "symbol"),
        }
        assert len({row["id"] for row in shard_fk}) == 1


def test_database_rejects_nonfrozen_generation_and_symbol_values(tmp_path):
    store = PITReceiptStore(str(tmp_path / "store"))
    with store._connect() as connection:
        with pytest.raises(sqlite3.IntegrityError):
            _insert_generation(
                connection,
                "etf-proxy-1",
                "scope-invalid-oos",
                final_oos_eligible=1,
            )
        with pytest.raises(sqlite3.IntegrityError):
            _insert_generation(
                connection,
                "etf-proxy-2",
                "scope-invalid-vintage",
                vintage="backfilled_after_seeing_results",
            )

        _insert_generation(connection, "etf-proxy-3", "scope-symbol")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO etf_proxy_generation_shards (
                    generation_id, symbol, attempt_id,
                    request_semantics_sha256, retrieved_at, raw_sha256,
                    raw_bytes, normalized_sha256, row_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "etf-proxy-3",
                    "510050.SH",
                    "attempt-invalid-symbol",
                    "b" * 64,
                    "2024-01-03T16:00:00+08:00",
                    "c" * 64,
                    1,
                    "d" * 64,
                    1,
                ),
            )


def test_only_one_collecting_generation_per_scope_and_reopen(tmp_path):
    root = tmp_path / "store"
    store = PITReceiptStore(str(root))
    with store._connect() as connection:
        _insert_generation(connection, "etf-proxy-1", "same-scope")
        with pytest.raises(sqlite3.IntegrityError):
            _insert_generation(connection, "etf-proxy-2", "same-scope")
        _insert_generation(connection, "etf-proxy-3", "different-scope")

    reopened = PITReceiptStore(str(root))
    with reopened._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM etf_proxy_generations"
        ).fetchone()[0] == 2


def test_source_and_artifact_initializers_reference_one_shared_schema():
    initialize_source = inspect.getsource(PITReceiptStore._initialize)
    publish_source = inspect.getsource(PITReceiptStore.publish_universe_artifact)
    assert initialize_source.count("_ETF_PROXY_GENERATION_SCHEMA_SQL") == 1
    assert publish_source.count("_ETF_PROXY_GENERATION_SCHEMA_SQL") == 1
    schema = getattr(research_pit_store, "_ETF_PROXY_GENERATION_SCHEMA_SQL", "")
    assert "CREATE TABLE" in schema
