from collections import defaultdict
from functools import wraps
import inspect
import json
import math
import sys
from typing import Any, Dict, List, Optional

import pandas as pd

import app.research_artifact_replay as research_artifact_replay_module
import app.research_pit_store as research_pit_store_module
from app.announcement_context import build_announcement_context
from app.research_artifact_replay import ArtifactNativeReplayAdapter
from app.a_share_universe import (
    AShareUniverseProvider,
    _is_excluded_name,
    select_deep_scan_candidates,
)
from app.config import Settings
from app.dragon_tiger import DragonTigerProvider, OFFICIAL_SOURCE_CAVEAT as DRAGON_TIGER_CAVEAT
from app.execution import assess_entry_executability
from app.indicators import add_indicators
from app.margin_eligibility import MarginEligibilityProvider
from app.market_data import AkshareDataProvider
from app.research_cache import (
    _announcements_with_file_cache,
    _history_with_file_cache,
)
from app.research_composite_universe import (
    CompositeAuditedUniverse,
    load_composite_universe_descriptor,
)
from app.research_common import _date_value, _date_yyyymmdd, _num
from app.research_context import (
    _historical_market_breadth,
    _historical_market_context,
    _historical_proxy_returns,
    _historical_industry_rotation_contexts,
    _price_action_context,
    _quality_from_prior,
    _relative_strength_context,
    _stock_breadth_market_context,
    _stock_market_returns,
    _stock_relative_strength_context,
    _stock_universe_equal_weight_benchmark,
    STOCK_MARKET_CONTEXT_SCHEMA_VERSION,
)
from app.research_equity import (
    _equity_points_from_basket_returns,
    _max_drawdown_pct,
)
from app.research_portfolio import (
    _realized_trade_from_future,
    _select_with_portfolio_controls,
    _window_portfolio_stats,
)
from app.research_pit import PointInTimeUniverse
from app.research_partitions import assert_range_allowed, load_temporal_partition_contract
from app.research_pit_store import AuditedPointInTimeUniverse
from app.research_stats import (
    _add_counts,
    _announcement_group_stats,
    _research_group_stats,
    _signal_tags,
    _split_events,
)
from app.signal_tags import build_candidate_context_tags
from app.signals import REQUIRED_COLUMNS, evaluate_signal
from app.strategy_signal_evidence import build_signal_snapshot
from app.storage import read_json


MARKET_PROXY_SYMBOLS = [
    {"symbol": "510300", "market": "etf", "name": "沪深300ETF"},
    {"symbol": "159915", "market": "etf", "name": "创业板ETF"},
]

_ARTIFACT_LINEAR_PRICE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "ma5",
    "ma10",
    "ma20",
    "ma60",
    "ma120",
    "ema12",
    "ema26",
    "macd",
    "macd_signal",
    "macd_hist",
    "boll_upper",
    "boll_lower",
    "tr",
    "atr14",
    "high20_prev",
    "low20_prev",
)


