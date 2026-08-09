#!/usr/bin/env python3
"""CLI: audit Factor V3 development training readiness toward the research goal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.factor_v3_training_readiness_audit import (  # noqa: E402
    render_markdown,
    run_factor_v3_training_readiness_audit,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit Factor V3 development training-matrix readiness."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=ROOT,
        help="Repository root (default: parent of scripts/).",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional path to write machine-readable JSON report.",
    )
    parser.add_argument(
        "--md-out",
        type=Path,
        default=None,
        help="Optional path to write Markdown report.",
    )
    args = parser.parse_args(argv)

    report = run_factor_v3_training_readiness_audit(repo_root=args.repo_root)
    markdown = render_markdown(report)
    print(markdown)

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\nWrote JSON: {args.json_out}")
    if args.md_out is not None:
        args.md_out.parent.mkdir(parents=True, exist_ok=True)
        args.md_out.write_text(markdown, encoding="utf-8")
        print(f"Wrote Markdown: {args.md_out}")

    return 0 if report.ready_for_formal_development_materialization else 2


if __name__ == "__main__":
    raise SystemExit(main())
