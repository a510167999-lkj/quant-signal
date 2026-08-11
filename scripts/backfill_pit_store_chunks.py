#!/usr/bin/env python3
"""Backfill PIT store in weekly chunks (avoids Jiaoch trade_cal wide-range error).

Usage:
  python scripts/backfill_pit_store_chunks.py --start 2023-06-26 --end 2024-07-04
  python scripts/backfill_pit_store_chunks.py --start 2023-06-26 --end 2023-12-31 \
      --contract data/research_partitions/frozen-v1.json --role development
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def weekly_chunks(start: str, end: str) -> list[tuple[str, str]]:
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    chunks: list[tuple[str, str]] = []
    cur = s
    while cur <= e:
        nxt = min(cur + timedelta(days=6), e)
        chunks.append((cur.isoformat(), nxt.isoformat()))
        cur = nxt + timedelta(days=1)
    return chunks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill PIT store in weekly chunks")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument(
        "--contract",
        default="data/research_partitions/frozen-v1.json",
    )
    parser.add_argument("--role", default="development")
    parser.add_argument("--store-dir", default="data/research_pit_store/current_pool_market_v2")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args(argv)

    chunks = weekly_chunks(args.start, args.end)
    total = len(chunks)
    print(f"Backfilling {total} weekly chunks ({args.start} .. {args.end}) role={args.role}")

    import os
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")
    # load .env for JIAOCH_TOKEN
    env_file = ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

    failures: list[tuple[str, str, str]] = []
    for i, (cs, ce) in enumerate(chunks, 1):
        cmd = [
            sys.executable, "-m", "app.jobs", "research-pit-fetch-tushare",
            "--store-dir", args.store_dir,
            "--start-date", cs,
            "--end-date", ce,
            "--workers", str(args.workers),
            "--temporal-contract-path", args.contract,
            "--temporal-role", args.role,
        ]
        print(f"[{i}/{total}] {cs} .. {ce}", flush=True)
        result = subprocess.run(
            cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            # extract last error line
            err_lines = [l for l in result.stderr.strip().splitlines() if l.strip()]
            err = err_lines[-1] if err_lines else result.stdout.strip().splitlines()[-1:]
            err_str = " ".join(err)[-200:]
            print(f"  FAILED: {err_str}")
            failures.append((cs, ce, err_str))
        else:
            print(f"  ok")

    print(f"\nDone: {total} chunks, {len(failures)} failures.")
    if failures:
        print("Failures:")
        for cs, ce, err in failures:
            print(f"  {cs} .. {ce}: {err}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
