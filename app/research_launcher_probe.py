"""A zero-ledger-write child for supervised launcher handshake verification."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from app.research_launcher_ack import wait_for_launcher_ack


def _sha256(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("launcher probe hash is invalid")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-write supervised launcher probe")
    parser.add_argument("--plan-sha256", required=True)
    parser.add_argument("--plan-file-sha256", required=True)
    parser.add_argument("--fixture-id", required=True)
    parser.add_argument("--fixture-manifest-file-sha256", required=True)
    args = parser.parse_args(argv)
    if sys.stdin.buffer.read() != b"":
        raise ValueError("launcher probe stdin must be closed")
    ready = {
        "schema_version": "research-launcher-ready/v1",
        "event": "READY",
        "plan_sha256": _sha256(args.plan_sha256),
        "plan_file_sha256": _sha256(args.plan_file_sha256),
        "fixture_id": _sha256(args.fixture_id),
        "fixture_manifest_file_sha256": _sha256(args.fixture_manifest_file_sha256),
    }
    print(
        "READY "
        + json.dumps(
            ready,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ),
        flush=True,
    )
    wait_for_launcher_ack(ready)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
