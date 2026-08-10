#!/usr/bin/env python3
"""CLI: inventory parent-source / evaluation authority binding surface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_parent_eval_authority_inventory import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_parent_eval_authority_inventory,
    write_inventory_evidence,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory parent-source / evaluation / activation binding surfaces "
            f"for stage {STAGE_GOAL_ID}."
        )
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Evidence output root (default under data/research_runs/...)",
    )
    parser.add_argument(
        "--allow-any-role",
        action="store_true",
        help="Skip VPS_RUNTIME_ROLE=local_research requirement (tests only).",
    )
    args = parser.parse_args(argv)

    inventory = build_parent_eval_authority_inventory(
        repo_root=args.repo_root.resolve(),
        require_local_research=not args.allow_any_role,
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (args.repo_root / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_inventory_evidence(inventory, output_root=output_root)

    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print("\n--- gap summary ---")
    for code in inventory.get("gap_codes", []):
        print(f"- {code}")
    print(f"\nupstream_ready_for_cross_bind={inventory.get('upstream_ready_for_cross_bind')}")
    print(f"formal_parent_eval_ready={inventory.get('formal_parent_eval_ready')}")
    print(f"ok={inventory.get('ok')}")
    return 0 if inventory.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
