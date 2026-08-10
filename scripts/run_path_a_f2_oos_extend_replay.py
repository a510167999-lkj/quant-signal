#!/usr/bin/env python3
"""Path A F2: extend OOS window and re-run zero-refit."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_f2_oos_extend_replay import (  # noqa: E402
    DEFAULT_OOS_END,
    DEFAULT_OUTPUT_ROOT,
    STAGE_GOAL_ID,
    build_path_a_f2_oos_extend_replay,
    write_path_a_f2_oos_extend_replay,
)


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Path A F2 ({STAGE_GOAL_ID})")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    parser.add_argument("--no-extend", action="store_true")
    parser.add_argument("--no-generate-qualified", action="store_true")
    parser.add_argument("--oos-end-date", default=DEFAULT_OOS_END)
    parser.add_argument("--max-universe-symbols", type=int, default=300)
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    _load_dotenv(repo / ".env")
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")

    report = build_path_a_f2_oos_extend_replay(
        repo_root=repo,
        require_local_research=not args.allow_any_role,
        extend=not args.no_extend,
        generate_qualified=not args.no_generate_qualified,
        oos_end_date=str(args.oos_end_date),
        max_universe_symbols=int(args.max_universe_symbols),
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (repo / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_f2_oos_extend_replay(report, output_root=output_root)
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    cmp_ = report.get("comparison_to_f1") or {}
    print("\ncomparison_to_f1:")
    for k in (
        "f1_oos_sessions",
        "f2_oos_sessions",
        "sessions_delta",
        "f1_oos_qualified_trades",
        "f2_oos_qualified_trades",
        "qualified_trades_delta",
        "f1_zero_refit_selected",
        "f2_zero_refit_selected",
        "f2_store_session_max",
        "f2_qt_signal_max",
    ):
        print(f"  {k}=", cmp_.get(k))
    zr = report.get("zero_refit_replay") or {}
    inv = zr.get("inventory") or {}
    print(
        "zero_refit executed=",
        zr.get("executed"),
        "selected=",
        zr.get("selected_trade_count"),
        "reason=",
        zr.get("reason"),
    )
    if inv:
        print(
            "inventory levels=",
            inv.get("market_level_counts"),
            "prefilter_match=",
            inv.get("count_matching_frozen_spec_prefilter"),
        )
    print("effective_strategy_found=", report.get("effective_strategy_found"))
    print("\nblockers:")
    for b in report.get("blockers") or []:
        print("-", b.get("code"), ":", b.get("detail"))
    print("\nnext_actions:")
    for a in report.get("next_actions") or []:
        print("-", a)
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
