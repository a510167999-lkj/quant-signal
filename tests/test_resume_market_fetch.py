import importlib.util
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "resume_market_fetch.py"
MARKET_ROW_TABLES = (
    "market_session_generation_rows_adj_factor",
    "market_session_generation_rows_daily",
    "market_session_generation_rows_stk_limit",
    "market_session_generation_rows_suspend_d",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("resume_market_fetch", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _create_store(path: Path, *, status: str, started_at: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE market_session_generations "
        "(generation_id TEXT PRIMARY KEY, trade_date TEXT NOT NULL, status TEXT NOT NULL, "
        "started_at TEXT NOT NULL, terminal_at TEXT, terminal_reason TEXT)"
    )
    conn.execute(
        "CREATE TABLE market_session_generation_shards "
        "(generation_id TEXT NOT NULL REFERENCES market_session_generations(generation_id), "
        "dataset TEXT NOT NULL)"
    )
    for table in MARKET_ROW_TABLES:
        conn.execute(
            f"CREATE TABLE {table} "
            "(generation_id TEXT NOT NULL REFERENCES market_session_generations(generation_id), value TEXT)"
        )
    conn.execute(
        "INSERT INTO market_session_generations VALUES ('partial-1', '2026-06-04', ?, ?, NULL, NULL)",
        (status, started_at),
    )
    conn.executemany(
        "INSERT INTO market_session_generation_shards VALUES ('partial-1', ?)",
        [("daily",), ("adj_factor",)],
    )
    for table in MARKET_ROW_TABLES:
        conn.execute(f"INSERT INTO {table} VALUES ('partial-1', 'row')")
    conn.commit()
    conn.close()


def test_clean_partial_generations_abandons_stale_collecting_rows_and_keeps_evidence(tmp_path):
    module = _load_module()
    database = tmp_path / "metadata.sqlite3"
    started_at = (
        datetime.now(timezone.utc)
        - timedelta(seconds=module.MARKET_SESSION_GENERATION_WINDOW_SECONDS + 1)
    ).isoformat()
    _create_store(database, status="collecting", started_at=started_at)
    module.DB_PATH = database

    assert module.clean_partial_generations() == 1

    conn = sqlite3.connect(database)
    assert conn.execute(
        "SELECT status, terminal_reason FROM market_session_generations"
    ).fetchone() == ("abandoned", "stale_incomplete")
    assert conn.execute("SELECT COUNT(*) FROM market_session_generation_shards").fetchone()[0] == 2
    for table in MARKET_ROW_TABLES:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_clean_partial_generations_rejects_non_collecting_partial(tmp_path):
    module = _load_module()
    database = tmp_path / "metadata.sqlite3"
    _create_store(database, status="published", started_at=datetime.now(timezone.utc).isoformat())
    module.DB_PATH = database

    with pytest.raises(RuntimeError, match="non-collecting"):
        module.clean_partial_generations()

    conn = sqlite3.connect(database)
    assert conn.execute("SELECT COUNT(*) FROM market_session_generations").fetchone()[0] == 1
    conn.close()


def test_clean_partial_generations_preserves_fresh_collecting_partial_for_resume(tmp_path):
    module = _load_module()
    database = tmp_path / "metadata.sqlite3"
    _create_store(database, status="collecting", started_at=datetime.now(timezone.utc).isoformat())
    module.DB_PATH = database

    assert module.clean_partial_generations() == 0

    conn = sqlite3.connect(database)
    assert conn.execute("SELECT status FROM market_session_generations").fetchone()[0] == "collecting"
    assert conn.execute("SELECT COUNT(*) FROM market_session_generation_shards").fetchone()[0] == 2
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()
