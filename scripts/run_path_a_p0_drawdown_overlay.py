#!/usr/bin/env python3
"""Path A P0: drawdown overlay comparison on frozen signal kernel."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_p0_drawdown_overlay import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_p0_drawdown_overlay,
    write_path_a_p0_drawdown_overlay,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Path A P0 ({STAGE_GOAL_ID})")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    parser.add_argument("--qualified-trades-path", type=Path, default=None)
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")

    report = build_path_a_p0_drawdown_overlay(
        repo_root=repo,
        require_local_research=not args.allow_any_role,
        qualified_trades_path=args.qualified_trades_path,
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (repo / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_p0_drawdown_overlay(report, output_root=output_root)
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print("\nranking:", report.get("ranking_by_mdd_then_stability"))
    print("best_by_mdd:", report.get("best_by_mdd"))
    print("\nvariants:")
    for v in report.get("variants") or []:
        m = v.get("metrics") or {}
        a = v.get("overlay_audit") or {}
        print(
            f"- {v.get('candidate_id')}: ret={m.get('portfolio_compounded_return_pct')} "
            f"mdd={m.get('portfolio_max_drawdown_pct')} "
            f"both={m.get('rolling_both_pass_rate')} "
            f"blocked={a.get('blocked_new_count')} half={a.get('half_size_count')}"
        )
    print("comparison_md=", pointer.get("comparison_md"))
    print("guarantee=", report.get("meets_user_requirement_as_guarantee"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
