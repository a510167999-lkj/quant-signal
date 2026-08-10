#!/usr/bin/env python3
"""Path A F1: post-train OOS collect inventory + zero-refit replay."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_f1_oos_collect_replay import (  # noqa: E402
    DEFAULT_OOS_END,
    DEFAULT_OUTPUT_ROOT,
    OOS_COLLECTION_START,
    STAGE_GOAL_ID,
    build_path_a_f1_oos_collect_replay,
    write_path_a_f1_oos_collect_replay,
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
    parser = argparse.ArgumentParser(description=f"Path A F1 ({STAGE_GOAL_ID})")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--allow-any-role", action="store_true")
    parser.add_argument(
        "--collect",
        action="store_true",
        help="Attempt OOS market collect into path_a_oos_market (needs JIAOCH_TOKEN)",
    )
    parser.add_argument(
        "--generate-qualified",
        action="store_true",
        help="Generate OOS qualified trades into path_a_oos/ (never overwrites train cache)",
    )
    parser.add_argument("--max-universe-symbols", type=int, default=300)
    parser.add_argument("--oos-end-date", default=DEFAULT_OOS_END)
    parser.add_argument("--oos-qualified-path", type=Path, default=None)
    args = parser.parse_args(argv)

    repo = args.repo_root.resolve()
    _load_dotenv(repo / ".env")
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")

    report = build_path_a_f1_oos_collect_replay(
        repo_root=repo,
        require_local_research=not args.allow_any_role,
        collect=bool(args.collect),
        generate_qualified=bool(args.generate_qualified),
        oos_end_date=str(args.oos_end_date),
        oos_qualified_path=args.oos_qualified_path,
        max_universe_symbols=int(args.max_universe_symbols),
    )
    output_root = (
        args.output_root
        if args.output_root is not None
        else (repo / DEFAULT_OUTPUT_ROOT)
    ).resolve()
    pointer = write_path_a_f1_oos_collect_replay(report, output_root=output_root)
    print(json.dumps(pointer, ensure_ascii=False, indent=2))
    print(f"\noos_window={OOS_COLLECTION_START}..{args.oos_end_date}")
    print("shadow_oos_collect_ok=", report.get("shadow_oos_collect_ok"))
    print("shadow_oos_replay_ok=", report.get("shadow_oos_replay_ok"))
    print("formal_final_oos_still_sealed=", report.get("formal_final_oos_still_sealed"))
    print("effective_strategy_found=", report.get("effective_strategy_found"))
    gen = report.get("generate_qualified_attempt") or {}
    if gen:
        print(
            "generate_qualified oos_trades=",
            gen.get("oos_trade_count"),
            "signals=",
            gen.get("first_signal_date"),
            "..",
            gen.get("last_signal_date"),
        )
    zr = report.get("zero_refit_replay") or {}
    if zr:
        inv = zr.get("inventory") or {}
        print(
            "zero_refit executed=",
            zr.get("executed"),
            "selected=",
            zr.get("selected_trade_count"),
            "reason=",
            zr.get("reason"),
            "port_ret=",
            zr.get("portfolio_compounded_return_pct"),
            "port_mdd=",
            zr.get("portfolio_max_drawdown_pct"),
        )
        if inv:
            print(
                "inventory levels=",
                inv.get("market_level_counts"),
                "prefilter_match=",
                inv.get("count_matching_frozen_spec_prefilter"),
            )
    print("\nblockers:")
    for b in report.get("blockers") or []:
        print("-", b.get("code"), ":", b.get("detail"))
    print("\nwarnings:")
    for w in report.get("warnings") or []:
        print("-", w.get("code"), ":", w.get("detail"))
    print("\nnext_actions:")
    for a in report.get("next_actions") or []:
        print("-", a)
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
