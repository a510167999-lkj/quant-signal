"""Path A P1-B: walk-forward protocol (zero-refit test folds).

Catalog §3.2 P1-B:
- train segment 12 months (evaluation only; NO in-fold grid search)
- test segment 3 months
- step 3 months (non-overlapping test segments)
- test folds run the FROZEN spec; zero parameter change
- report per-fold metrics + a spliced all-test equity curve

The signal kernel is the frozen e4_primary; this engine does not search, it
only slices the frozen kernel selection by signal_date month and evaluates.
WF splice != formal final-OOS.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from app import factor_v3_path_a_f0_oos_readiness as f0
from app import factor_v3_path_a_frozen_train_window_replay as train_replay
from app import factor_v3_path_a_p0_drawdown_overlay as p0
from app import factor_v3_path_a_p1_acceptance_protocol as p1a
from app import factor_v3_path_a_p0_drawdown_overlay_specs as specs
from app import factor_v3_train_window_freeze_contract as freeze
from app.research_sweep import _trade_metrics

STAGE_GOAL_ID = "path-a-p1-walk-forward/v1"
STAGE_GOAL_SUMMARY = (
    "路径 A P1-B：walk-forward 协议（12m train / 3m test / 3m step），"
    "test fold 零改参，train fold 仅评估不搜参；WF 拼接 ≠ formal final-OOS；"
    "永不自动交易；不保证 50/15。"
)
REPORT_SCHEMA = "path-a-p1-walk-forward-report/v1"
POINTER_SCHEMA = "path-a-p1-walk-forward-pointer/v1"
REQUIRED_RUNTIME_ROLE = "local_research"

# Protocol constants (catalog §3.2 P1-B — locked; change => bump version).
TRAIN_MONTHS = 12
TEST_MONTHS = 3
STEP_MONTHS = 3

DEFAULT_FROZEN_CANDIDATE = Path(
    "data/research_runs/path_a_f0_oos_readiness/FROZEN_CANDIDATE.json"
)
DEFAULT_QUALIFIED_TRADES = Path("data/research_cache/qualified_hold5_stop5.json")
DEFAULT_OUTPUT_ROOT = Path("data/research_runs/path_a_p1_acceptance")


class PathAP1Error(ValueError):
    """Raised when P1 walk-forward stage fails closed."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_local_research() -> str:
    role = os.getenv("VPS_RUNTIME_ROLE", "")
    if not isinstance(role, str) or role.strip().casefold() != REQUIRED_RUNTIME_ROLE:
        raise PathAP1Error(
            f"requires VPS_RUNTIME_ROLE={REQUIRED_RUNTIME_ROLE} (got {role!r})"
        )
    return role.strip()


def _add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    """Add delta months, returning (year, month). delta may be negative."""

    idx = (year * 12 + (month - 1)) + delta
    return idx // 12, idx % 12 + 1


def _month_of(signal_date: str) -> tuple[int, int]:
    return int(signal_date[:4]), int(signal_date[5:7])


