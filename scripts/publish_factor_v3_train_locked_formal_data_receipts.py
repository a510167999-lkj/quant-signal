#!/usr/bin/env python3
"""Publish train-locked formal upstream data receipt binder (before 2026-08-01)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_train_locked_formal_data_receipts import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_train_locked_formal_data_receipt_bundle,
    write_train_locked_formal_data_receipts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            f"Publish train-locked formal upstream data receipts ({STAGE_GOAL_ID}). "
            "Freeze: train before 2026-08-01; no daily incremental sync."
        )
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument(
        "--allow-any-role",
        action="store_true",
        help="Skip VPS_RUNTIME_ROLE=local_research (tests only).",
    )
    args = parser.parse_args(argv)

    bundle = build_train_locked_formal_data_receipt_bundle(
        repo_root=args.repo_root.resolve(),
        require_local_research=not args.allow_any_role,
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (args.repo_root / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_train_locked_formal_data_receipts(
        bundle, output_root=output_root
    )
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print("\nupstream_formal_data_complete=", bundle.get("upstream_formal_data_complete"))
    print("parent_eval_formal_complete=", bundle.get("parent_eval_formal_complete"))
    print(
        "train_end=",
        bundle["train_window_freeze"]["train_inclusive_session_end"],
        "exclusive=",
        bundle["train_window_freeze"]["train_exclusive_end_date"],
    )
    print("daily_incremental_sync_required=", bundle.get("daily_incremental_sync_required"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
