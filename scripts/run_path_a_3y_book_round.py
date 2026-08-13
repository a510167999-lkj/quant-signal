#!/usr/bin/env python3
"""Score the frozen 3y selection-book set on Jiaoch v2 QT."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_path_a_3y_book_round import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_QT,
    STAGE_GOAL_ID,
    build_path_a_3y_book_round,
    format_book_round_table,
    write_path_a_3y_book_round,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=STAGE_GOAL_ID)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--qualified-trades-path", type=Path, default=DEFAULT_QT)
    parser.add_argument("--allow-any-role", action="store_true")
    parser.add_argument(
        "--family",
        choices=("book", "combo", "combo_overlay", "clip", "volclip"),
        default="book",
    )
    args = parser.parse_args(argv)
    repo = args.repo_root.resolve()
    os.environ.setdefault("VPS_RUNTIME_ROLE", "local_research")
    qt = args.qualified_trades_path
    report = build_path_a_3y_book_round(
        repo_root=repo,
        qualified_trades_path=qt if qt.is_absolute() else (repo / qt),
        require_local_research=not args.allow_any_role,
        family=args.family,
    )
    family_roots = {
        "book": DEFAULT_OUTPUT_ROOT,
        "combo": Path("data/research_runs/path_a_3y_combo_round"),
        "combo_overlay": Path("data/research_runs/path_a_3y_combo_overlay_round"),
        "clip": Path("data/research_runs/path_a_3y_clip_round"),
        "volclip": Path("data/research_runs/path_a_3y_volclip_round"),
    }
    chosen_root = args.output_root or family_roots[args.family]
    output_root = (
        chosen_root if chosen_root.is_absolute() else (repo / chosen_root)
    ).resolve()
    pointer = write_path_a_3y_book_round(report, output_root=output_root)
    print(format_book_round_table(report), end="")
    print("wrote", pointer.get("table_path"))
    return 0 if pointer.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
