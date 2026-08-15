"""Build a Jiaoch-only QT from pre-registered MA20 reclaim signals."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from app.execution import assess_entry_executability
from app.factor_v3_path_a_protocol_signal_specs import (
    BOLL_RECLAIM_TAG,
    BREAKOUT_60D_TAG,
    DN2_BOUNCE_TAG,
    DN3_BOUNCE_TAG,
    ENGULF_TAG,
    HAMMER_TAG,
    INSIDE_UP_TAG,
    KDJ_OVERSOLD_TAG,
    LIMIT_FOLLOW_TAG,
    LO20_BOUNCE_TAG,
    MA10_RECLAIM_TAG,
    MA60_RECLAIM_TAG,
    MACD_GOLD_TAG,
    NR7_UP_TAG,
    PULLBACK_TAG,
    RECLAIM_TAG,
    SIGNAL_BOUNCE_FAMILY,
    SIGNAL_BOUNCE_STAGE_GOAL_ID,
    SIGNAL_CLASSIC_FAMILY,
    SIGNAL_CLASSIC_STAGE_GOAL_ID,
    SIGNAL_ENTRY_FAMILY,
    SIGNAL_ENTRY_STAGE_GOAL_ID,
    SIGNAL_FAMILY,
    SIGNAL_HOLD_FAMILY,
    SIGNAL_HOLD_STAGE_GOAL_ID,
    SIGNAL_SHAPE_FAMILY,
    SIGNAL_SHAPE_STAGE_GOAL_ID,
    STAGE_GOAL_ID,
    TIGHT5_UP_TAG,
)
from app.indicators import add_indicators
from app.jiaoch_live_market import JIAOCH_DAILY_CACHE_SOURCE_VERSION
from app.research_context import (
    _historical_market_breadth,
    _stock_breadth_market_context,
    _stock_market_returns,
    _stock_relative_strength_context,
)
from app.research_goal_contract import DATA_SOURCE_POLICY
from app.research_portfolio import _realized_trade_from_future
from app.signal_tags import (
    build_candidate_context_tags,
    build_market_breadth_tags,
    build_price_action_tags,
)
from app.storage import write_json

START_DATE = "2023-07-03"
END_DATE = "2026-07-03"
HOLD_DAYS = 5
STOP_LOSS_PCT = 5.0
DEFAULT_CACHE_DIR = Path("data/research_cache/jiaoch_stk_mins_3y_v2_holdout")
DEFAULT_QT_PATH = Path(
    "data/research_cache/qualified_hold5_stop5_3y_jiaoch_signal.json"
)
DEFAULT_HOLD_QT_PATH = Path(
    "data/research_cache/qualified_hold5_10_stop5_3y_jiaoch_signal.json"
)
DEFAULT_ENTRY_QT_PATH = Path(
    "data/research_cache/qualified_hold5_stop5_3y_jiaoch_entry.json"
)
DEFAULT_BOUNCE_QT_PATH = Path(
    "data/research_cache/qualified_hold5_stop5_3y_jiaoch_bounce.json"
)
DEFAULT_SHAPE_QT_PATH = Path(
    "data/research_cache/qualified_hold5_stop5_3y_jiaoch_shape.json"
)
DEFAULT_CLASSIC_QT_PATH = Path(
    "data/research_cache/qualified_hold5_stop5_3y_jiaoch_classic.json"
)


class PathAProtocolSignalError(ValueError):
    """Raised when the protocol signal book cannot be built."""


def signal_masks(frame: pd.DataFrame) -> pd.DataFrame:
    """Causal MA20 reclaim masks on already-QFQ bars."""

    close = pd.to_numeric(frame["close"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce")
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    prior_high20 = high.shift(1).rolling(20, min_periods=20).max()
    history = ma60.notna()
    prior_close = close.shift(1)
    prior_ma20 = ma20.shift(1)
    reclaim = (
        history
        & (ma20 > ma60)
        & (prior_close < prior_ma20)
        & (close >= ma20)
    ).fillna(False)
    breakout = (history & (close > prior_high20)).fillna(False)
    pullback = (reclaim & (close <= prior_high20)).fillna(False)
    out = pd.DataFrame(index=frame.index)
    out["ma20"] = ma20
    out["ma60"] = ma60
    out["prior_high20"] = prior_high20
    out["reclaim"] = reclaim
    out["pullback"] = pullback
    out["breakout"] = breakout
    return out


def limit_threshold_pct(symbol: str) -> float:
    """Near-limit threshold: main 10% board, ChiNext 20% board, minus 0.5."""

    return 19.5 if str(symbol).startswith("30") else 9.5


def entry_exec_limits(symbol: str) -> tuple[float, float]:
    """max_gap_up, locked_limit_gap. ChiNext boards are 20%."""

    if str(symbol).startswith("30"):
        return 12.0, 19.3
    return 6.0, 9.3


def entry_masks(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Limit-follow, MA60 reclaim, and 60d breakout. Not the MA20 reclaim book."""

    close = pd.to_numeric(frame["close"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce")
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    prior_high60 = high.shift(1).rolling(60, min_periods=60).max()
    history = ma60.notna()
    prior_close = close.shift(1)
    prior_ma60 = ma60.shift(1)
    change_pct = (close / prior_close - 1.0) * 100.0
    threshold = limit_threshold_pct(symbol)
    limit_today = (change_pct >= threshold).fillna(False)
    limit_prior = limit_today.shift(1).fillna(False)
    limit_follow = (
        history
        & limit_prior
        & (~limit_today)
        & (close >= prior_close)
    ).fillna(False)
    ma60_reclaim = (
        history
        & (ma20 > ma60)
        & (prior_close < prior_ma60)
        & (close >= ma60)
    ).fillna(False)
    breakout_60d = (history & (close > prior_high60)).fillna(False)
    out = pd.DataFrame(index=frame.index)
    out["limit_follow"] = limit_follow
    out["ma60_reclaim"] = ma60_reclaim
    out["breakout_60d"] = breakout_60d
    out["fire"] = (limit_follow | ma60_reclaim | breakout_60d).fillna(False)
    return out


def bounce_masks(frame: pd.DataFrame) -> pd.DataFrame:
    """Down-day bounce, inside-bar break, compressed-range break. Not reclaim."""

    close = pd.to_numeric(frame["close"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    prior_high20 = high.shift(1).rolling(20, min_periods=20).max()
    history = ma60.notna()
    uptrend = history & (ma20 > ma60)
    not_breakout = (close <= prior_high20).fillna(False)
    delta = close.diff()
    today_up = (delta > 0).fillna(False)
    down1 = (delta.shift(1) < 0).fillna(False)
    down2 = (delta.shift(2) < 0).fillna(False)
    down3 = (delta.shift(3) < 0).fillna(False)
    dn2 = (uptrend & down1 & down2 & today_up & not_breakout).fillna(False)
    dn3 = (uptrend & down1 & down2 & down3 & today_up & not_breakout).fillna(False)
    inside_yest = (
        (high.shift(1) < high.shift(2)) & (low.shift(1) > low.shift(2))
    ).fillna(False)
    inside_up = (
        uptrend & inside_yest & (close > high.shift(1)) & not_breakout
    ).fillna(False)
    range5 = high.rolling(5, min_periods=5).max() - low.rolling(5, min_periods=5).min()
    range20 = (
        high.rolling(20, min_periods=20).max() - low.rolling(20, min_periods=20).min()
    )
    compressed = ((range20 > 0) & ((range5 / range20) < 0.5)).fillna(False)
    break5 = (close > high.shift(1).rolling(5, min_periods=5).max()).fillna(False)
    tight5_up = (uptrend & compressed & break5).fillna(False)
    out = pd.DataFrame(index=frame.index)
    out["dn2"] = dn2
    out["dn3"] = dn3
    out["inside_up"] = inside_up
    out["tight5_up"] = tight5_up
    out["fire"] = (dn2 | inside_up | tight5_up).fillna(False)
    return out


def shape_masks(frame: pd.DataFrame) -> pd.DataFrame:
    """Candlestick / faster-MA / 20d-low identities. Not reclaim or down2 bounce."""

    close = pd.to_numeric(frame["close"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    open_px = pd.to_numeric(frame["open"], errors="coerce")
    ma10 = close.rolling(10, min_periods=10).mean()
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=60).mean()
    prior_high20 = high.shift(1).rolling(20, min_periods=20).max()
    prior_low20 = low.shift(1).rolling(20, min_periods=20).min()
    history = ma60.notna()
    uptrend = history & (ma20 > ma60)
    not_breakout = (close <= prior_high20).fillna(False)
    prior_open = open_px.shift(1)
    prior_close = close.shift(1)
    engulf = (
        uptrend
        & (prior_close < prior_open)
        & (close > open_px)
        & (open_px <= prior_close)
        & (close > prior_open)
        & not_breakout
    ).fillna(False)
    body = (close - open_px).abs()
    lower_wick = pd.concat([open_px, close], axis=1).min(axis=1) - low
    upper_wick = high - pd.concat([open_px, close], axis=1).max(axis=1)
    day_range = (high - low).clip(lower=1e-6)
    hammer = (
        uptrend
        & (lower_wick >= (2.0 * body))
        & (lower_wick >= (0.5 * day_range))
        & (upper_wick <= (0.25 * day_range))
        & (close >= prior_close)
        & not_breakout
    ).fillna(False)
    ma10_reclaim = (
        history
        & (ma10 > ma20)
        & (ma20 > ma60)
        & (prior_close < ma10.shift(1))
        & (close >= ma10)
        & not_breakout
    ).fillna(False)
    lo20 = (
        history
        & (prior_close <= (prior_low20 * 1.02))
        & (close > prior_close)
        & not_breakout
    ).fillna(False)
    rng = high - low
    nr7 = rng < rng.shift(1).rolling(6, min_periods=6).min()
    nr7_up = (uptrend & nr7 & (close > high.shift(1)) & not_breakout).fillna(False)
    out = pd.DataFrame(index=frame.index)
    out["engulf"] = engulf
    out["hammer"] = hammer
    out["ma10_reclaim"] = ma10_reclaim
    out["lo20"] = lo20
    out["nr7_up"] = nr7_up
    out["fire"] = (engulf | hammer | ma10_reclaim | lo20 | nr7_up).fillna(False)
    return out


def classic_masks(frame: pd.DataFrame) -> pd.DataFrame:
    """Textbook MACD / KDJ / BOLL events. Not reclaim, bounce, or shape."""

    work = frame
    if "macd" not in frame.columns or "kdj_k" not in frame.columns:
        work = add_indicators(frame)
    close = pd.to_numeric(work["close"], errors="coerce")
    high = pd.to_numeric(work["high"], errors="coerce")
    ma20 = pd.to_numeric(work["ma20"], errors="coerce")
    ma60 = pd.to_numeric(work["ma60"], errors="coerce")
    macd = pd.to_numeric(work["macd"], errors="coerce")
    macd_signal = pd.to_numeric(work["macd_signal"], errors="coerce")
    kdj_k = pd.to_numeric(work["kdj_k"], errors="coerce")
    kdj_d = pd.to_numeric(work["kdj_d"], errors="coerce")
    kdj_j = pd.to_numeric(work["kdj_j"], errors="coerce")
    boll_lower = pd.to_numeric(work["boll_lower"], errors="coerce")
    prior_high20 = high.shift(1).rolling(20, min_periods=20).max()
    history = ma60.notna()
    uptrend = history & (ma20 > ma60)
    not_breakout = (close <= prior_high20).fillna(False)
    macd_cross = (
        uptrend
        & (macd.shift(1) <= macd_signal.shift(1))
        & (macd > macd_signal)
    ).fillna(False)
    kdj_cross = (
        history
        & (kdj_k.shift(1) <= kdj_d.shift(1))
        & (kdj_k > kdj_d)
        & ((kdj_j.shift(1) <= 20.0) | (kdj_k.shift(1) <= 20.0))
    ).fillna(False)
    boll_reclaim = (
        history
        & (close.shift(1) < boll_lower.shift(1))
        & (close >= boll_lower)
        & not_breakout
    ).fillna(False)
    out = pd.DataFrame(index=frame.index)
    out["macd_cross"] = macd_cross
    out["kdj_cross"] = kdj_cross
    out["boll_reclaim"] = boll_reclaim
    out["fire"] = (macd_cross | kdj_cross | boll_reclaim).fillna(False)
    return out


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def _bar_tags(row: pd.Series, masks: pd.Series, *, book: str = "reclaim") -> list[str]:
    tags = {"action_buy"}
    if book == "entry":
        if bool(masks.get("limit_follow")):
            tags.add(LIMIT_FOLLOW_TAG)
        if bool(masks.get("ma60_reclaim")):
            tags.add(MA60_RECLAIM_TAG)
            tags.add("ma_structure")
        if bool(masks.get("breakout_60d")):
            tags.add(BREAKOUT_60D_TAG)
    elif book == "bounce":
        tags.add("ma_structure")
        if bool(masks.get("dn2")):
            tags.add(DN2_BOUNCE_TAG)
        if bool(masks.get("dn3")):
            tags.add(DN3_BOUNCE_TAG)
        if bool(masks.get("inside_up")):
            tags.add(INSIDE_UP_TAG)
        if bool(masks.get("tight5_up")):
            tags.add(TIGHT5_UP_TAG)
    elif book == "shape":
        if bool(masks.get("engulf")):
            tags.add(ENGULF_TAG)
            tags.add("ma_structure")
        if bool(masks.get("hammer")):
            tags.add(HAMMER_TAG)
            tags.add("ma_structure")
        if bool(masks.get("ma10_reclaim")):
            tags.add(MA10_RECLAIM_TAG)
            tags.add("ma_structure")
        if bool(masks.get("lo20")):
            tags.add(LO20_BOUNCE_TAG)
        if bool(masks.get("nr7_up")):
            tags.add(NR7_UP_TAG)
            tags.add("ma_structure")
    elif book == "classic":
        if bool(masks.get("macd_cross")):
            tags.add(MACD_GOLD_TAG)
            tags.add("ma_structure")
        if bool(masks.get("kdj_cross")):
            tags.add(KDJ_OVERSOLD_TAG)
            tags.add("ma_structure")
        if bool(masks.get("boll_reclaim")):
            tags.add(BOLL_RECLAIM_TAG)
    else:
        tags.add(RECLAIM_TAG)
        tags.add("ma_structure")
        if bool(masks.get("pullback")):
            tags.add(PULLBACK_TAG)
        if bool(masks.get("breakout")):
            tags.add("breakout_20d")
    volume_ratio = _num(row.get("volume_ratio"))
    if volume_ratio is not None and volume_ratio >= 1.2:
        tags.add("volume_confirmed")
    return_20d = _num(row.get("return_20d"))
    if return_20d is not None:
        pct = return_20d * 100.0
        if 0 <= pct <= 12:
            tags.add("moderate_20d_momentum")
        elif pct > 12:
            tags.add("extended_20d_momentum")
        elif pct < 0:
            tags.add("weak_20d_momentum")
    vol = _num(row.get("volatility_20d"))
    if vol is not None:
        vol_pct = vol * 100.0
        if vol_pct <= 35:
            tags.add("controlled_volatility")
        elif vol_pct >= 45:
            tags.add("high_volatility")
    rsi = _num(row.get("rsi14"))
    if rsi is not None:
        if 45 <= rsi <= 70:
            tags.add("balanced_rsi")
        elif rsi > 75:
            tags.add("overbought_rsi")
        elif rsi < 40:
            tags.add("weak_rsi")
    return sorted(tags)


def _load_cache_frame(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    frame = pd.DataFrame(payload.get("records") or [])
    if frame.empty or "date" not in frame.columns:
        raise PathAProtocolSignalError(f"empty cache: {path}")
    frame["date"] = frame["date"].astype(str).str.slice(0, 10)
    frame = frame.sort_values("date").reset_index(drop=True)
    return add_indicators(frame)


def build_signal_trades(
    prepared: list[tuple[dict[str, Any], pd.DataFrame]],
    *,
    start_date: str = START_DATE,
    end_date: str = END_DATE,
    hold_days: int = HOLD_DAYS,
    hold_horizons: tuple[int, ...] | None = None,
    stop_loss_pct: float = STOP_LOSS_PCT,
    book: str = "reclaim",
) -> list[dict[str, Any]]:
    horizons = tuple(hold_horizons) if hold_horizons is not None else (int(hold_days),)
    max_hold = max(int(item) for item in horizons)
    symbol_frames = {
        str(item["symbol"]): {"frame": frame, "base": item}
        for item, frame in prepared
    }
    breadth_by_date = _historical_market_breadth(
        symbol_frames,
        start_date,
        max_hold,
        universe_source=None,
    )
    trades: list[dict[str, Any]] = []
    for item, frame in prepared:
        symbol = str(item["symbol"])
        if book == "entry":
            masks = entry_masks(frame, symbol)
            fire = masks["fire"]
            gap_up, locked_gap = entry_exec_limits(symbol)
        elif book == "bounce":
            masks = bounce_masks(frame)
            fire = masks["fire"]
            gap_up, locked_gap = 6.0, 9.3
        elif book == "shape":
            masks = shape_masks(frame)
            fire = masks["fire"]
            gap_up, locked_gap = 6.0, 9.3
        elif book == "classic":
            masks = classic_masks(frame)
            fire = masks["fire"]
            gap_up, locked_gap = 6.0, 9.3
        else:
            masks = signal_masks(frame)
            fire = masks["reclaim"]
            gap_up, locked_gap = 6.0, 9.3
        for index in fire[fire].index.tolist():
            signal_date = str(frame.at[index, "date"])[:10]
            if signal_date < start_date or signal_date > end_date:
                continue
            if int(index) + 1 >= len(frame):
                continue
            signal_bar = frame.iloc[int(index)]
            entry_bar = frame.iloc[int(index) + 1]
            executable = assess_entry_executability(
                signal_bar,
                entry_bar,
                max_gap_up_pct=gap_up,
                max_gap_down_pct=7.0,
                locked_limit_gap_pct=locked_gap,
                max_intraday_range_pct=8.0,
                decision_cutoff="next_open",
            )
            if executable.get("executable") is not True:
                continue
            amount = _num(signal_bar.get("amount"), 0.0) or 0.0
            if amount <= 0:
                close = _num(signal_bar.get("close"), 0.0) or 0.0
                volume = _num(signal_bar.get("volume"), 0.0) or 0.0
                amount = close * volume
            breadth = breadth_by_date.get(signal_date) or {}
            market = _stock_breadth_market_context(breadth)
            relative = _stock_relative_strength_context(
                signal_bar,
                _stock_market_returns(breadth),
            )
            base_tags = set(_bar_tags(signal_bar, masks.iloc[int(index)], book=book))
            base_tags.update(relative.get("tags") or [])
            base_tags.update(build_market_breadth_tags(breadth))
            base_tags.update(build_price_action_tags(executable))
            candidate = {
                "symbol": symbol,
                "name": item.get("name"),
                "amount": amount,
                "change_pct": _num(signal_bar.get("change_pct")),
            }
            base_tags.update(build_candidate_context_tags(candidate))
            for horizon in horizons:
                hold = int(horizon)
                if int(index) + 1 + hold >= len(frame):
                    continue
                realized = _realized_trade_from_future(
                    frame,
                    int(index) + 1,
                    int(index) + 1 + hold,
                    stop_loss_pct=stop_loss_pct,
                )
                tags = set(base_tags)
                tags.add(f"hold{hold}")
                trades.append(
                    {
                        **realized,
                        "symbol": symbol,
                        "name": item.get("name"),
                        "signal_date": signal_date,
                        "action": "BUY",
                        "score": 4.0,
                        "rank_score": round(float(amount), 4),
                        "candidate_amount": amount,
                        "market_level": market.get("level"),
                        "signal_tags": sorted(tags),
                        "relative_strength": {
                            key: value
                            for key, value in relative.items()
                            if key != "tags"
                        },
                        "entry_executability": executable,
                    }
                )
    trades.sort(
        key=lambda row: (
            str(row["signal_date"]),
            str(row["symbol"]),
            str(row.get("exit_date") or ""),
        )
    )
    return trades


def build_path_a_protocol_signal_qt(
    *,
    repo_root: Path | None = None,
    cache_dir: Path | None = None,
    max_symbols: int = 0,
    require_local_research: bool = True,
    progress_every: int = 100,
    hold_horizons: tuple[int, ...] = (5,),
    signal_family: str = SIGNAL_FAMILY,
    book: str = "reclaim",
    start_date: str = START_DATE,
    end_date: str = END_DATE,
    slice_name: str = "holdout",
) -> dict[str, Any]:
    if require_local_research:
        role = os.getenv("VPS_RUNTIME_ROLE", "")
        if role.strip().casefold() != "local_research":
            raise PathAProtocolSignalError(
                f"requires VPS_RUNTIME_ROLE=local_research (got {role!r})"
            )
    from scripts.run_path_a_3y_clean_replay import (
        cache_ok,
        cache_path,
        load_eligible_universe,
        load_traded_symbols,
        select_targets,
    )

    root = (repo_root or Path.cwd()).resolve()
    cache = (cache_dir or (root / DEFAULT_CACHE_DIR)).resolve()
    eligible = load_eligible_universe(root)
    traded = load_traded_symbols(root)
    targets = select_targets(
        eligible,
        slice_name="holdout",
        max_symbols=max_symbols,
        traded=traded,
    )
    prepared: list[tuple[dict[str, Any], pd.DataFrame]] = []
    skipped = 0
    for index, item in enumerate(targets, 1):
        path = cache_path(cache, "a", item["symbol"])
        if cache_ok(path) is None:
            skipped += 1
            continue
        prepared.append((item, _load_cache_frame(path)))
        if progress_every and index % progress_every == 0:
            print(
                f"loaded {len(prepared)}/{index} cache_ok skipped={skipped}",
                flush=True,
            )
    if not prepared:
        raise PathAProtocolSignalError("no Jiaoch v2 cache_ok holdout names")
    print(f"building trades names={len(prepared)} skipped={skipped}", flush=True)
    trades = build_signal_trades(
        prepared,
        start_date=start_date,
        end_date=end_date,
        hold_horizons=hold_horizons,
        book=book,
    )
    family = str(signal_family)
    if family == SIGNAL_HOLD_FAMILY:
        stage = SIGNAL_HOLD_STAGE_GOAL_ID
    elif family == SIGNAL_ENTRY_FAMILY:
        stage = SIGNAL_ENTRY_STAGE_GOAL_ID
    elif family == SIGNAL_BOUNCE_FAMILY:
        stage = SIGNAL_BOUNCE_STAGE_GOAL_ID
    elif family == SIGNAL_SHAPE_FAMILY:
        stage = SIGNAL_SHAPE_STAGE_GOAL_ID
    elif family == SIGNAL_CLASSIC_FAMILY:
        stage = SIGNAL_CLASSIC_STAGE_GOAL_ID
    else:
        stage = STAGE_GOAL_ID
    payload = {
        "qualified_trades": trades,
        "summary": {
            "path_a_3y_clean_replay": {
                "stage": stage,
                "slice": slice_name,
                "development_only": True,
                "promotable": False,
                "source_policy": DATA_SOURCE_POLICY,
                "source_version": JIAOCH_DAILY_CACHE_SOURCE_VERSION,
                "signal_family": family,
                "window": {"start": start_date, "end": end_date},
                "hold_days": HOLD_DAYS,
                "hold_horizons": list(hold_horizons),
                "stop_loss_pct": STOP_LOSS_PCT,
                "name_count": len(prepared),
                "skipped_missing_cache": skipped,
                "raw_trade_count": len(trades),
            }
        },
    }
    return payload


def write_path_a_protocol_signal_qt(
    payload: dict[str, Any],
    *,
    qt_path: Path,
) -> Path:
    qt_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(str(qt_path), payload)
    return qt_path
