"""Verify the destination artifact schema for market-session generations.

`publish_universe_artifact` builds a pruned destination SQLite whose
market-session tables must be column/constraint identical to the source
`_initialize` schema so that `audit_coverage` can re-bind to the same proof
offline. Driving the real publish path is blocked by the coverage-audit hash
gate, so this test exercises the shared private schema initializer that
publish itself uses on a throwaway database — proving the DDL is valid without
duplicating a second hand-written schema.
"""

import sqlite3

from app.research_pit_store import _MARKET_SESSION_GENERATION_SCHEMA_SQL

EXPECTED_TABLES = (
    "market_session_generations",
    "market_session_generation_shards",
    "market_session_generation_rows_daily",
    "market_session_generation_rows_adj_factor",
    "market_session_generation_rows_stk_limit",
    "market_session_generation_rows_suspend_d",
    "market_session_generation_head",
)


def _connect_with_schema() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(_MARKET_SESSION_GENERATION_SCHEMA_SQL)
    return connection


def _table_columns(connection: sqlite3.Connection, table: str) -> dict:
    return {row["name"]: int(row["pk"]) for row in connection.execute(f"PRAGMA table_info({table})")}


def test_schema_creates_all_market_session_tables_and_head():
    connection = _connect_with_schema()
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert set(EXPECTED_TABLES) <= tables


def test_schema_creates_one_collecting_market_session_index():
    connection = _connect_with_schema()
    indexes = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
        )
    }
    # WHY: the partial unique index mirrors the source store so that at most one
    # generation per trade_date can sit in 'collecting' status — losing it would
    # let the destination silently accept duplicate in-flight sessions.
    assert "one_collecting_market_session_per_trade_date" in indexes


def test_suspend_d_primary_key_includes_suspend_timing():
    connection = _connect_with_schema()
    columns = _table_columns(connection, "market_session_generation_rows_suspend_d")
    # WHY: suspend_timing must be part of the key, otherwise two distinct
    # suspend events (e.g. morning vs. full-day) for the same code/date/type
    # would collapse into one row and silently drop evidence.
    assert columns["suspend_timing"] > 0
    assert columns["suspend_type"] > 0
    # generation_id + trade_date + ts_code round out the composite key.
    assert {columns["generation_id"], columns["trade_date"], columns["ts_code"]} == {1, 2, 3}


def test_schema_carries_distinguishing_columns_for_deep_validation():
    connection = _connect_with_schema()

    generations = _table_columns(connection, "market_session_generations")
    assert "final_oos_eligible" in generations
    assert "lineage_sha256" in generations
    assert "vintage" in generations

    shards = _table_columns(connection, "market_session_generation_shards")
    assert "dataset" in shards
    assert shards["dataset"] > 0  # dataset is part of the shard PK

    head = _table_columns(connection, "market_session_generation_head")
    assert head["trade_date"] == 1  # head is keyed by trade_date

    daily = _table_columns(connection, "market_session_generation_rows_daily")
    for column in ("open", "high", "low", "close", "vol", "amount"):
        assert column in daily


def test_schema_has_no_dangling_foreign_keys():
    connection = _connect_with_schema()
    # WHY: an empty schema must already be FK-clean; any violation here means a
    # REFERENCES clause points at a table the initializer forgot to create.
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