def build_walk_forward_folds(
    kernel_selected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Slice the frozen kernel selection into non-overlapping test folds.

    Each fold records its train window [train_start, train_end] and test window
    [test_start, test_end] (inclusive month boundaries) plus the trades whose
    signal_date falls in the test window. Train folds are listed for audit but
    are evaluated only (never searched).
    """

    if not kernel_selected:
        return []
    dates = [str(t.get("signal_date") or "")[:10] for t in kernel_selected]
    dates = [d for d in dates if d]
    if not dates:
        return []
    dates.sort()
    first_y, first_m = _month_of(dates[0])
    last_y, last_m = _month_of(dates[-1])

    folds: list[dict[str, Any]] = []
    # First test window starts after the first TRAIN_MONTHS.
    offset = TRAIN_MONTHS
    ty, tm = _add_months(first_y, first_m, offset)
    fold_index = 0
    while (ty, tm) <= (last_y, last_m):
        test_start_y, test_start_m = ty, tm
        # test window is [test_start_m .. test_start_m + TEST_MONTHS - 1]
        te_y, te_m = _add_months(test_start_y, test_start_m, TEST_MONTHS - 1)
        # train window is the TEST_MONTHS * preceding * TRAIN_MONTHS before test
        train_end_y, train_end_m = _add_months(test_start_y, test_start_m, -1)
        train_start_y, train_start_m = _add_months(
            train_end_y, train_end_m, -(TRAIN_MONTHS - 1)
        )

        def _in_window(d: str, sy: int, sm: int, ey: int, em: int) -> bool:
            y, m = _month_of(d)
            return (y, m) >= (sy, sm) and (y, m) <= (ey, em)

        test_trades = [
            t
            for t in kernel_selected
            if _in_window(
                str(t.get("signal_date") or "")[:10],
                test_start_y,
                test_start_m,
                te_y,
                te_m,
            )
        ]
        train_trades = [
            t
            for t in kernel_selected
            if _in_window(
                str(t.get("signal_date") or "")[:10],
                train_start_y,
                train_start_m,
                train_end_y,
                train_end_m,
            )
        ]
        folds.append(
            {
                "fold_index": fold_index,
                "train_window": {
                    "start": f"{train_start_y:04d}-{train_start_m:02d}",
                    "end": f"{train_end_y:04d}-{train_end_m:02d}",
                },
                "test_window": {
                    "start": f"{test_start_y:04d}-{test_start_m:02d}",
                    "end": f"{te_y:04d}-{te_m:02d}",
                },
                "train_trade_count": len(train_trades),
                "test_trade_count": len(test_trades),
                "test_trades": test_trades,
            }
        )
        fold_index += 1
        ty, tm = _add_months(ty, tm, STEP_MONTHS)
    return folds


def _kernel_dict() -> dict[str, Any]:
    return p0._kernel_dict()


def _fold_metrics(test_trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate a single test fold with the frozen kernel (zero refit)."""

    kernel = _kernel_dict()
    if not test_trades:
        return {
            "selected_trade_count": 0,
            "signal_days": 0,
            "portfolio_compounded_return_pct": 0.0,
            "portfolio_max_drawdown_pct": 0.0,
            "rolling_1y_latest_return_pct": None,
            "rolling_1y_latest_max_drawdown_pct": None,
            "rolling_12m_summary": train_replay._rolling_summary([]),
            "trade_win_rate_pct": None,
        }
    metrics = _trade_metrics(
        test_trades,
        hold_days=int(kernel["hold_days"]),
        max_active_positions=int(kernel["max_active_positions"]),
        exposure_multiplier=float(kernel["exposure_multiplier"]),
        annual_financing_rate_pct=float(kernel["annual_financing_rate_pct"]),
        roundtrip_cost_bps=float(kernel["roundtrip_cost_bps"]),
        slippage_bps=float(kernel["slippage_bps"]),
        capital_model=str(kernel["capital_model"]),
    )
    rs = train_replay._rolling_summary(
        [train_replay._window_row(w) for w in (metrics.get("rolling_1y_windows") or [])]
    )
    return {
        "selected_trade_count": int(metrics.get("selected_trade_count") or 0),
        "signal_days": int(metrics.get("signal_days") or 0),
        "portfolio_compounded_return_pct": metrics.get(
            "portfolio_compounded_return_pct"
        ),
        "portfolio_max_drawdown_pct": metrics.get("portfolio_max_drawdown_pct"),
        "rolling_1y_latest_return_pct": metrics.get("rolling_1y_latest_return_pct"),
        "rolling_1y_latest_max_drawdown_pct": metrics.get(
            "rolling_1y_latest_max_drawdown_pct"
        ),
        "rolling_12m_summary": rs,
        "trade_win_rate_pct": metrics.get("trade_win_rate_pct"),
    }


def build_path_a_p1_walk_forward(
    *,
    repo_root: Path | None = None,
    require_local_research: bool = True,
) -> dict[str, Any]:
    if require_local_research:
        role = _require_local_research()
    else:
        role = os.getenv("VPS_RUNTIME_ROLE", "")

    freeze.assert_train_window_freeze_consistent()
    root = (repo_root or Path.cwd()).resolve()

    card_path = root / DEFAULT_FROZEN_CANDIDATE
    card = train_replay._load_json(card_path)
    if card is None:
        raise PathAP1Error(f"frozen candidate missing: {card_path}")
    if card.get("candidate_id") != f0.FROZEN_PRIMARY_CANDIDATE["candidate_id"]:
        raise PathAP1Error("frozen candidate id mismatch")

    qt_path = root / DEFAULT_QUALIFIED_TRADES
    qt = train_replay._load_json(qt_path)
    if qt is None:
        raise PathAP1Error("qualified trades missing")
    train_trades = train_replay._filter_train_trades(list(qt.get("qualified_trades") or []))
    kernel_selected = p0._select_kernel_trades(train_trades)

    folds = build_walk_forward_folds(kernel_selected)
    kernel = _kernel_dict()

    fold_reports: list[dict[str, Any]] = []
    all_test_trades: list[dict[str, Any]] = []
    for fold in folds:
        fm = _fold_metrics(fold["test_trades"])
        all_test_trades.extend(fold["test_trades"])
        fold_reports.append(
            {
                "fold_index": fold["fold_index"],
                "train_window": fold["train_window"],
                "test_window": fold["test_window"],
                "train_trade_count": fold["train_trade_count"],
                "test_trade_count": fold["test_trade_count"],
                "metrics": fm,
                "scoreboard": p1a.build_w_scoreboard(fm),
            }
        )

    # Spliced all-test equity (treat all test-fold trades as one sequence).
    spliced = _fold_metrics(all_test_trades)
    spliced_scoreboard = p1a.build_w_scoreboard(spliced)

    unsigned = {
        "schema": REPORT_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "stage_goal_summary": STAGE_GOAL_SUMMARY,
        "path": "A_no_tcb_p1_walk_forward",
        "development_only": True,
        "vps_runtime_role": role,
        "automatic_trading_allowed": False,
        "production_profile_registered": False,
        "formal_materialization_eligible": False,
        "tcb_used": False,
        "refit": False,
        "parameter_search": False,
        "protocol": {
            "train_months": TRAIN_MONTHS,
            "test_months": TEST_MONTHS,
            "step_months": STEP_MONTHS,
            "test_fold_zero_refit": True,
            "train_fold_search_allowed": False,
            "non_overlapping_test_segments": True,
        },
        "signal_kernel": kernel,
        "signal_kernel_sha256": p0._sha(kernel),
        "frozen_candidate_id": card.get("candidate_id"),
        "frozen_candidate_spec_sha256": card.get("candidate_spec_sha256"),
        "train_window_freeze": freeze.train_window_freeze_descriptor(),
        "kernel_selected_trade_count": len(kernel_selected),
        "fold_count": len(fold_reports),
        "folds": fold_reports,
        "spliced_all_test": {
            "metrics": spliced,
            "scoreboard": spliced_scoreboard,
            "p1a_acceptance": p1a.validate_variants(
                [{"candidate_id": "spliced_all_test", "scoreboard": spliced_scoreboard}]
            ),
        },
        "effective_strategy_found": False,
        "formal_final_oos_executable": False,
        "meets_user_requirement_as_guarantee": False,
        "notes": (
            "WF spliced test-fold equity != formal final-OOS. "
            "Train folds evaluated only; zero refit on test folds. "
            "Short train window (~2y) yields few folds; limited statistical power."
        ),
        "ok": True,
    }
    return {**unsigned, "report_sha256": _sha(unsigned)}


def write_path_a_p1_walk_forward(
    report: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    if report.get("ok") is not True:
        raise PathAP1Error("refuse non-ok")
    output_root.mkdir(parents=True, exist_ok=True)
    digest = report["report_sha256"]
    cas = output_root / "sha256" / digest[:2] / digest
    cas.mkdir(parents=True, exist_ok=True)
    path = cas / "path-a-p1-walk-forward.json"
    raw = _canonical_bytes(report) + b"\n"
    if path.exists() and path.read_bytes() != raw:
        raise PathAP1Error("CAS collision")
    if not path.exists():
        path.write_bytes(raw)

    lines = [
        "# P1-B Walk-forward（零改参 test fold）",
        "",
        f"- stage: `{report.get('stage_goal_id')}`",
        f"- protocol: train {TRAIN_MONTHS}m / test {TEST_MONTHS}m / step {STEP_MONTHS}m",
        f"- folds: {report.get('fold_count')}",
        f"- guarantee: **false** · WF 拼接 ≠ formal final-OOS",
        "",
        "## 每折（test fold 冻结规格）",
        "",
        "| fold | train | test | 笔数 | 收益% | MDD% |",
        "|------|-------|------|------|-------|------|",
    ]
    for f in report.get("folds") or []:
        m = f.get("metrics") or {}
        lines.append(
            "| {idx} | {tr} | {te} | {n} | {ret} | {mdd} |".format(
                idx=f.get("fold_index"),
                tr=f"{f['train_window']['start']}..{f['train_window']['end']}",
                te=f"{f['test_window']['start']}..{f['test_window']['end']}",
                n=f.get("test_trade_count"),
                ret=_fmt(m.get("portfolio_compounded_return_pct")),
                mdd=_fmt(m.get("portfolio_max_drawdown_pct")),
            )
        )
    sp = (report.get("spliced_all_test") or {}).get("metrics") or {}
    lines.extend(
        [
            "",
            "## 拼接全程",
            "",
            f"- 收益: {_fmt(sp.get('portfolio_compounded_return_pct'))}",
            f"- MDD: {_fmt(sp.get('portfolio_max_drawdown_pct'))}",
            f"- 笔数: {sp.get('selected_trade_count')}",
            "",
            "## 边界",
            "",
            "- WF 拼接成绩仍 ≠ formal final-OOS",
            "- train fold 仅评估，未搜参；test fold 零改参",
            "- 训练窗约 2y，fold 数有限，统计意义有限",
            "",
        ]
    )
    md_path = output_root / "WALK_FORWARD.md"
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    pointer = {
        "schema": POINTER_SCHEMA,
        "stage_goal_id": STAGE_GOAL_ID,
        "path": "A_no_tcb_p1_walk_forward",
        "ok": True,
        "development_only": True,
        "automatic_trading_allowed": False,
        "refit": False,
        "parameter_search": False,
        "effective_strategy_found": False,
        "meets_user_requirement_as_guarantee": False,
        "fold_count": report.get("fold_count"),
        "spliced_scoreboard": (report.get("spliced_all_test") or {}).get("scoreboard"),
        "report_sha256": digest,
        "evidence_path": str(path.resolve()),
        "evidence_file_sha256": hashlib.sha256(raw).hexdigest(),
        "walk_forward_md": str(md_path.resolve()),
    }
    (output_root / "LATEST.json").write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return pointer


def _fmt(v: Any) -> str:
    if v is None:
        return ""
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)


__all__ = [
    "DEFAULT_OUTPUT_ROOT",
    "POINTER_SCHEMA",
    "REPORT_SCHEMA",
    "REQUIRED_RUNTIME_ROLE",
    "STEP_MONTHS",
    "STAGE_GOAL_ID",
    "STAGE_GOAL_SUMMARY",
    "TEST_MONTHS",
    "TRAIN_MONTHS",
    "PathAP1Error",
    "build_path_a_p1_walk_forward",
    "build_walk_forward_folds",
    "write_path_a_p1_walk_forward",
]
