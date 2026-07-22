"""Cron-driven jiaoch market data fetch with partial-safe resume.

Strategy:
1. Clean any partial generations (shard_count != 4) from previously killed runs.
2. Run the CLI fetch with batch_size=1 (atomic per-session commit) + resume.
3. The CLI runs until killed by the 10-min timeout; that's OK because
   batch_size=1 means only the in-flight session might be partial.
4. Check progress; if complete, signal stage 1.4-1.6.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from app.research_pit_store import MARKET_SESSION_GENERATION_WINDOW_SECONDS

WORK_DIR = Path(__file__).resolve().parent.parent
DB_PATH = WORK_DIR / "data/research_pit_store/current_pool_market/metadata.sqlite3"
PROGRESS_PATH = WORK_DIR / "data/research_pit_store/.market_progress.json"
TOKEN = os.environ.get("JIAOCH_TOKEN", "")


def clean_partial_generations() -> int:
    """Abandon stale collecting generations while retaining their evidence."""
    if not DB_PATH.exists():
        return 0
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")
        now = datetime.now(timezone.utc)
        rows = conn.execute(
            """
            SELECT g.generation_id, g.trade_date, g.status, g.started_at, COUNT(s.dataset) as c
            FROM market_session_generations g
            LEFT JOIN market_session_generation_shards s ON s.generation_id = g.generation_id
            GROUP BY g.generation_id HAVING c != 4
            """
        ).fetchall()
        unexpected = [
            (trade_date, status, count)
            for _gen_id, trade_date, status, _started_at, count in rows
            if status != "collecting"
        ]
        if unexpected:
            raise RuntimeError(
                f"refusing to abandon non-collecting partial generations: {unexpected!r}"
            )
        stale_rows = []
        fresh_rows = []
        for gen_id, trade_date, _status, started_at, count in rows:
            started = datetime.fromisoformat(started_at).astimezone(timezone.utc)
            if started > now:
                raise RuntimeError("collecting generation start time is in the future")
            record = (gen_id, trade_date, count)
            if (now - started).total_seconds() > MARKET_SESSION_GENERATION_WINDOW_SECONDS:
                stale_rows.append(record)
            else:
                fresh_rows.append(record)
        for _gen_id, trade_date, count in fresh_rows:
            print(f"preserving fresh partial for resume: {trade_date} ({count}/4)", flush=True)
        now_iso = now.isoformat()
        for gen_id, trade_date, count in stale_rows:
            print(f"abandoning stale partial: {trade_date} ({count}/4)", flush=True)
            updated = conn.execute(
                """
                UPDATE market_session_generations
                SET status = 'abandoned', terminal_at = ?, terminal_reason = 'stale_incomplete'
                WHERE generation_id = ? AND status = 'collecting'
                """,
                (now_iso, gen_id),
            ).rowcount
            if updated != 1:
                raise RuntimeError("collecting generation changed during cleanup")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("partial cleanup found broken receipt foreign keys")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    return len(stale_rows)


def check_progress() -> dict:
    if not PROGRESS_PATH.exists():
        return {"status": "unknown", "completed": 0, "remaining": 483}
    try:
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "unknown", "completed": 0, "remaining": 483}


def run_fetch() -> int:
    """Run the CLI fetch command. Returns exit code."""
    env = dict(os.environ)
    env["JIAOCH_TOKEN"] = TOKEN
    env["PYTHONUNBUFFERED"] = "1"
    cmd = [
        sys.executable,
        "-u",
        "-m",
        "app.jobs",
        "research-current-pool-fetch-market-jiaoch",
        "--store-dir",
        "data/research_pit_store/current_pool_market",
        "--start-date",
        "2024-07-05",
        "--end-date",
        "2026-07-03",
        "--workers",
        "1",
        "--batch-size",
        "1",
        "--timeout",
        "30",
        "--temporal-contract-path",
        "data/research_partitions/frozen-v1.json",
        "--temporal-role",
        "contaminated_diagnostic",
        "--progress-path",
        str(PROGRESS_PATH),
    ]
    result = subprocess.run(
        cmd, cwd=str(WORK_DIR), env=env, timeout=540, capture_output=True, text=True
    )
    if result.stdout:
        print(result.stdout[-500:], flush=True)
    if result.stderr:
        print("STDERR:", result.stderr[-500:], flush=True)
    return result.returncode


def main() -> int:
    if not TOKEN:
        print("JIAOCH_TOKEN not set", flush=True)
        return 1

    # Step 1: abandon stale partials
    abandoned = clean_partial_generations()
    if abandoned:
        print(f"abandoned {abandoned} stale partial generations", flush=True)

    # Step 2: check if already complete
    progress = check_progress()
    if progress.get("status") == "complete":
        print("ALREADY COMPLETE - proceed to stage 1.4-1.6", flush=True)
        return 0

    # Step 3: run fetch (will be killed by 9-min timeout, that's fine)
    print(f"current: {progress.get('completed', 0)}/{483}, running fetch...", flush=True)
    try:
        code = run_fetch()
        print(f"fetch exited with code {code}", flush=True)
        if code:
            return code
    except subprocess.TimeoutExpired:
        print("fetch timed out (expected for long runs)", flush=True)

    # Step 4: report final progress
    progress = check_progress()
    completed = progress.get("completed", 0)
    remaining = progress.get("remaining", 483)
    last = progress.get("last_session")
    status = progress.get("status")
    print(f"progress: status={status} completed={completed} remaining={remaining} last={last}", flush=True)

    if status == "complete" or remaining == 0:
        print("ALL MARKET DATA COMPLETE", flush=True)
        return 0

    return 0  # always 0; next cron will continue


if __name__ == "__main__":
    raise SystemExit(main())
