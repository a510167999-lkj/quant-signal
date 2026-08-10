#!/usr/bin/env python3
"""Path A F0: audit formal final-OOS readiness and freeze E4 primary candidate."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_f0_oos_readiness import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_f0_oos_readiness,
    write_path_a_f0_oos_readiness,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Path A F0 ({STAGE_GOAL_ID})")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)

    report = build_path_a_f0_oos_readiness(
        repo_root=args.repo_root.resolve(),
        require_local_research=not args.allow_any_role,
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (args.repo_root / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_f0_oos_readiness(report, output_root=output_root)
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print("\nblockers:")
    for b in report.get("blockers") or []:
        print("-", b.get("code"), ":", b.get("detail"))
    print("\nwarnings:")
    for w in report.get("warnings") or []:
        print("-", w.get("code"), ":", w.get("detail"))
    print("formal_final_oos_executable=", report.get("formal_final_oos_executable"))
    print("candidate_status=", report.get("candidate_status"))
    print("effective_strategy_found=", report.get("effective_strategy_found"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