def _artifact_causal_indicator_prefix(
    analysis_frame: pd.DataFrame, signal_index: int
) -> pd.DataFrame:
    """Rescale an enriched artifact prefix to its signal-date qfq basis.

    All price-like values in a qfq frame differ from another causal as-of only
    by one positive constant.  ``raw_close / close`` on the signal row cancels
    the later analysis-end denominator, while ratio/volume indicators remain
    unchanged.  This preserves the exact signal decision without re-querying
    and recomputing the artifact for every historical date.
    """

    if signal_index < 0 or signal_index >= len(analysis_frame):
        raise ValueError("artifact signal index is outside the analysis frame")
    prefix = analysis_frame.iloc[: signal_index + 1].copy()
    latest = prefix.iloc[-1]
    try:
        raw_close = float(latest["raw_close"])
        normalized_close = float(latest["close"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("artifact signal scale inputs are missing") from exc
    scale = raw_close / normalized_close if normalized_close else float("nan")
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("artifact signal scale is not positive and finite")
    for column in _ARTIFACT_LINEAR_PRICE_COLUMNS:
        if column in prefix.columns:
            prefix[column] = prefix[column] * scale
    return prefix


def _artifact_causal_signal_window(
    analysis_frame: pd.DataFrame, signal_index: int
) -> pd.DataFrame:
    """Return the two causal rows consumed by ``evaluate_signal``.

    Artifact indicators are calculated once over the complete analysis frame.
    Recomputing them on this two-row window would change their rolling meaning,
    so missing inputs fail closed instead of falling through to the evaluator's
    general-purpose indicator enrichment.
    """

    if signal_index < 1 or signal_index >= len(analysis_frame):
        raise ValueError("artifact signal index is outside the analysis frame")
    missing = [
        column for column in REQUIRED_COLUMNS if column not in analysis_frame.columns
    ]
    if missing:
        raise ValueError(
            "artifact analysis frame is missing precomputed signal indicators: "
            + ", ".join(missing)
        )
    window = analysis_frame.iloc[signal_index - 1 : signal_index + 1].copy()
    latest = window.iloc[-1]
    try:
        raw_close = float(latest["raw_close"])
        normalized_close = float(latest["close"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("artifact signal scale inputs are missing") from exc
    scale = raw_close / normalized_close if normalized_close else float("nan")
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("artifact signal scale is not positive and finite")
    linear_columns = [
        column for column in _ARTIFACT_LINEAR_PRICE_COLUMNS if column in window.columns
    ]
    window[linear_columns] = window[linear_columns] * scale
    return window


def _strategy_signal_snapshot(signal: Dict[str, Any]) -> Dict[str, Any] | None:
    """Persist a replay claim when the evaluator returned the full contract.

    A few research callers intentionally monkeypatch a minimal signal object
    for control-flow tests.  Such a row remains usable for development output,
    but its missing snapshot must later block strict evidence rather than abort
    the entire backtest.
    """

    try:
        return build_signal_snapshot(signal)
    except (KeyError, TypeError, ValueError):
        return None


def _require_positive_hold_days(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("hold_days must be a positive integer")
    return value


def _emit_progress(payload: Dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr, flush=True)


def _load_snapshot(settings: Settings, use_live_snapshot: bool) -> List[Dict[str, Any]]:
    if use_live_snapshot:
        return AShareUniverseProvider(settings.universe_cache_path).snapshot(
            use_cache_on_error=True
        )
    payload = read_json(settings.universe_cache_path, {"items": []})
    return payload.get("items", []) if isinstance(payload, dict) else []


def _benchmark_return(
    provider: AkshareDataProvider,
    symbol: str,
    start_date: str,
    lookback_days: int,
    end_date: str = None,
) -> float:
    frame, _source = provider.history(symbol, "etf", lookback_days=lookback_days, adjust="qfq")
    frame = frame[frame["date"] >= start_date]
    if end_date:
        frame = frame[frame["date"] <= end_date]
    if len(frame) < 2:
        return 0.0
    return (float(frame.iloc[-1]["close"]) / float(frame.iloc[0]["open"]) - 1) * 100


def _compact_announcement_context(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "level": payload.get("level", "neutral"),
        "score": payload.get("score", 0),
        "score_adjustment": payload.get("score_adjustment", 0),
        "allow_recommendation": payload.get("allow_recommendation", True),
        "announcement_count": payload.get("announcement_count", 0),
        "negative_count": payload.get("negative_count", 0),
        "positive_count": payload.get("positive_count", 0),
        "event_counts": payload.get("event_counts", {}),
        "announcements": payload.get("announcements", [])[:3],
        "errors": payload.get("errors", []),
    }


def _historical_snapshot_item(
    base: Dict[str, Any], frame: pd.DataFrame, index: int
) -> Dict[str, Any]:
    row = frame.iloc[index]
    previous = frame.iloc[index - 1] if index > 0 else row
    # artifact-native signal OHLC may be normalized to a later as-of factor.
    # The signal day's own tradable quote and absolute price filter must use
    # the frozen raw close, while legacy frames continue to use close.
    latest = _num(row.get("raw_close", row.get("close")))
    amount = _num(row.get("amount"))
    if amount <= 0:
        amount = latest * _num(row.get("volume"))
    previous_close = _num(previous.get("raw_close", previous.get("close")))
    change_pct = _num(row.get("change_pct"))
    if not change_pct and previous_close > 0:
        change_pct = (latest / previous_close - 1) * 100
    return {
        "symbol": base.get("symbol"),
        "market": base.get("market", "a"),
        "name": base.get("name"),
        "latest": latest,
        "amount": amount,
        "change_pct": change_pct,
        "volume": _num(row.get("volume")),
        "date": str(row.get("date")),
    }


def _artifact_open_verdict(
    adapter: ArtifactNativeReplayAdapter,
    cache: Dict[tuple[str, str, str], Dict[str, Any]],
    symbol: str,
    trade_date: str,
    side: str,
) -> Dict[str, Any]:
    key = (str(symbol), str(trade_date), str(side))
    if key not in cache:
        verdict = adapter.next_open(str(symbol), str(trade_date), str(side))
        if not isinstance(verdict, dict) or type(verdict.get("fillable")) is not bool:
            raise ValueError("artifact next-open evidence has an invalid fillable verdict")
        proof = verdict.get("generation_proof")
        if not isinstance(proof, dict) or proof.get("trade_date") != str(trade_date):
            raise ValueError("artifact next-open generation proof does not match trade date")
        if verdict["fillable"]:
            raw_price = _num(verdict.get("raw_price"), default=-1)
            if raw_price <= 0:
                raise ValueError("artifact next-open fill has no positive raw price")
        elif verdict.get("raw_price") is not None:
            raise ValueError("artifact rejected next-open evidence carries a raw price")
        cache[key] = dict(verdict)
    return dict(cache[key])


def _artifact_native_time_exit_trade(
    *,
    adapter: ArtifactNativeReplayAdapter,
    verdict_cache: Dict[tuple[str, str, str], Dict[str, Any]],
    symbol: str,
    signal_date: str,
    sessions: List[str],
    session_positions: Dict[str, int],
    artifact_start_date: str,
    hold_days: int,
    settings: Settings,
    analysis_frame: Optional[pd.DataFrame] = None,
    analysis_date_positions: Optional[Dict[str, int]] = None,
) -> Optional[tuple[Dict[str, Any], Dict[str, Any]]]:
    """Build one exact raw-open entry and causal-total-return time exit.

    The market calendar chooses entry/exit sessions; the artifact gate proves
    raw-open fillability. A blocked planned sell is retried on each later open
    session. Intraday stop/take/trailing models are intentionally outside this
    first strict slice because OHLC alone cannot prove their execution price.
    """

    signal_position = session_positions.get(str(signal_date))
    if signal_position is None:
        raise ValueError(f"signal date is absent from audited open sessions: {signal_date}")
    entry_position = signal_position + 1
    planned_exit_position = entry_position + int(hold_days)
    if entry_position >= len(sessions) or planned_exit_position >= len(sessions):
        return None  # right-censored by artifact coverage; do not fabricate an outcome

    entry_date = sessions[entry_position]
    buy = _artifact_open_verdict(
        adapter, verdict_cache, str(symbol), entry_date, "buy"
    )
    if not buy["fillable"]:
        return None

    def causal_frame(as_of_date: str) -> pd.DataFrame:
        if analysis_frame is None:
            return adapter.signal_frame(str(symbol), artifact_start_date, as_of_date)
        positions = analysis_date_positions or {
            str(value): position
            for position, value in enumerate(analysis_frame["date"].tolist())
        }
        as_of_position = positions.get(str(as_of_date))
        if as_of_position is None:
            raise ValueError(f"artifact analysis frame is missing as-of date: {as_of_date}")
        return _artifact_causal_indicator_prefix(analysis_frame, as_of_position)

    entry_raw_price = float(buy["raw_price"])
    entry_context_frame = causal_frame(entry_date)
    if entry_context_frame.empty or list(entry_context_frame["date"]) != sorted(
        set(entry_context_frame["date"])
    ):
        raise ValueError("artifact entry frame is empty, duplicated, or unsorted")
    entry_context = entry_context_frame.set_index("date", drop=False)
    if signal_date not in entry_context.index or entry_date not in entry_context.index:
        raise ValueError("artifact entry frame is missing signal or entry session")
    signal_context_row = entry_context.loc[signal_date]
    entry_context_row = entry_context.loc[entry_date]
    if abs(float(entry_context_row["raw_open"]) - entry_raw_price) > max(
        1.0, abs(entry_raw_price)
    ) * 1e-9:
        raise ValueError("artifact entry raw price disagrees with entry frame")

    # The strategy gap filter must compare prices on the same entry-date
    # causal adjustment basis. Raw close/open are reserved for execution and
    # limit gates; comparing raw prices across a split/ex-right boundary would
    # invent a gap and incorrectly discard a fillable trade.
    strategy_filter = assess_entry_executability(
        {"close": float(signal_context_row["close"])},
        {"open": float(entry_context_row["open"])},
        max_gap_up_pct=settings.max_entry_gap_up_pct,
        max_gap_down_pct=settings.max_entry_gap_down_pct,
        locked_limit_gap_pct=settings.locked_limit_gap_pct,
        max_intraday_range_pct=settings.max_entry_intraday_range_pct,
        decision_cutoff="next_open",
    )
    if not strategy_filter["executable"]:
        return None

    exit_verdict: Optional[Dict[str, Any]] = None
    exit_position: Optional[int] = None
    for candidate_position in range(planned_exit_position, len(sessions)):
        candidate_date = sessions[candidate_position]
        candidate = _artifact_open_verdict(
            adapter, verdict_cache, str(symbol), candidate_date, "sell"
        )
        if candidate["fillable"]:
            exit_verdict = candidate
            exit_position = candidate_position
            break
    if exit_verdict is None or exit_position is None:
        raise ValueError(
            f"artifact-native exit remains unfillable through coverage end: {symbol} "
            f"from {sessions[planned_exit_position]}"
        )

    exit_date = sessions[exit_position]
    outcome_frame = causal_frame(exit_date)
    if outcome_frame.empty or list(outcome_frame["date"]) != sorted(
        set(outcome_frame["date"])
    ):
        raise ValueError("artifact outcome frame is empty, duplicated, or unsorted")
    outcome_by_date = outcome_frame.set_index("date", drop=False)
    if entry_date not in outcome_by_date.index or exit_date not in outcome_by_date.index:
        raise ValueError("artifact outcome frame is missing an executed session")
    entry_row = outcome_by_date.loc[entry_date]
    exit_row = outcome_by_date.loc[exit_date]
    if abs(float(entry_row["raw_open"]) - entry_raw_price) > max(
        1.0, abs(entry_raw_price)
    ) * 1e-9:
        raise ValueError("artifact entry raw price disagrees with signal frame")
    exit_raw_price = float(exit_verdict["raw_price"])
    if abs(float(exit_row["raw_open"]) - exit_raw_price) > max(
        1.0, abs(exit_raw_price)
    ) * 1e-9:
        raise ValueError("artifact exit raw price disagrees with signal frame")

    entry_total_return_price = float(entry_row["open"])
    exit_total_return_price = float(exit_row["open"])
    if entry_total_return_price <= 0 or exit_total_return_price <= 0:
        raise ValueError("artifact outcome frame has a nonpositive signal open")

    held = outcome_frame[
        (outcome_frame["date"] >= entry_date) & (outcome_frame["date"] <= exit_date)
    ].copy()
    mark_to_market_path: List[Dict[str, Any]] = []
    adverse = 0.0
    favorable = 0.0
    for _index, row in held.iterrows():
        is_exit = str(row["date"]) == exit_date
        open_price = exit_total_return_price if is_exit else float(row["open"])
        high_price = open_price if is_exit else float(row["high"])
        low_price = open_price if is_exit else float(row["low"])
        close_price = open_price if is_exit else float(row["close"])
        marks = {
            "date": str(row["date"]),
            "open_return_pct": round(
                (open_price / entry_total_return_price - 1) * 100, 4
            ),
            "high_return_pct": round(
                (high_price / entry_total_return_price - 1) * 100, 4
            ),
            "close_return_pct": round(
                (close_price / entry_total_return_price - 1) * 100, 4
            ),
            "low_return_pct": round(
                (low_price / entry_total_return_price - 1) * 100, 4
            ),
        }
        mark_to_market_path.append(marks)
        adverse = min(adverse, marks["low_return_pct"])
        favorable = max(favorable, marks["high_return_pct"])

    realized = {
        "entry_date": entry_date,
        "exit_date": exit_date,
        "planned_exit_date": sessions[planned_exit_position],
        "exit_reason": "time_exit_next_open",
        "holding_days": max(1, exit_position - entry_position),
        "mark_to_market_path": mark_to_market_path,
        "return_pct": round(
            (exit_total_return_price / entry_total_return_price - 1) * 100, 4
        ),
        "max_adverse_pct": round(adverse, 4),
        "max_favorable_pct": round(favorable, 4),
        "entry_raw_price": entry_raw_price,
        "exit_raw_price": exit_raw_price,
        "exit_execution_evidence": exit_verdict,
        "price_basis": "raw_unadjusted_execution",
        "return_price_basis": "causal_total_return_open_to_open",
    }
    entry_evidence = {
        **strategy_filter,
        "evidence_source": "audited_artifact_next_open",
        "fillable": True,
        "reason": buy["reason"],
        "raw_price": entry_raw_price,
        "generation_proof": buy["generation_proof"],
    }
    return realized, entry_evidence


def _load_research_universe_items(
    settings: Settings,
    use_live_snapshot: bool,
    max_universe_symbols: int = 300,
) -> List[Dict[str, Any]]:
    snapshot = _load_snapshot(settings, use_live_snapshot)
    items = []
    seen = set()
    for item in snapshot:
        symbol = str(item.get("symbol") or "").strip()
        name = str(item.get("name") or "").strip()
        if len(symbol) != 6 or not symbol.isdigit() or not name or symbol in seen:
            continue
        seen.add(symbol)
        items.append(dict(item))
    items.sort(key=lambda row: _num(row.get("amount")), reverse=True)
    if max_universe_symbols and max_universe_symbols > 0:
        return items[:max_universe_symbols]
    return items


def _resolve_historical_universe(
    settings: Settings,
    use_live_snapshot: bool,
    max_universe_symbols: int,
    start_date: str,
    end_date: str = None,
    pit_universe_path: str = None,
    audited_pit_universe_path: str = None,
    composite_pit_descriptor_path: str = None,
    expected_coverage_audit_sha256: str = None,
    expected_artifact_root_sha256: str = None,
    expected_composite_root_sha256: str = None,
    expected_temporal_contract_sha256: str = None,
    expected_temporal_role: str = None,
) -> tuple[List[Dict[str, Any]], Any]:
    if sum(
        bool(value)
        for value in (
            pit_universe_path,
            audited_pit_universe_path,
            composite_pit_descriptor_path,
        )
    ) > 1:
        raise ValueError(
            "only one of audited PIT, composite PIT, and legacy PIT paths may be used"
        )
    if expected_coverage_audit_sha256 and not audited_pit_universe_path:
        raise ValueError("expected coverage audit hash requires an audited PIT universe path")
    if audited_pit_universe_path and not expected_coverage_audit_sha256:
        raise ValueError("audited PIT universe requires expected coverage audit hash")
    if expected_composite_root_sha256 and not composite_pit_descriptor_path:
        raise ValueError("expected composite root requires a composite descriptor path")
    if composite_pit_descriptor_path and not expected_composite_root_sha256:
        raise ValueError("composite PIT universe requires expected composite root")
    artifact_path = (
        composite_pit_descriptor_path
        or audited_pit_universe_path
        or pit_universe_path
    )
    if not artifact_path:
        return (
            _load_research_universe_items(
                settings,
                use_live_snapshot=use_live_snapshot,
                max_universe_symbols=max_universe_symbols,
            ),
            None,
        )
    if use_live_snapshot:
        raise ValueError("PIT universe mode forbids live/current snapshot fallback")
    if max_universe_symbols:
        raise ValueError("PIT universe mode requires max_universe_symbols=0")
    if composite_pit_descriptor_path:
        universe = load_composite_universe_descriptor(
            composite_pit_descriptor_path,
            expected_composite_root_sha256=expected_composite_root_sha256,
        )
        if (
            universe.temporal_contract_sha256
            != expected_temporal_contract_sha256
            or universe.temporal_role != expected_temporal_role
        ):
            universe.close()
            raise ValueError("composite temporal authority mismatch")
    elif audited_pit_universe_path:
        loader_kwargs = {
            "expected_coverage_audit_sha256": expected_coverage_audit_sha256,
        }
        if expected_artifact_root_sha256 is not None:
            loader_kwargs.update(
                expected_artifact_root_sha256=expected_artifact_root_sha256,
                expected_temporal_contract_sha256=expected_temporal_contract_sha256,
                expected_temporal_role=expected_temporal_role,
            )
        universe = AuditedPointInTimeUniverse.from_file(
            audited_pit_universe_path, **loader_kwargs
        )
    else:
        universe = PointInTimeUniverse.from_file(pit_universe_path)
    try:
        items = universe.seed_items(start_date, end_date or universe.end_date)
    except Exception:
        if (
            audited_pit_universe_path or composite_pit_descriptor_path
        ) and hasattr(universe, "close"):
            universe.close()
        raise
    return items, universe


def _pit_historical_member(
    universe_source: Any, symbol: str, signal_date: str
) -> Optional[Dict[str, Any]]:
    if universe_source is None or signal_date < str(
        getattr(universe_source, "start_date", "")
    ):
        return None
    try:
        item = universe_source.item_as_of(str(symbol), signal_date)
    except ValueError:
        return None
    if item is None or _is_excluded_name(str(item.get("name") or "")):
        return None
    return item


def _batch_pit_eligible_dates_by_symbol(
    universe_source: Any, signal_dates: set[str]
) -> Optional[Dict[str, set[str]]]:
    items_as_of = getattr(universe_source, "items_as_of", None)
    if universe_source is None or not callable(items_as_of):
        return None
    eligible: Dict[str, set[str]] = defaultdict(set)
    universe_start = str(getattr(universe_source, "start_date", ""))
    for signal_date in sorted(signal_dates):
        if signal_date < universe_start:
            continue
        try:
            items = items_as_of(signal_date)
        except ValueError:
            if getattr(universe_source, "is_audited_store_artifact", False):
                raise
            continue
        for item in items:
            if _is_excluded_name(str(item.get("name") or "")):
                continue
            symbol = str(item.get("symbol") or "")
            if symbol:
                eligible[symbol].add(signal_date)
    return dict(eligible)


def _build_historical_candidate_maps(
    symbol_frames: Dict[str, Dict[str, Any]],
    start_date: str,
    hold_days: int,
    max_deep: int,
    settings: Settings,
    universe_source: Any = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    frame_refs_by_date: Dict[str, List[tuple[Dict[str, Any], pd.DataFrame, int]]] = (
        defaultdict(list)
    )
    for payload in symbol_frames.values():
        frame = payload["frame"]
        base = payload["base"]
        for index in range(1, len(frame)):
            signal_date = str(frame.iloc[index]["date"])
            if signal_date < start_date:
                continue
            frame_refs_by_date[signal_date].append((base, frame, index))

    candidate_maps: Dict[str, Dict[str, Dict[str, Any]]] = {}
    batch_items_as_of = getattr(universe_source, "items_as_of", None)
    for signal_date, frame_refs in frame_refs_by_date.items():
        historical_by_symbol = None
        if universe_source is not None and callable(batch_items_as_of):
            if signal_date < str(getattr(universe_source, "start_date", "")):
                historical_items = []
            else:
                try:
                    historical_items = batch_items_as_of(signal_date)
                except ValueError:
                    if getattr(universe_source, "is_audited_store_artifact", False):
                        raise
                    historical_items = []
            historical_by_symbol = {
                str(item.get("symbol") or ""): item
                for item in historical_items
                if not _is_excluded_name(str(item.get("name") or ""))
            }

        snapshot = []
        for base, frame, index in frame_refs:
            historical_base = base
            if historical_by_symbol is not None:
                historical_base = historical_by_symbol.get(
                    str(base.get("symbol") or "")
                )
            elif universe_source is not None:
                historical_base = _pit_historical_member(
                    universe_source,
                    str(base.get("symbol") or ""),
                    signal_date,
                )
            if historical_base is None:
                continue
            snapshot.append(_historical_snapshot_item(historical_base, frame, index))

        if not snapshot:
            continue
        candidates = select_deep_scan_candidates(
            snapshot=snapshot,
            max_deep=max_deep,
            min_amount=settings.scan_min_amount,
            min_price=settings.scan_min_price,
            max_price=settings.scan_max_price,
        )
        candidate_maps[signal_date] = {
            str(candidate.get("symbol")): {**candidate, "candidate_rank": position}
            for position, candidate in enumerate(candidates, 1)
        }
    return candidate_maps


def _research_payload_from_trades(
    selected: List[Dict[str, Any]],
    all_trades: List[Dict[str, Any]],
    errors: List[Dict[str, Any]],
    settings: Settings,
    provider: AkshareDataProvider,
    start_date: str,
    max_deep: int,
    top_n: int,
    hold_days: int,
    lookback_days: int,
    buy_only: bool,
    min_score: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    trailing_stop_pct: float,
    symbol_cooldown_days: int,
    max_active_positions: int,
    required_events: set,
    require_all_announcement_events: bool,
    excluded_events: set,
    required_market_levels: set,
    required_signal_tags: set,
    require_all_signal_tags: bool,
    excluded_signal_tags: set,
    min_prior_win_rate: float,
    min_prior_avg_return: float,
    max_prior_avg_adverse: float,
    use_announcement_context: bool,
    announcement_lookback_days: int,
    announcement_blocked_count: int,
    announcement_scored_count: int,
    announcement_positive_count: int,
    announcement_watch_risk_count: int,
    announcement_fetch_errors: int,
    announcement_blocked_by_event: Dict[str, int],
    candidate_count: int,
    fetched_symbols: int,
    include_qualified_trades: bool,
    extra_summary: Dict[str, Any] = None,
    analysis_end_date: str = None,
    market_benchmark_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    selected_by_signal_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in selected:
        selected_by_signal_date[item["signal_date"]].append(item)

    basket_returns = []
    for signal_date, top_trades in selected_by_signal_date.items():
        if top_trades:
            basket_returns.append(
                {
                    "signal_date": signal_date,
                    "return_pct": sum(item["return_pct"] for item in top_trades) / len(top_trades),
                    "count": len(top_trades),
                }
            )
    basket_returns.sort(key=lambda item: item["signal_date"])

    wins = [item for item in selected if item["return_pct"] > 0]
    adverse_items = [item for item in selected if item.get("max_adverse_pct") is not None]
    equity_points = _equity_points_from_basket_returns(basket_returns, hold_days)
    compound = equity_points[-1]["equity"] if equity_points else 1.0
    equity_curve = [1.0] + [point["equity"] for point in equity_points]
    rolling_1y = _window_portfolio_stats(equity_points, days=365)

    returns = [item["return_pct"] for item in selected]
    median_return = float(pd.Series(returns).median()) if returns else 0.0
    summary = {
        "start_date": start_date,
        "end_date": max([item["exit_date"] for item in selected], default=None),
        "candidate_count": candidate_count,
        "fetched_symbols": fetched_symbols,
        "error_count": len(errors),
        "raw_qualified_trade_count": len(all_trades),
        "selected_trade_count": len(selected),
        "signal_days": len(basket_returns),
        "top_n": top_n,
        "hold_days": hold_days,
        "buy_only": buy_only,
        "min_score": min_score,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "trailing_stop_pct": trailing_stop_pct,
        "symbol_cooldown_days": symbol_cooldown_days,
        "max_active_positions": max_active_positions,
        "announcement_context_enabled": use_announcement_context,
        "announcement_lookback_days": announcement_lookback_days
        if use_announcement_context
        else None,
        "required_announcement_events": sorted(required_events),
        "require_all_announcement_events": bool(require_all_announcement_events),
        "excluded_announcement_events": sorted(excluded_events),
        "required_market_levels": sorted(required_market_levels),
        "required_signal_tags": sorted(required_signal_tags),
        "require_all_signal_tags": bool(require_all_signal_tags),
        "excluded_signal_tags": sorted(excluded_signal_tags),
        "min_prior_win_rate": min_prior_win_rate,
        "min_prior_avg_return": min_prior_avg_return,
        "max_prior_avg_adverse": max_prior_avg_adverse,
        "announcement_blocked_count": announcement_blocked_count,
        "announcement_scored_count": announcement_scored_count,
        "announcement_positive_count": announcement_positive_count,
        "announcement_watch_risk_count": announcement_watch_risk_count,
        "announcement_fetch_errors": announcement_fetch_errors,
        "announcement_blocked_by_event": dict(sorted(announcement_blocked_by_event.items())),
        "trade_win_rate_pct": round(len(wins) / len(selected) * 100, 2) if selected else None,
        "trade_avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
        "trade_median_return_pct": round(median_return, 2) if returns else None,
        "trade_avg_max_adverse_pct": round(
            sum(item["max_adverse_pct"] for item in adverse_items) / len(adverse_items), 2
        )
        if adverse_items
        else None,
        "basket_win_rate_pct": round(
            len([item for item in basket_returns if item["return_pct"] > 0])
            / len(basket_returns)
            * 100,
            2,
        )
        if basket_returns
        else None,
        "basket_avg_return_pct": round(
            sum(item["return_pct"] for item in basket_returns) / len(basket_returns),
            2,
        )
        if basket_returns
        else None,
        "portfolio_compounded_return_pct": round((compound - 1) * 100, 2)
        if basket_returns
        else None,
        "portfolio_max_drawdown_pct": _max_drawdown_pct(equity_curve) if basket_returns else None,
        "rolling_1y": rolling_1y,
    }
    # historical (stock-only) 路径传入 market_benchmark_summary → 完全跳过 ETF
    # _benchmark_return，summary 不含 hs300etf/cybetf key；candidate/live 旧调用保持
    # 默认 None → 走 ETF buy-hold 旧行为。
    if market_benchmark_summary is not None:
        summary.update(market_benchmark_summary)
    else:
        summary["hs300etf_buy_hold_pct"] = round(
            _benchmark_return(
                provider, "510300", start_date, lookback_days, end_date=analysis_end_date
            ),
            2,
        )
        summary["cybetf_buy_hold_pct"] = round(
            _benchmark_return(
                provider, "159915", start_date, lookback_days, end_date=analysis_end_date
            ),
            2,
        )
    if extra_summary:
        summary.update(extra_summary)
    return {
        "summary": summary,
        "announcement_group_stats": _announcement_group_stats(selected)
        if use_announcement_context
        else {},
        "research_group_stats": _research_group_stats(selected),
        "qualified_trades": all_trades if include_qualified_trades else [],
        "best": sorted(selected, key=lambda item: item["return_pct"], reverse=True)[:5],
        "worst": sorted(selected, key=lambda item: item["return_pct"])[:5],
        "errors": errors[:20],
    }


def run_candidate_research_backtest(
    settings: Settings,
    provider: AkshareDataProvider,
    start_date: str,
    max_deep: int,
    top_n: int,
    hold_days: int,
    lookback_days: int,
    use_live_snapshot: bool = False,
    cache_dir: str = "data/research_cache",
    progress_every: int = 0,
    buy_only: bool = False,
    min_score: float = None,
    stop_loss_pct: float = None,
    take_profit_pct: float = None,
    trailing_stop_pct: float = None,
    symbol_cooldown_days: int = 0,
    max_active_positions: int = 0,
    use_announcement_context: bool = False,
    announcement_lookback_days: int = None,
    require_announcement_events: Any = None,
    require_all_announcement_events: bool = False,
    exclude_announcement_events: Any = None,
    require_market_levels: Any = None,
    require_signal_tags: Any = None,
    require_all_signal_tags: bool = False,
    exclude_signal_tags: Any = None,
    min_prior_win_rate: float = None,
    min_prior_avg_return: float = None,
    max_prior_avg_adverse: float = None,
    use_margin_eligibility_context: bool = False,
    include_qualified_trades: bool = False,
) -> Dict[str, Any]:
    snapshot = _load_snapshot(settings, use_live_snapshot)
    candidates = select_deep_scan_candidates(
        snapshot=snapshot,
        max_deep=max_deep,
        min_amount=settings.scan_min_amount,
        min_price=settings.scan_min_price,
        max_price=settings.scan_max_price,
    )

    proxy_frames = []
    for proxy in MARKET_PROXY_SYMBOLS:
        frame, _source = _history_with_file_cache(
            provider,
            proxy["market"],
            proxy["symbol"],
            lookback_days,
            "qfq",
            cache_dir,
        )
        proxy_frames.append(add_indicators(frame))

    all_trades: List[Dict[str, Any]] = []
    by_signal_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    errors = []
    fetched_symbols = 0
    market_cache: Dict[str, Dict[str, Any]] = {}
    proxy_return_cache: Dict[str, Dict[str, Any]] = {}
    announcement_blocked_count = 0
    announcement_scored_count = 0
    announcement_positive_count = 0
    announcement_watch_risk_count = 0
    announcement_fetch_errors = 0
    announcement_blocked_by_event: Dict[str, int] = {}
    announcement_lookback_days = announcement_lookback_days or settings.announcement_lookback_days
    required_events = set(_split_events(require_announcement_events))
    excluded_events = set(_split_events(exclude_announcement_events))
    required_market_levels = set(_split_events(require_market_levels))
    required_signal_tags = set(_split_events(require_signal_tags))
    excluded_signal_tags = set(_split_events(exclude_signal_tags))
    margin_symbol_map: Dict[str, Dict[str, Any]] = {}
    margin_fetch_errors = 0
    if use_margin_eligibility_context:
        try:
            margin_payload = MarginEligibilityProvider(
                settings.margin_eligibility_cache_path,
                settings.enable_margin_eligibility_context,
            ).build_map(use_cache_on_error=True)
            margin_symbol_map = margin_payload.get("symbol_map") or {}
        except Exception as exc:
            margin_fetch_errors += 1
            errors.append({"stage": "margin_eligibility_context", "message": str(exc)})

    for position, candidate in enumerate(candidates, 1):
        symbol = candidate.get("symbol")
        try:
            frame, _source = _history_with_file_cache(
                provider, "a", symbol, lookback_days, "qfq", cache_dir
            )
            frame = add_indicators(frame)
            fetched_symbols += 1
            announcement_items: List[Dict[str, Any]] = []
            if use_announcement_context:
                announcement_start = (
                    _date_value(start_date) - pd.Timedelta(days=announcement_lookback_days)
                ).strftime("%Y%m%d")
                announcement_end = _date_yyyymmdd(frame["date"].max())
                try:
                    announcement_items = _announcements_with_file_cache(
                        str(symbol),
                        announcement_start,
                        announcement_end,
                        cache_dir,
                    )
                except Exception as exc:
                    announcement_fetch_errors += 1
                    errors.append(
                        {
                            "symbol": symbol,
                            "name": candidate.get("name"),
                            "message": "announcement_fetch_failed: %s" % exc,
                        }
                    )
            prior_outcomes = []
            for index in range(90, len(frame) - hold_days - 1):
                signal_date = str(frame.iloc[index]["date"])
                signal = evaluate_signal(frame.iloc[: index + 1])
                if signal["action"] not in {"BUY", "WATCH"} or signal["score"] < 2:
                    continue

                entry_index = index + 1
                exit_index = entry_index + hold_days
                executable = assess_entry_executability(
                    frame.iloc[index],
                    frame.iloc[entry_index],
                    max_gap_up_pct=settings.max_entry_gap_up_pct,
                    max_gap_down_pct=settings.max_entry_gap_down_pct,
                    locked_limit_gap_pct=settings.locked_limit_gap_pct,
                    max_intraday_range_pct=settings.max_entry_intraday_range_pct,
                    decision_cutoff="next_open",
                )
                if not executable["executable"]:
                    continue

                realized = {
                    **_realized_trade_from_future(
                        frame,
                        entry_index,
                        exit_index,
                        stop_loss_pct=stop_loss_pct,
                        take_profit_pct=take_profit_pct,
                        trailing_stop_pct=trailing_stop_pct,
                    ),
                    "signal_date": signal_date,
                }
                prior_outcomes.append(realized)
                if signal_date < start_date:
                    continue

                matured_prior = [item for item in prior_outcomes if item["exit_date"] < signal_date]
                quality = _quality_from_prior(matured_prior, settings)
                if not quality:
                    continue
                if min_prior_win_rate is not None and quality["win_rate_pct"] < float(
                    min_prior_win_rate
                ):
                    continue
                if min_prior_avg_return is not None and quality["avg_return_pct"] < float(
                    min_prior_avg_return
                ):
                    continue
                if max_prior_avg_adverse is not None and quality["avg_adverse_pct"] > float(
                    max_prior_avg_adverse
                ):
                    continue
                if signal_date not in market_cache:
                    market_cache[signal_date] = _historical_market_context(
                        proxy_frames, signal_date
                    )
                market_context = market_cache[signal_date]
                if signal_date not in proxy_return_cache:
                    proxy_return_cache[signal_date] = _historical_proxy_returns(
                        proxy_frames, signal_date
                    )
                relative_strength = _relative_strength_context(
                    frame.iloc[index], proxy_return_cache[signal_date]
                )
                if required_market_levels and market_context["level"] not in required_market_levels:
                    continue
                allowed_actions = {"BUY"} if buy_only else {"BUY", "WATCH"}
                if not market_context["allow_watch"]:
                    allowed_actions = {"BUY"}
                score_threshold = max(market_context["min_signal_score"], min_score or 0)
                if signal["action"] not in allowed_actions or signal["score"] < score_threshold:
                    continue
                margin_eligibility = (
                    margin_symbol_map.get(str(symbol), {}) if use_margin_eligibility_context else {}
                )
                candidate_for_tags = (
                    {**candidate, "margin_eligibility": margin_eligibility}
                    if margin_eligibility
                    else candidate
                )
                signal_tags = sorted(
                    set(
                        _signal_tags(signal)
                        + list(relative_strength.get("tags") or [])
                        + build_candidate_context_tags(candidate_for_tags, quality)
                    )
                )
                signal_tag_set = set(signal_tags)
                if (
                    required_signal_tags
                    and require_all_signal_tags
                    and not required_signal_tags.issubset(signal_tag_set)
                ):
                    continue
                if (
                    required_signal_tags
                    and not require_all_signal_tags
                    and not (signal_tag_set & required_signal_tags)
                ):
                    continue
                if excluded_signal_tags and signal_tag_set & excluded_signal_tags:
                    continue

                action_bonus = {"BUY": 25, "WATCH": 12}.get(signal["action"], 0)
                announcement_context = {}
                if use_announcement_context:
                    announcement_context = build_announcement_context(
                        announcement_items,
                        as_of_date=signal_date,
                        lookback_days=announcement_lookback_days,
                        updated_at=signal_date,
                    )
                    if announcement_context.get("level") != "neutral":
                        announcement_scored_count += 1
                    if announcement_context.get("level") == "positive":
                        announcement_positive_count += 1
                    if announcement_context.get("level") == "watch_risk":
                        announcement_watch_risk_count += 1
                    if not announcement_context.get("allow_recommendation", True):
                        announcement_blocked_count += 1
                        _add_counts(
                            announcement_blocked_by_event,
                            announcement_context.get("event_counts") or {},
                        )
                        continue
                    event_set = set((announcement_context.get("event_counts") or {}).keys())
                    if (
                        required_events
                        and require_all_announcement_events
                        and not required_events.issubset(event_set)
                    ):
                        continue
                    if (
                        required_events
                        and not require_all_announcement_events
                        and not (event_set & required_events)
                    ):
                        continue
                    if excluded_events and event_set & excluded_events:
                        continue
                rank_score = (
                    float(signal["score"]) * 12
                    + float(signal["confidence"])
                    + action_bonus
                    + float(candidate.get("prefilter_score") or 0)
                    + quality["score_bonus"]
                    + _num(announcement_context.get("score_adjustment"))
                    + market_context["score_adjustment"]
                )
                trade = {
                    **realized,
                    "symbol": symbol,
                    "name": candidate.get("name"),
                    "candidate_rank": position,
                    "candidate_prefilter_score": candidate.get("prefilter_score"),
                    "candidate_amount": candidate.get("amount"),
                    "candidate_latest": candidate.get("latest"),
                    "candidate_change_pct": candidate.get("change_pct"),
                    "action": signal["action"],
                    "score": float(signal["score"]),
                    "strategy_signal": _strategy_signal_snapshot(signal),
                    "rank_score": round(rank_score, 4),
                    "market_level": market_context["level"],
                    "signal_tags": signal_tags,
                    "relative_strength": {
                        key: value for key, value in relative_strength.items() if key != "tags"
                    },
                    "prior_count": quality["trade_count"],
                    "prior_win_rate_pct": round(quality["win_rate_pct"], 2),
                    "prior_avg_return_pct": round(quality["avg_return_pct"], 2),
                    "prior_avg_adverse_pct": round(quality["avg_adverse_pct"], 2),
                    "entry_executability": executable,
                    "margin_eligibility": margin_eligibility,
                    "announcement_context": _compact_announcement_context(announcement_context)
                    if use_announcement_context
                    else {},
                }
                all_trades.append(trade)
                by_signal_date[signal_date].append(trade)
        except Exception as exc:
            errors.append({"symbol": symbol, "name": candidate.get("name"), "message": str(exc)})
        if progress_every and position % progress_every == 0:
            _emit_progress(
                {
                    "processed": position,
                    "candidate_count": len(candidates),
                    "raw_qualified_trade_count": len(all_trades),
                    "announcement_blocked_count": announcement_blocked_count,
                    "errors": len(errors),
                }
            )

    selected = _select_with_portfolio_controls(
        by_signal_date,
        top_n,
        symbol_cooldown_days=symbol_cooldown_days,
        max_active_positions=max_active_positions,
    )
    selected_by_signal_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in selected:
        selected_by_signal_date[item["signal_date"]].append(item)

    basket_returns = []
    for signal_date, top_trades in selected_by_signal_date.items():
        if top_trades:
            basket_returns.append(
                {
                    "signal_date": signal_date,
                    "return_pct": sum(item["return_pct"] for item in top_trades) / len(top_trades),
                    "count": len(top_trades),
                }
            )
    basket_returns.sort(key=lambda item: item["signal_date"])

    wins = [item for item in selected if item["return_pct"] > 0]
    adverse_items = [item for item in selected if item.get("max_adverse_pct") is not None]
    equity_points = _equity_points_from_basket_returns(basket_returns, hold_days)
    compound = equity_points[-1]["equity"] if equity_points else 1.0
    equity_curve = [1.0] + [point["equity"] for point in equity_points]
    rolling_1y = _window_portfolio_stats(equity_points, days=365)

    returns = [item["return_pct"] for item in selected]
    median_return = float(pd.Series(returns).median()) if returns else 0.0
    summary = {
        "start_date": start_date,
        "end_date": max([item["exit_date"] for item in selected], default=None),
        "candidate_count": len(candidates),
        "fetched_symbols": fetched_symbols,
        "error_count": len(errors),
        "raw_qualified_trade_count": len(all_trades),
        "selected_trade_count": len(selected),
        "signal_days": len(basket_returns),
        "top_n": top_n,
        "hold_days": hold_days,
        "buy_only": buy_only,
        "min_score": min_score,
        "stop_loss_pct": stop_loss_pct,
        "take_profit_pct": take_profit_pct,
        "trailing_stop_pct": trailing_stop_pct,
        "symbol_cooldown_days": symbol_cooldown_days,
        "max_active_positions": max_active_positions,
        "announcement_context_enabled": use_announcement_context,
        "announcement_lookback_days": announcement_lookback_days
        if use_announcement_context
        else None,
        "required_announcement_events": sorted(required_events),
        "require_all_announcement_events": bool(require_all_announcement_events),
        "excluded_announcement_events": sorted(excluded_events),
        "required_market_levels": sorted(required_market_levels),
        "required_signal_tags": sorted(required_signal_tags),
        "require_all_signal_tags": bool(require_all_signal_tags),
        "excluded_signal_tags": sorted(excluded_signal_tags),
        "min_prior_win_rate": min_prior_win_rate,
        "min_prior_avg_return": min_prior_avg_return,
        "max_prior_avg_adverse": max_prior_avg_adverse,
        "announcement_blocked_count": announcement_blocked_count,
        "announcement_scored_count": announcement_scored_count,
        "announcement_positive_count": announcement_positive_count,
        "announcement_watch_risk_count": announcement_watch_risk_count,
        "announcement_fetch_errors": announcement_fetch_errors,
        "announcement_blocked_by_event": dict(sorted(announcement_blocked_by_event.items())),
        "margin_eligibility_context_enabled": bool(use_margin_eligibility_context),
        "margin_eligibility_scope": "sse_szse_current" if use_margin_eligibility_context else None,
        "margin_eligibility_days": 1 if margin_symbol_map else 0,
        "margin_eligibility_fetch_errors": margin_fetch_errors,
        "margin_eligibility_caveat": (
            "research-backtest uses the current official SSE/SZSE margin list and can introduce "
            "lookahead in historical slices. Use research-historical-universe --margin-eligibility-context "
            "for SZSE as-of historical tags."
        )
        if use_margin_eligibility_context
        else None,
        "trade_win_rate_pct": round(len(wins) / len(selected) * 100, 2) if selected else None,
        "trade_avg_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
        "trade_median_return_pct": round(median_return, 2) if returns else None,
        "trade_avg_max_adverse_pct": round(
            sum(item["max_adverse_pct"] for item in adverse_items) / len(adverse_items), 2
        )
        if adverse_items
        else None,
        "basket_win_rate_pct": round(
            len([item for item in basket_returns if item["return_pct"] > 0])
            / len(basket_returns)
            * 100,
            2,
        )
        if basket_returns
        else None,
        "basket_avg_return_pct": round(
            sum(item["return_pct"] for item in basket_returns) / len(basket_returns),
            2,
        )
        if basket_returns
        else None,
        "portfolio_compounded_return_pct": round((compound - 1) * 100, 2)
        if basket_returns
        else None,
        "portfolio_max_drawdown_pct": _max_drawdown_pct(equity_curve) if basket_returns else None,
        "rolling_1y": rolling_1y,
        "hs300etf_buy_hold_pct": round(
            _benchmark_return(provider, "510300", start_date, lookback_days), 2
        ),
        "cybetf_buy_hold_pct": round(
            _benchmark_return(provider, "159915", start_date, lookback_days), 2
        ),
    }
    return {
        "summary": summary,
        "announcement_group_stats": _announcement_group_stats(selected)
        if use_announcement_context
        else {},
        "research_group_stats": _research_group_stats(selected),
        "qualified_trades": all_trades if include_qualified_trades else [],
        "best": sorted(selected, key=lambda item: item["return_pct"], reverse=True)[:5],
        "worst": sorted(selected, key=lambda item: item["return_pct"])[:5],
        "errors": errors[:20],
    }


def _run_historical_universe_research_backtest_resolved(
    settings: Settings,
    provider: AkshareDataProvider,
    start_date: str,
    max_deep: int,
    top_n: int,
    hold_days: int,
    lookback_days: int,
    end_date: str = None,
    max_universe_symbols: int = 300,
    use_live_snapshot: bool = False,
    cache_dir: str = "data/research_cache",
    progress_every: int = 0,
    buy_only: bool = False,
    min_score: float = None,
    stop_loss_pct: float = None,
    take_profit_pct: float = None,
    trailing_stop_pct: float = None,
    symbol_cooldown_days: int = 0,
    max_active_positions: int = 0,
    use_announcement_context: bool = False,
    announcement_lookback_days: int = None,
    require_announcement_events: Any = None,
    require_all_announcement_events: bool = False,
    exclude_announcement_events: Any = None,
    require_market_levels: Any = None,
    require_signal_tags: Any = None,
    require_all_signal_tags: bool = False,
    exclude_signal_tags: Any = None,
    min_prior_win_rate: float = None,
    min_prior_avg_return: float = None,
    max_prior_avg_adverse: float = None,
    use_industry_rotation_context: bool = False,
    industry_rotation_max_boards: int = 40,
    use_margin_eligibility_context: bool = False,
    use_dragon_tiger_context: bool = False,
    include_qualified_trades: bool = False,
    pit_universe_path: str = None,
    audited_pit_universe_path: str = None,
    composite_pit_descriptor_path: str = None,
    expected_coverage_audit_sha256: str = None,
    expected_artifact_root_sha256: str = None,
    expected_composite_root_sha256: str = None,
    temporal_contract_path: str = None,
    expected_temporal_contract_sha256: str = None,
    expected_temporal_role: str = None,
    _resolved_universe_items: List[Dict[str, Any]] = None,
    _resolved_pit_universe: Any = None,
) -> Dict[str, Any]:
    if _resolved_universe_items is None:
        raise RuntimeError("historical universe must be resolved by the managed entrypoint")
    _require_positive_hold_days(hold_days)
    universe_items = _resolved_universe_items
    pit_universe = _resolved_pit_universe
    if (
        pit_universe is None
        or not callable(getattr(pit_universe, "items_as_of", None))
        or not callable(getattr(pit_universe, "open_sessions", None))
    ):
        raise ValueError(
            "stock-only historical research requires a PIT universe with "
            "items_as_of and open_sessions"
        )
    analysis_end_date = end_date or (pit_universe.end_date if pit_universe is not None else None)
    if analysis_end_date > pit_universe.end_date:
        raise ValueError("requested end_date exceeds audited artifact coverage")
    artifact_adapter: Optional[ArtifactNativeReplayAdapter] = None
    artifact_sessions: List[str] = []
    artifact_session_positions: Dict[str, int] = {}
    artifact_verdict_cache: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    if getattr(pit_universe, "is_audited_store_artifact", False):
        adapter_temporal_kwargs = {}
        if expected_temporal_contract_sha256 is not None:
            adapter_temporal_kwargs = {
                "expected_temporal_contract_sha256": expected_temporal_contract_sha256,
                "expected_temporal_role": expected_temporal_role,
            }
        artifact_adapter = ArtifactNativeReplayAdapter(
            pit_universe, **adapter_temporal_kwargs
        )
        if any(value is not None for value in (stop_loss_pct, take_profit_pct, trailing_stop_pct)):
            raise ValueError(
                "artifact-native replay does not support unproven intraday exit models"
            )
        if any(
            (
                use_announcement_context,
                use_industry_rotation_context,
                use_margin_eligibility_context,
                use_dragon_tiger_context,
            )
        ):
            raise ValueError(
                "artifact-native replay forbids external contexts not bound to the artifact"
            )
        artifact_sessions = pit_universe.open_sessions(
            pit_universe.start_date, analysis_end_date
        )
        if artifact_sessions != sorted(set(artifact_sessions)):
            raise ValueError("audited open sessions are duplicated or unsorted")
        artifact_session_positions = {
            session: position for position, session in enumerate(artifact_sessions)
        }

    symbol_frames: Dict[str, Dict[str, Any]] = {}
    errors = []
    fetched_symbols = 0
    for position, item in enumerate(universe_items, 1):
        symbol = item.get("symbol")
        try:
            if artifact_adapter is not None:
                frame = artifact_adapter.signal_frame(
                    str(symbol), pit_universe.start_date, analysis_end_date
                )
            else:
                frame, _source = _history_with_file_cache(
                    provider, "a", symbol, lookback_days, "qfq", cache_dir
                )
                if analysis_end_date:
                    frame = frame[frame["date"] <= analysis_end_date].copy()
            symbol_frames[str(symbol)] = {"base": item, "frame": add_indicators(frame)}
            fetched_symbols += 1
        except Exception as exc:
            if pit_universe is not None:
                raise ValueError(
                    f"PIT universe history is incomplete for {symbol}: {exc}"
                ) from exc
            errors.append({"symbol": symbol, "name": item.get("name"), "message": str(exc)})
        if progress_every and position % progress_every == 0:
            _emit_progress(
                {
                    "phase": "fetch_history",
                    "processed": position,
                    "universe_symbol_count": len(universe_items),
                    "fetched_symbols": fetched_symbols,
                    "errors": len(errors),
                }
            )

    candidate_maps = _build_historical_candidate_maps(
        symbol_frames,
        start_date=start_date,
        hold_days=hold_days,
        max_deep=max_deep,
        settings=settings,
        universe_source=pit_universe,
    )
    market_breadth_by_date = _historical_market_breadth(
        symbol_frames,
        start_date=start_date,
        hold_days=hold_days,
        universe_source=pit_universe,
    )
    def historical_signal_indexes(frame: pd.DataFrame) -> range:
        stop = (
            len(frame)
            if artifact_adapter is not None
            else len(frame) - hold_days - 1
        )
        return range(90, stop)

    historical_signal_dates = {
        str(frame.iloc[index]["date"])
        for payload in symbol_frames.values()
        for frame in (payload["frame"],)
        for index in historical_signal_indexes(frame)
    }
    eligible_dates_by_symbol = _batch_pit_eligible_dates_by_symbol(
        pit_universe, historical_signal_dates
    )
    industry_rotation_by_date: Dict[str, Dict[str, Any]] = {}
    if use_industry_rotation_context:
        try:
            end_date = max(
                [str(payload["frame"]["date"].max()) for payload in symbol_frames.values()],
                default=start_date,
            )
            industry_rotation_by_date = _historical_industry_rotation_contexts(
                settings,
                start_date=start_date,
                end_date=end_date,
                max_boards=industry_rotation_max_boards,
            )
        except Exception as exc:
            errors.append({"stage": "industry_rotation_context", "message": str(exc)})
    dragon_tiger_by_date: Dict[str, Dict[str, Any]] = {}
    dragon_tiger_fetch_errors = 0
    if use_dragon_tiger_context:
        try:
            end_date = max(
                [str(payload["frame"]["date"].max()) for payload in symbol_frames.values()],
                default=start_date,
            )
            dragon_tiger_payload = DragonTigerProvider(cache_dir).build_contexts(
                start_date=start_date,
                end_date=end_date,
                use_cache_on_error=True,
            )
            dragon_tiger_by_date = dragon_tiger_payload.get("by_date") or {}
        except Exception as exc:
            dragon_tiger_fetch_errors += 1
            errors.append({"stage": "dragon_tiger_context", "message": str(exc)})

    all_trades: List[Dict[str, Any]] = []
    by_signal_date: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    market_context_cache: Dict[str, Dict[str, Any]] = {}
    market_returns_cache: Dict[str, Dict[str, Any]] = {}
    announcement_blocked_count = 0
    announcement_scored_count = 0
    announcement_positive_count = 0
    announcement_watch_risk_count = 0
    announcement_fetch_errors = 0
    announcement_blocked_by_event: Dict[str, int] = {}
    announcement_lookback_days = announcement_lookback_days or settings.announcement_lookback_days
    required_events = set(_split_events(require_announcement_events))
    excluded_events = set(_split_events(exclude_announcement_events))
    required_market_levels = set(_split_events(require_market_levels))
    required_signal_tags = set(_split_events(require_signal_tags))
    excluded_signal_tags = set(_split_events(exclude_signal_tags))
    margin_provider = MarginEligibilityProvider(
        settings.margin_eligibility_cache_path,
        settings.enable_margin_eligibility_context,
    )
    margin_by_date: Dict[str, Dict[str, Any]] = {}
    margin_fetch_errors = 0

    def margin_context_for(signal_date: str, symbol: str) -> Dict[str, Any]:
        nonlocal margin_fetch_errors
        if not use_margin_eligibility_context:
            return {}
        if not str(symbol).startswith(("0", "3")):
            return {}
        if signal_date not in margin_by_date:
            try:
                margin_by_date[signal_date] = margin_provider.build_szse_underlying_map(
                    signal_date,
                    cache_dir=cache_dir,
                    use_cache_on_error=True,
                )
            except Exception as exc:
                margin_fetch_errors += 1
                margin_by_date[signal_date] = {
                    "symbol_map": {},
                    "summary": {
                        "enabled": True,
                        "scope": "szse_underlying_asof",
                        "requested_as_of": signal_date,
                        "error": str(exc),
                    },
                    "errors": [{"source": "szse_underlying_asof", "message": str(exc)}],
                }
        return (margin_by_date.get(signal_date, {}).get("symbol_map") or {}).get(str(symbol), {})

    for position, (symbol, payload) in enumerate(symbol_frames.items(), 1):
        frame = payload["frame"]
        base = payload["base"]
        artifact_frame_date_positions = (
            {
                str(value): frame_position
                for frame_position, value in enumerate(frame["date"].tolist())
            }
            if artifact_adapter is not None
            else None
        )
        if artifact_frame_date_positions is not None and len(
            artifact_frame_date_positions
        ) != len(frame):
            raise ValueError(f"artifact analysis frame has duplicate dates: {symbol}")
        batched_eligible_dates = (
            eligible_dates_by_symbol.get(str(symbol))
            if eligible_dates_by_symbol is not None
            else None
        )
        try:
            announcement_items: List[Dict[str, Any]] = []
            if use_announcement_context:
                announcement_start = (
                    _date_value(start_date) - pd.Timedelta(days=announcement_lookback_days)
                ).strftime("%Y%m%d")
                announcement_end = _date_yyyymmdd(frame["date"].max())
                try:
                    announcement_items = _announcements_with_file_cache(
                        str(symbol),
                        announcement_start,
                        announcement_end,
                        cache_dir,
                    )
                except Exception as exc:
                    announcement_fetch_errors += 1
                    errors.append(
                        {
                            "symbol": symbol,
                            "name": base.get("name"),
                            "message": "announcement_fetch_failed: %s" % exc,
                        }
                    )
            prior_outcomes = []
            for index in historical_signal_indexes(frame):
                signal_date = str(frame.iloc[index]["date"])
                if pit_universe is not None:
                    if eligible_dates_by_symbol is not None:
                        if (
                            batched_eligible_dates is None
                            or signal_date not in batched_eligible_dates
                        ):
                            continue
                    elif _pit_historical_member(
                        pit_universe, str(symbol), signal_date
                    ) is None:
                        continue
                signal_frame = frame.iloc[: index + 1]
                signal_frame_index = index
                if artifact_adapter is not None:
                    # qfq bases for different causal as-of dates differ by one
                    # positive constant. Rescale the already enriched prefix
                    # back to the signal-date raw close instead of repeating an
                    # artifact SQL read and full indicator calculation.
                    signal_frame = _artifact_causal_signal_window(frame, index)
                    if signal_frame.empty or str(signal_frame.iloc[-1]["date"]) != signal_date:
                        raise ValueError(
                            f"artifact signal frame is incomplete as of {symbol}/{signal_date}"
                        )
                    signal_frame_index = len(signal_frame) - 1
                signal = evaluate_signal(signal_frame)
                if signal["action"] not in {"BUY", "WATCH"} or signal["score"] < 2:
                    continue

                if artifact_adapter is not None:
                    artifact_trade = _artifact_native_time_exit_trade(
                        adapter=artifact_adapter,
                        verdict_cache=artifact_verdict_cache,
                        symbol=str(symbol),
                        signal_date=signal_date,
                        sessions=artifact_sessions,
                        session_positions=artifact_session_positions,
                        artifact_start_date=pit_universe.start_date,
                        hold_days=hold_days,
                        settings=settings,
                        analysis_frame=frame,
                        analysis_date_positions=artifact_frame_date_positions,
                    )
                    if artifact_trade is None:
                        continue
                    realized_payload, executable = artifact_trade
                    realized = {**realized_payload, "signal_date": signal_date}
                else:
                    entry_index = index + 1
                    exit_index = entry_index + hold_days
                    executable = assess_entry_executability(
                        frame.iloc[index],
                        frame.iloc[entry_index],
                        max_gap_up_pct=settings.max_entry_gap_up_pct,
                        max_gap_down_pct=settings.max_entry_gap_down_pct,
                        locked_limit_gap_pct=settings.locked_limit_gap_pct,
                        max_intraday_range_pct=settings.max_entry_intraday_range_pct,
                        decision_cutoff="next_open",
                    )
                    if not executable["executable"]:
                        continue

                    realized = {
                        **_realized_trade_from_future(
                            frame,
                            entry_index,
                            exit_index,
                            stop_loss_pct=stop_loss_pct,
                            take_profit_pct=take_profit_pct,
                            trailing_stop_pct=trailing_stop_pct,
                        ),
                        "signal_date": signal_date,
                    }
                prior_outcomes.append(realized)
                if signal_date < start_date:
                    continue

                candidate = (candidate_maps.get(signal_date) or {}).get(str(symbol))
                if not candidate:
                    continue
                margin_eligibility = margin_context_for(signal_date, str(symbol))
                if margin_eligibility:
                    candidate = {**candidate, "margin_eligibility": margin_eligibility}

                matured_prior = [item for item in prior_outcomes if item["exit_date"] < signal_date]
                quality = _quality_from_prior(matured_prior, settings)
                if not quality:
                    continue
                if min_prior_win_rate is not None and quality["win_rate_pct"] < float(
                    min_prior_win_rate
                ):
                    continue
                if min_prior_avg_return is not None and quality["avg_return_pct"] < float(
                    min_prior_avg_return
                ):
                    continue
                if max_prior_avg_adverse is not None and quality["avg_adverse_pct"] > float(
                    max_prior_avg_adverse
                ):
                    continue
                if signal_date not in market_context_cache:
                    market_breadth = market_breadth_by_date.get(signal_date, {})
                    market_context_cache[signal_date] = _stock_breadth_market_context(
                        market_breadth
                    )
                    market_returns_cache[signal_date] = _stock_market_returns(
                        market_breadth
                    )
                market_context = market_context_cache[signal_date]
                relative_strength = _stock_relative_strength_context(
                    signal_frame.iloc[signal_frame_index],
                    market_returns_cache[signal_date],
                )
                market_breadth = market_breadth_by_date.get(signal_date, {})
                price_action = _price_action_context(
                    signal_frame, signal_frame_index, symbol
                )
                industry_rotation = industry_rotation_by_date.get(signal_date, {})
                dragon_tiger = (dragon_tiger_by_date.get(signal_date) or {}).get(str(symbol), {})
                if required_market_levels and market_context["level"] not in required_market_levels:
                    continue
                # allowed_actions 显式尊重 allow_buy / allow_watch / buy_only：
                # unknown（allow_buy=False）→ 空 set，BUY/WATCH 都不穿透；
                # defensive（allow_watch=False）→ 仅 BUY；buy_only 即便 allow_watch 也不放 WATCH。
                allowed_actions = set()
                if market_context["allow_buy"]:
                    allowed_actions.add("BUY")
                if market_context["allow_watch"] and not buy_only:
                    allowed_actions.add("WATCH")
                score_threshold = max(market_context["min_signal_score"], min_score or 0)
                if signal["action"] not in allowed_actions or signal["score"] < score_threshold:
                    continue
                signal_tags = sorted(
                    set(
                        _signal_tags(signal)
                        + list(relative_strength.get("tags") or [])
                        + list(market_breadth.get("tags") or [])
                        + list(price_action.get("tags") or [])
                        + list(industry_rotation.get("tags") or [])
                        + list(dragon_tiger.get("tags") or [])
                        + build_candidate_context_tags(candidate, quality)
                    )
                )
                signal_tag_set = set(signal_tags)
                if (
                    required_signal_tags
                    and require_all_signal_tags
                    and not required_signal_tags.issubset(signal_tag_set)
                ):
                    continue
                if (
                    required_signal_tags
                    and not require_all_signal_tags
                    and not (signal_tag_set & required_signal_tags)
                ):
                    continue
                if excluded_signal_tags and signal_tag_set & excluded_signal_tags:
                    continue

                action_bonus = {"BUY": 25, "WATCH": 12}.get(signal["action"], 0)
                announcement_context = {}
                if use_announcement_context:
                    announcement_context = build_announcement_context(
                        announcement_items,
                        as_of_date=signal_date,
                        lookback_days=announcement_lookback_days,
                        updated_at=signal_date,
                    )
                    if announcement_context.get("level") != "neutral":
                        announcement_scored_count += 1
                    if announcement_context.get("level") == "positive":
                        announcement_positive_count += 1
                    if announcement_context.get("level") == "watch_risk":
                        announcement_watch_risk_count += 1
                    if not announcement_context.get("allow_recommendation", True):
                        announcement_blocked_count += 1
                        _add_counts(
                            announcement_blocked_by_event,
                            announcement_context.get("event_counts") or {},
                        )
                        continue
                    event_set = set((announcement_context.get("event_counts") or {}).keys())
                    if (
                        required_events
                        and require_all_announcement_events
                        and not required_events.issubset(event_set)
                    ):
                        continue
                    if (
                        required_events
                        and not require_all_announcement_events
                        and not (event_set & required_events)
                    ):
                        continue
                    if excluded_events and event_set & excluded_events:
                        continue
                rank_score = (
                    float(signal["score"]) * 12
                    + float(signal["confidence"])
                    + action_bonus
                    + float(candidate.get("prefilter_score") or 0)
                    + quality["score_bonus"]
                    + _num(announcement_context.get("score_adjustment"))
                    + market_context["score_adjustment"]
                )
                trade = {
                    **realized,
                    "symbol": symbol,
                    "name": candidate.get("name"),
                    "candidate_rank": candidate.get("candidate_rank"),
                    "candidate_rank_pct": candidate.get("candidate_rank_pct"),
                    "candidate_prefilter_score": candidate.get("prefilter_score"),
                    "candidate_amount": candidate.get("amount"),
                    "candidate_amount_rank": candidate.get("amount_rank"),
                    "candidate_amount_rank_pct": candidate.get("amount_rank_pct"),
                    "candidate_latest": candidate.get("latest"),
                    "candidate_change_pct": candidate.get("change_pct"),
                    "action": signal["action"],
                    "score": float(signal["score"]),
                    "strategy_signal": _strategy_signal_snapshot(signal),
                    "rank_score": round(rank_score, 4),
                    "market_level": market_context["level"],
                    "market_context": market_context,
                    "signal_tags": signal_tags,
                    "relative_strength": {
                        key: value for key, value in relative_strength.items() if key != "tags"
                    },
                    "market_breadth": {
                        key: value for key, value in market_breadth.items() if key != "tags"
                    },
                    "price_action": {
                        key: value for key, value in price_action.items() if key != "tags"
                    },
                    "industry_rotation": {
                        key: value for key, value in industry_rotation.items() if key != "tags"
                    },
                    "dragon_tiger": {
                        key: value for key, value in dragon_tiger.items() if key != "tags"
                    },
                    "margin_eligibility": margin_eligibility,
                    "prior_count": quality["trade_count"],
                    "prior_win_rate_pct": round(quality["win_rate_pct"], 2),
                    "prior_avg_return_pct": round(quality["avg_return_pct"], 2),
                    "prior_avg_adverse_pct": round(quality["avg_adverse_pct"], 2),
                    "entry_executability": executable,
                    "announcement_context": _compact_announcement_context(announcement_context)
                    if use_announcement_context
                    else {},
                }
                all_trades.append(trade)
                by_signal_date[signal_date].append(trade)
        except Exception as exc:
            if artifact_adapter is not None:
                raise ValueError(
                    f"artifact-native evaluation failed for {symbol}: {exc}"
                ) from exc
            errors.append({"symbol": symbol, "name": base.get("name"), "message": str(exc)})
        if progress_every and position % progress_every == 0:
            _emit_progress(
                {
                    "phase": "evaluate_signals",
                    "processed": position,
                    "fetched_symbols": fetched_symbols,
                    "raw_qualified_trade_count": len(all_trades),
                    "historical_candidate_days": len(candidate_maps),
                    "errors": len(errors),
                }
            )

    selected = _select_with_portfolio_controls(
        by_signal_date,
        top_n,
        symbol_cooldown_days=symbol_cooldown_days,
        max_active_positions=max_active_positions,
    )
    stock_benchmark_summary = _stock_universe_equal_weight_benchmark(
        market_breadth_by_date,
        start_date,
        analysis_end_date,
        expected_sessions=pit_universe.open_sessions(start_date, analysis_end_date),
    )
    strict_audited_development_replay = (
        isinstance(
            artifact_adapter,
            research_artifact_replay_module.ArtifactNativeReplayAdapter,
        )
        and isinstance(
            pit_universe,
            research_pit_store_module.AuditedPointInTimeUniverse,
        )
        and pit_universe.external_temporal_authority_verified is True
        and pit_universe.temporal_role == "development"
        and pit_universe.temporal_contract_sha256
        == expected_temporal_contract_sha256
        and expected_temporal_role == "development"
        and artifact_adapter.artifact_root_sha256
        == pit_universe.artifact_root_sha256
    )
    composite_audited_development_replay = (
        isinstance(
            artifact_adapter,
            research_artifact_replay_module.ArtifactNativeReplayAdapter,
        )
        and isinstance(pit_universe, CompositeAuditedUniverse)
        and pit_universe.external_temporal_authority_verified is True
        and pit_universe.temporal_role == "development"
        and pit_universe.temporal_contract_sha256
        == expected_temporal_contract_sha256
        and expected_temporal_role == "development"
        and artifact_adapter.artifact_root_sha256
        == pit_universe.composite_root_sha256
    )
    audited_development_integrity = (
        strict_audited_development_replay
        or composite_audited_development_replay
    )
    audited_authority = (
        {
            "artifact_root_sha256": pit_universe.artifact_root_sha256,
            "coverage_audit_sha256": pit_universe.coverage_audit_sha256,
            "temporal_contract_sha256": pit_universe.temporal_contract_sha256,
            "temporal_role": pit_universe.temporal_role,
            "artifact_manifest_sha256": pit_universe.manifest["manifest_sha256"],
            "market_generation_root_sha256": pit_universe.manifest[
                "market_generations"
            ]["root_sha256"],
            "stock_generation_lineage_sha256": pit_universe.manifest[
                "stock_generation"
            ]["lineage_sha256"],
        }
        if strict_audited_development_replay
        else None
    )
    result = _research_payload_from_trades(
        selected=selected,
        all_trades=all_trades,
        errors=errors,
        settings=settings,
        provider=provider,
        start_date=start_date,
        max_deep=max_deep,
        top_n=top_n,
        hold_days=hold_days,
        lookback_days=lookback_days,
        buy_only=buy_only,
        min_score=min_score,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        trailing_stop_pct=trailing_stop_pct,
        symbol_cooldown_days=symbol_cooldown_days,
        max_active_positions=max_active_positions,
        required_events=required_events,
        require_all_announcement_events=require_all_announcement_events,
        excluded_events=excluded_events,
        required_market_levels=required_market_levels,
        required_signal_tags=required_signal_tags,
        require_all_signal_tags=require_all_signal_tags,
        excluded_signal_tags=excluded_signal_tags,
        min_prior_win_rate=min_prior_win_rate,
        min_prior_avg_return=min_prior_avg_return,
        max_prior_avg_adverse=max_prior_avg_adverse,
        use_announcement_context=use_announcement_context,
        announcement_lookback_days=announcement_lookback_days,
        announcement_blocked_count=announcement_blocked_count,
        announcement_scored_count=announcement_scored_count,
        announcement_positive_count=announcement_positive_count,
        announcement_watch_risk_count=announcement_watch_risk_count,
        announcement_fetch_errors=announcement_fetch_errors,
        announcement_blocked_by_event=announcement_blocked_by_event,
        candidate_count=len(universe_items),
        fetched_symbols=fetched_symbols,
        include_qualified_trades=include_qualified_trades,
        analysis_end_date=analysis_end_date,
        market_benchmark_summary=stock_benchmark_summary,
        extra_summary={
            "market_context_source": "point_in_time_stock_breadth",
            "market_context_schema_version": STOCK_MARKET_CONTEXT_SCHEMA_VERSION,
            "market_context_etf_dependent": False,
            "artifact_native_replay": artifact_adapter is not None,
            "market_data_source": "audited_artifact"
            if artifact_adapter is not None
            and not composite_audited_development_replay
            else "audited_composite_artifacts"
            if composite_audited_development_replay
            else "mutable_provider_cache",
            "artifact_root_sha256": artifact_adapter.artifact_root_sha256
            if artifact_adapter is not None
            else None,
            "artifact_replay_contract_sha256": artifact_adapter.contract_sha256
            if artifact_adapter is not None
            else None,
            "candidate_mode": "dated_universe_artifact_prefilter"
            if pit_universe is not None
            else "historical_daily_prefilter",
            "max_universe_symbols": max_universe_symbols,
            "daily_prefilter_max_deep": max_deep,
            "historical_candidate_days": len(candidate_maps),
            "historical_market_breadth_days": len(market_breadth_by_date),
            "industry_rotation_context_enabled": bool(use_industry_rotation_context),
            "industry_rotation_max_boards": industry_rotation_max_boards
            if use_industry_rotation_context
            else None,
            "industry_rotation_days": len(industry_rotation_by_date),
            "dragon_tiger_context_enabled": bool(use_dragon_tiger_context),
            "dragon_tiger_days": len(dragon_tiger_by_date),
            "dragon_tiger_fetch_errors": dragon_tiger_fetch_errors,
            "dragon_tiger_caveat": DRAGON_TIGER_CAVEAT if use_dragon_tiger_context else None,
            "margin_eligibility_context_enabled": bool(use_margin_eligibility_context),
            "margin_eligibility_scope": "szse_underlying_asof"
            if use_margin_eligibility_context
            else None,
            "margin_eligibility_days": len(margin_by_date),
            "margin_eligibility_fetch_errors": margin_fetch_errors,
            "margin_eligibility_caveat": (
                "Historical margin tags currently use SZSE as-of official reports only. "
                "SSE current lists are not used as historical filters because the tested date parameters "
                "return the current list and would introduce lookahead."
            )
            if use_margin_eligibility_context
            else None,
            "research_caveat": (
                "Audited membership, causal signal bars, raw next-open fills, price limits and "
                "suspensions are composed from multiple independently anchored immutable artifacts. "
                "The ordered composite is development-only and has no strict/native evidence bundle "
                "or live-proof status."
                if composite_audited_development_replay
                else
                "Audited membership, causal signal bars, raw next-open fills, price limits and "
                "suspensions are bound to one immutable artifact. This remains development-only: "
                "historical backfill vintage is not contemporaneous final-OOS evidence, and the "
                "strict slice currently supports only open-to-open time exits."
                if artifact_adapter is not None
                else "Daily membership rows and historical names are content-hash bound, but source "
                "completeness and raw-to-normalized lineage are not proven; market bars still come "
                "from a mutable qfq cache and historical ST/suspension intervals are not frozen."
                if pit_universe is not None
                else "Historical prefilter is rebuilt by signal date, but the fetched symbol seed still "
                "comes from the current A-share snapshot and may contain survivorship/current-liquidity bias."
            ),
            "research_data_contract": (
                {
                    "schema_version": "research_data_contract/v1",
                    "artifact_role": "development_only",
                    "entry_decision_cutoff": "next_open",
                    "point_in_time": audited_development_integrity,
                    "development_integrity": audited_development_integrity,
                    "eligible_for_development_validation": False,
                    "eligible_for_final_validation": False,
                    "final_oos_eligible": False,
                    "development_eligibility_reasons": (
                        [
                            "artifact_native_evidence_not_compiled",
                            "qualified_trades_sha256_not_bound",
                            "qualified_trade_lineage_not_bound",
                            "strict_evidence_bundle_not_compiled",
                        ]
                        if strict_audited_development_replay
                        else [
                            "composite_artifact_native_evidence_not_compiled",
                            "qualified_trades_sha256_not_bound",
                            "qualified_trade_lineage_not_bound",
                            "strict_evidence_bundle_not_compiled",
                        ]
                        if composite_audited_development_replay
                        else ["strict_audited_artifact_replay_not_verified"]
                    ),
                    "universe_point_in_time": audited_development_integrity,
                    "universe_artifact_integrity_verified": True,
                    "audited_store_coverage_verified": audited_development_integrity,
                    "universe_raw_lineage_verified": audited_development_integrity,
                    "market_data_artifact_integrity_verified": (
                        audited_development_integrity
                    ),
                    "raw_execution_bars_bound": audited_development_integrity,
                    "causal_signal_adjustment_bound": (
                        audited_development_integrity
                    ),
                    "execution_model": "raw_next_open_buy_and_sell"
                    if artifact_adapter is not None
                    else "heuristic_next_open_entry",
                    "artifact_root_sha256": artifact_adapter.artifact_root_sha256
                    if artifact_adapter is not None
                    else None,
                    "artifact_replay_contract_sha256": artifact_adapter.contract_sha256
                    if artifact_adapter is not None
                    else None,
                    "universe_sha256": pit_universe.universe_sha256,
                    "calendar_sha256": pit_universe.calendar_sha256,
                    "source_manifest_sha256": pit_universe.source_manifest_sha256,
                    "universe_source_manifest_sha256": pit_universe.source_manifest_sha256,
                    "coverage_audit_sha256": getattr(
                        pit_universe, "coverage_audit_sha256", None
                    ),
                    "external_temporal_authority_verified": (
                        audited_development_integrity
                    ),
                    "temporal_contract_sha256": (
                        pit_universe.temporal_contract_sha256
                        if audited_development_integrity
                        else None
                    ),
                    "temporal_role": (
                        pit_universe.temporal_role
                        if audited_development_integrity
                        else None
                    ),
                    "artifact_manifest_sha256": (
                        audited_authority["artifact_manifest_sha256"]
                        if audited_authority is not None
                        else None
                    ),
                    "market_generation_root_sha256": (
                        audited_authority["market_generation_root_sha256"]
                        if audited_authority is not None
                        else None
                    ),
                    "stock_generation_lineage_sha256": (
                        audited_authority["stock_generation_lineage_sha256"]
                        if audited_authority is not None
                        else None
                    ),
                    "audited_authority": audited_authority,
                    **(
                        {
                            "live_proof": False,
                            "composite_authority_schema_version": (
                                pit_universe.composite_authority["schema_version"]
                            ),
                            "composite_authority": pit_universe.composite_authority,
                        }
                        if composite_audited_development_replay
                        else {}
                    ),
                    "known_biases": (
                        [
                            "controlled_direct_transport_not_verified",
                            "independent_exchange_master_not_bound",
                            "historical_backfill_not_contemporaneously_observed",
                            "artifact_not_final_oos_eligible",
                            "intraday_exit_models_not_supported",
                        ]
                        if artifact_adapter is not None
                        else [
                            "daily_universe_source_completeness_unverified",
                            "universe_raw_lineage_unverified",
                            "as_of_adjusted_price_history_not_versioned",
                            "raw_execution_bars_not_bound",
                            "historical_st_and_suspension_intervals_not_bound",
                        ]
                    ),
                }
                if pit_universe is not None
                else {
                    "entry_decision_cutoff": "next_open",
                    "point_in_time": False,
                    "eligible_for_final_validation": False,
                    "known_biases": [
                        "current_snapshot_seed",
                        "current_liquidity_seed",
                        "as_of_adjusted_price_history_not_versioned",
                    ],
                }
            ),
        },
    )
    return result


@wraps(_run_historical_universe_research_backtest_resolved)
def run_historical_universe_research_backtest(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    signature = inspect.signature(_run_historical_universe_research_backtest_resolved)
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    _require_positive_hold_days(bound.arguments["hold_days"])
    if bound.arguments["pit_universe_path"]:
        raise ValueError("legacy PIT universe is not allowed for frozen development")
    audited_path = bound.arguments["audited_pit_universe_path"]
    composite_path = bound.arguments["composite_pit_descriptor_path"]
    if audited_path and composite_path:
        raise ValueError("only one audited or composite PIT artifact may be used")
    if not audited_path and not composite_path:
        raise ValueError("historical backtest requires a PIT artifact")
    if audited_path and not bound.arguments["expected_artifact_root_sha256"]:
        raise ValueError("audited backtest requires expected_artifact_root_sha256")
    if composite_path and not bound.arguments["expected_composite_root_sha256"]:
        raise ValueError("composite backtest requires expected_composite_root_sha256")
    if composite_path and bound.arguments["expected_artifact_root_sha256"]:
        raise ValueError("composite backtest forbids a single artifact root anchor")
    if audited_path and bound.arguments["expected_composite_root_sha256"]:
        raise ValueError("single artifact backtest forbids a composite root anchor")
    if not bound.arguments["temporal_contract_path"]:
        raise ValueError("audited backtest requires temporal_contract_path")
    if not bound.arguments["end_date"]:
        raise ValueError("audited backtest requires an explicit end_date")
    contract = load_temporal_partition_contract(bound.arguments["temporal_contract_path"])
    if contract["contract_sha256"] != bound.arguments["expected_temporal_contract_sha256"]:
        raise ValueError("expected temporal contract hash mismatch")
    role = bound.arguments["expected_temporal_role"]
    if role != "development":
        raise ValueError("ordinary backtest requires temporal role development")
    assert_range_allowed(
        contract,
        role,
        bound.arguments["start_date"],
        bound.arguments["end_date"],
        "backtest",
    )
    universe_items, pit_universe = _resolve_historical_universe(
        bound.arguments["settings"],
        use_live_snapshot=bound.arguments["use_live_snapshot"],
        max_universe_symbols=bound.arguments["max_universe_symbols"],
        start_date=bound.arguments["start_date"],
        end_date=bound.arguments["end_date"],
        pit_universe_path=bound.arguments["pit_universe_path"],
        audited_pit_universe_path=audited_path,
        composite_pit_descriptor_path=composite_path,
        expected_coverage_audit_sha256=bound.arguments[
            "expected_coverage_audit_sha256"
        ],
        expected_artifact_root_sha256=bound.arguments["expected_artifact_root_sha256"],
        expected_composite_root_sha256=bound.arguments[
            "expected_composite_root_sha256"
        ],
        expected_temporal_contract_sha256=bound.arguments[
            "expected_temporal_contract_sha256"
        ],
        expected_temporal_role=role,
    )
    try:
        return _run_historical_universe_research_backtest_resolved(
            *args,
            **kwargs,
            _resolved_universe_items=universe_items,
            _resolved_pit_universe=pit_universe,
        )
    finally:
        if getattr(pit_universe, "is_audited_store_artifact", False):
            pit_universe.close()
