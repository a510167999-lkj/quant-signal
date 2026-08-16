"""Load Path-A turnover / moneyflow / monthly industry panels."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

TURNOVER_ROOT = Path("data/research_cache/jiaoch_turnover_path_a")
FLOW_ROOT = Path("data/research_cache/jiaoch_moneyflow_path_a")
INDUSTRY_ROOT = Path("data/research_cache/jiaoch_bak_basic_month_path_a")


def _load_day_map(root: Path, field: str) -> dict[tuple[str, str], float | None]:
    out: dict[tuple[str, str], float | None] = {}
    days = root / "days"
    if not days.is_dir():
        return out
    for path in days.glob("*.json"):
        payload = json.loads(path.read_text(encoding="utf-8"))
        day = str(payload.get("trade_date") or path.stem)[:10]
        for row in payload.get("rows") or []:
            code = str(row.get("ts_code") or "")
            if not code:
                continue
            raw = row.get(field)
            try:
                out[(code, day)] = None if raw is None else float(raw)
            except (TypeError, ValueError):
                out[(code, day)] = None
    return out


def load_turnover_panel(repo: Path) -> dict[tuple[str, str], float | None]:
    return _load_day_map(repo / TURNOVER_ROOT, "turnover_rate")


def load_flow_panel(repo: Path) -> dict[tuple[str, str], float | None]:
    return _load_day_map(repo / FLOW_ROOT, "net_mf_amount")


def load_industry_membership(repo: Path) -> list[tuple[str, dict[str, str]]]:
    days = repo / INDUSTRY_ROOT / "days"
    layers: list[tuple[str, dict[str, str]]] = []
    if not days.is_dir():
        return layers
    for path in sorted(days.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        day = str(payload.get("trade_date") or path.stem)[:10]
        mapping: dict[str, str] = {}
        for row in payload.get("rows") or []:
            code = str(row.get("ts_code") or "")
            industry = str(row.get("industry") or "").strip()
            if code and industry:
                mapping[code] = industry
        if mapping:
            layers.append((day, mapping))
    return layers


def industry_as_of(
    layers: list[tuple[str, dict[str, str]]], day: str, ts_code: str
) -> str | None:
    chosen: dict[str, str] | None = None
    for start, mapping in layers:
        if start <= day:
            chosen = mapping
        else:
            break
    if chosen is None:
        return None
    return chosen.get(ts_code)


def attach_numeric_column(
    frame: pd.DataFrame,
    panel: dict[tuple[str, str], float | None],
    ts_code: str,
    column: str,
) -> pd.DataFrame:
    work = frame.copy()
    dates = work["date"].astype(str).str.slice(0, 10)
    work[column] = [panel.get((ts_code, day)) for day in dates]
    return work


def attach_industry_ranks(
    prepared: list[tuple[dict[str, Any], pd.DataFrame]],
    layers: list[tuple[str, dict[str, str]]],
) -> list[tuple[dict[str, Any], pd.DataFrame]]:
    """Equal-weight 20d industry return rank using as-of monthly membership."""

    if not prepared or not layers:
        return prepared
    ret_by_code: dict[str, pd.Series] = {}
    for item, frame in prepared:
        code = str(item.get("ts_code") or "")
        if not code or "close" not in frame.columns or "date" not in frame.columns:
            continue
        work = frame.copy()
        work["date"] = work["date"].astype(str).str.slice(0, 10)
        close = pd.to_numeric(work["close"], errors="coerce")
        series = close.pct_change(20)
        series.index = work["date"]
        ret_by_code[code] = series
    dates = sorted({day for series in ret_by_code.values() for day in series.index})
    rank_by_code: dict[str, dict[str, float]] = {code: {} for code in ret_by_code}
    for day in dates:
        buckets: dict[str, list[float]] = {}
        member_of: dict[str, str] = {}
        for code, series in ret_by_code.items():
            industry = industry_as_of(layers, day, code)
            if not industry or day not in series.index:
                continue
            value = series.loc[day]
            if pd.isna(value):
                continue
            buckets.setdefault(industry, []).append(float(value))
            member_of[code] = industry
        if not buckets:
            continue
        ind_ret = {ind: sum(vals) / len(vals) for ind, vals in buckets.items()}
        ordered = sorted(ind_ret, key=lambda name: ind_ret[name])
        n = len(ordered)
        ranks = {name: (index + 1) / n for index, name in enumerate(ordered)}
        for code, industry in member_of.items():
            rank_by_code[code][day] = ranks[industry]
    out: list[tuple[dict[str, Any], pd.DataFrame]] = []
    for item, frame in prepared:
        work = frame.copy()
        dates = work["date"].astype(str).str.slice(0, 10)
        code = str(item.get("ts_code") or "")
        ranks = rank_by_code.get(code) or {}
        work["industry_rank"] = [ranks.get(day) for day in dates]
        out.append((item, work))
    return out
