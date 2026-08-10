#!/usr/bin/env python3
"""Path A P1-B: walk-forward protocol (zero-refit test folds)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_p1_walk_forward import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_p1_walk_forward,
    write_path_a_p1_walk_forward,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Path A P1-B ({STAGE_GOAL_ID})")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")

    report = build_path_a_p1_walk_forward(
        repo_root=repo,
        require_local_research=not args.allow_any_role,
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (repo / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_p1_walk_forward(report, output_root=output_root)
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print("\nfolds:", report.get("fold_count"))
    for f in report.get("folds") or []:
        m = f.get("metrics") or {}
        print(
            f"- fold {f.get('fold_index')}: "
            f"{f['test_window']['start']}..{f['test_window']['end']} "
            f"n={f.get('test_trade_count')} "
            f"ret={m.get('portfolio_compounded_return_pct')} "
            f"mdd={m.get('portfolio_max_drawdown_pct')}"
        )
    sp = (report.get("spliced_all_test") or {}).get("metrics") or {}
    print(
        f"\nspliced: ret={sp.get('portfolio_compounded_return_pct')} "
        f"mdd={sp.get('portfolio_max_drawdown_pct')} "
        f"n={sp.get('selected_trade_count')}"
    )
    print("walk_forward_md=", pointer.get("walk_forward_md"))
    print("guarantee=", report.get("meets_user_requirement_as_guarantee"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
