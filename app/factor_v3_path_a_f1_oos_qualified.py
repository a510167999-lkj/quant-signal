"""Path A F1: generate post-train OOS qualified trades (shadow diagnostic).

Uses train-store membership frozen at train end + stitched daily bars from
train market store and path_a OOS market store. Does NOT write into the train
qualified cache. Not formal final-OOS; survivorship/membership freeze caveats
apply.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from app import factor_v3_train_window_freeze_contract as freeze
from app.a_share_universe import _is_excluded_name
from app.config import Settings
from app.research_backtest import _run_historical_universe_research_backtest_resolved
from app.research_scope import is_mainboard_chinext_symbol

OOS_COLLECTION_START = "2026-07-04"
DEFAULT_TRAIN_STORE = Path("data/research_pit_store/current_pool_market_v2")
DEFAULT_OOS_STORE = Path("data/research_pit_store/path_a_oos_market_full")
DEFAULT_OUTPUT = Path("data/research_cache/path_a_oos/qualified_hold5_stop5_oos.json")
DEFAULT_MEMBERSHIP_ASOF = freeze.TRAIN_INCLUSIVE_SESSION_END  # 2026-07-03
# Prior-history window for quality stats (membership seed frozen; dates open).
DEFAULT_PRIOR_HISTORY_START = "2025-07-01"

# Tushare daily amount is 千元; research scan thresholds use 元.
_AMOUNT_YUAN_SCALE = 1000.0
# Tushare vol is 手; research volume often in shares.
_VOLUME_SHARE_SCALE = 100.0


class PathAF1QualifiedError(ValueError):
    """Raised when OOS qualified generation fails closed."""


class PathAShadowOosUniverse:
    """Minimal PIT-like universe for Path-A shadow OOS (not audited formal)."""

    is_audited_store_artifact = False

    def __init__(
        self,
        *,
        sessions: list[str],
        membership: dict[str, dict[str, Any]],
        oos_start_date: str,
        end_date: str,
        membership_as_of: str,
        prior_history_start: str,
    ) -> None:
        if not sessions:
            raise PathAF1QualifiedError("no OOS open sessions")
        self._sessions = sorted(set(sessions))
        self._membership = dict(membership)
        # Membership list frozen at membership_as_of. Eligibility window starts at
        # prior_history_start so quality priors can mature before OOS signals.
        self.membership_as_of = str(membership_as_of)[:10]
        self.prior_history_start = str(prior_history_start)[:10]
        self.oos_start_date = str(oos_start_date)[:10]
        self.start_date = self.prior_history_start
        self.end_date = str(end_date)[:10]
        seed = {
            "sessions": self._sessions,
            "symbols": sorted(self._membership.keys()),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "oos_start_date": self.oos_start_date,
            "membership_as_of": self.membership_as_of,
            "prior_history_start": self.prior_history_start,
        }
        digest = hashlib.sha256(
            json.dumps(
                seed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        self.universe_sha256 = digest
        self.calendar_sha256 = hashlib.sha256(
            ",".join(self._sessions).encode("utf-8")
        ).hexdigest()
        self.source_manifest_sha256 = digest

    def open_sessions(self, start_date: Any, end_date: Any) -> list[str]:
        first = str(start_date)[:10]
        last = str(end_date)[:10]
        if last < first:
            raise ValueError("end_date precedes start_date")
        return [s for s in self._sessions if first <= s <= last]

    def items_as_of(self, signal_date: Any) -> list[dict[str, Any]]:
        day = str(signal_date)[:10]
        if day < self.prior_history_start or day > self.end_date:
            raise ValueError(f"date outside shadow OOS universe: {day}")
        return list(self._membership.values())

    def item_as_of(self, symbol: Any, signal_date: Any) -> dict[str, Any] | None:
        day = str(signal_date)[:10]
        if day < self.prior_history_start or day > self.end_date:
            return None
        return self._membership.get(str(symbol))

    def seed_items(self, start_date: Any, end_date: Any) -> list[dict[str, Any]]:
        _ = start_date, end_date
        return list(self._membership.values())


class StoreStitchedHistoryProvider:
    """history() backed by train+OOS market stores (equity only)."""

    def __init__(self, bars_by_symbol: dict[str, pd.DataFrame]) -> None:
        self._bars = bars_by_symbol

    def history(
        self,
        symbol: str,
        market: str,
        lookback_days: int = 360,
        adjust: str = "qfq",
    ) -> tuple[pd.DataFrame, str]:
        _ = market, lookback_days, adjust
        code = str(symbol).strip().split(".")[0]
        frame = self._bars.get(code)
        if frame is None or frame.empty:
            raise ValueError(f"no stitched bars for {symbol}")
        return frame.copy(), "path_a_train_oos_store_stitch"


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _connect(store: Path) -> sqlite3.Connection:
    meta = store / "metadata.sqlite3"
    if not meta.is_file():
        raise PathAF1QualifiedError(f"store missing: {meta}")
    return sqlite3.connect(f"file:{meta.as_posix()}?mode=ro", uri=True)


def _oos_sessions(oos_store: Path) -> list[str]:
    con = _connect(oos_store)
    try:
        rows = con.execute(
            """
            SELECT trade_date FROM market_session_generation_head
            ORDER BY trade_date
            """
        ).fetchall()
    finally:
        con.close()
    return [str(r[0])[:10] for r in rows]


def _seed_membership(
    train_store: Path,
    *,
    as_of: str,
    max_symbols: int,
) -> list[dict[str, Any]]:
    con = _connect(train_store)
    try:
        rows = con.execute(
            """
            SELECT u.ts_code, u.name, u.exchange, u.industry, d.amount
            FROM daily_universe u
            JOIN market_session_generation_head h
              ON h.trade_date = u.trade_date
            LEFT JOIN market_session_generation_rows_daily d
              ON d.generation_id = h.generation_id AND d.ts_code = u.ts_code
            WHERE u.trade_date = ?
            """,
            (as_of,),
        ).fetchall()
    finally:
        con.close()
    items: list[dict[str, Any]] = []
    for ts_code, name, exchange, industry, amount in rows:
        code = str(ts_code or "").split(".")[0]
        if not is_mainboard_chinext_symbol(code):
            continue
        if _is_excluded_name(str(name or "")):
            continue
        items.append(
            {
                "symbol": code,
                "ts_code": str(ts_code),
                "name": str(name or ""),
                "market": "a",
                "exchange": str(exchange or ""),
                "industry": str(industry or ""),
                "amount": float(amount or 0.0) * _AMOUNT_YUAN_SCALE,
            }
        )
    items.sort(key=lambda row: float(row.get("amount") or 0.0), reverse=True)
    if max_symbols > 0:
        items = items[:max_symbols]
    if not items:
        raise PathAF1QualifiedError(f"no membership seed on {as_of}")
    return items


def _load_bars_for_codes(
    store: Path,
    ts_codes: list[str],
) -> dict[str, list[dict[str, Any]]]:
    if not ts_codes:
        return {}
    con = _connect(store)
    try:
        # chunk IN clauses
        out: dict[str, list[dict[str, Any]]] = {}
        chunk = 400
        for i in range(0, len(ts_codes), chunk):
            part = ts_codes[i : i + chunk]
            placeholders = ",".join("?" for _ in part)
            sql = f"""
                SELECT r.ts_code, r.trade_date, r.open, r.high, r.low, r.close,
                       r.vol, r.amount
                FROM market_session_generation_rows_daily r
                JOIN market_session_generation_head h
                  ON h.generation_id = r.generation_id
                WHERE r.ts_code IN ({placeholders})
                ORDER BY r.ts_code, r.trade_date
            """
            for row in con.execute(sql, part):
                ts_code, trade_date, o, h, l, c, vol, amount = row
                code = str(ts_code).split(".")[0]
                out.setdefault(code, []).append(
                    {
                        "date": str(trade_date)[:10],
                        "open": float(o),
                        "high": float(h),
                        "low": float(l),
                        "close": float(c),
                        "volume": float(vol or 0.0) * _VOLUME_SHARE_SCALE,
                        "amount": float(amount or 0.0) * _AMOUNT_YUAN_SCALE,
                    }
                )
    finally:
        con.close()
    return out


def _stitch_frames(
    train_bars: dict[str, list[dict[str, Any]]],
    oos_bars: dict[str, list[dict[str, Any]]],
    codes: list[str],
    *,
    min_rows: int,
) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for code in codes:
        by_date: dict[str, dict[str, Any]] = {}
        for row in train_bars.get(code) or []:
            by_date[row["date"]] = row
        for row in oos_bars.get(code) or []:
            by_date[row["date"]] = row  # OOS overwrites same day if any
        if len(by_date) < min_rows:
            continue
        ordered = [by_date[d] for d in sorted(by_date)]
        frame = pd.DataFrame(ordered)
        frames[code] = frame
    return frames


def generate_path_a_oos_qualified_trades(
    *,
    repo_root: Path | None = None,
    train_store: Path | None = None,
    oos_store: Path | None = None,
    output_path: Path | None = None,
    max_universe_symbols: int = 300,
    max_deep: int = 80,
    top_n: int = 3,
    hold_days: int = 5,
    stop_loss_pct: float = 5.0,
    symbol_cooldown_days: int = 5,
    max_active_positions: int = 3,
    lookback_days: int = 620,
    progress_every: int = 50,
) -> dict[str, Any]:
    """Build OOS qualified trades and write outside the train cache."""

    root = (repo_root or Path.cwd()).resolve()
    train_path = (train_store or (root / DEFAULT_TRAIN_STORE)).resolve()
    oos_path = (oos_store or (root / DEFAULT_OOS_STORE)).resolve()
    out_path = (output_path or (root / DEFAULT_OUTPUT)).resolve()

    freeze.assert_train_window_freeze_consistent()
    if out_path.resolve() == (root / "data/research_cache/qualified_hold5_stop5.json").resolve():
        raise PathAF1QualifiedError("refuse to overwrite train qualified_hold5_stop5.json")

    sessions = _oos_sessions(oos_path)
    sessions = [s for s in sessions if s >= OOS_COLLECTION_START]
    if not sessions:
        raise PathAF1QualifiedError("no OOS sessions in store")
    oos_start = sessions[0]
    oos_end = sessions[-1]

    seeds = _seed_membership(
        train_path,
        as_of=DEFAULT_MEMBERSHIP_ASOF,
        max_symbols=max_universe_symbols,
    )
    ts_codes = [str(s["ts_code"]) for s in seeds]
    codes = [str(s["symbol"]) for s in seeds]

    train_bars = _load_bars_for_codes(train_path, ts_codes)
    oos_bars = _load_bars_for_codes(oos_path, ts_codes)
    # Need warmup (~90) + OOS window; require at least 120 rows.
    frames = _stitch_frames(train_bars, oos_bars, codes, min_rows=120)
    if len(frames) < 50:
        raise PathAF1QualifiedError(
            f"too few stitched histories with enough bars: {len(frames)}"
        )

    # Drop seeds without history
    seeds = [s for s in seeds if s["symbol"] in frames]
    membership = {s["symbol"]: s for s in seeds}
    universe = PathAShadowOosUniverse(
        sessions=sessions,
        membership=membership,
        oos_start_date=oos_start,
        end_date=oos_end,
        membership_as_of=DEFAULT_MEMBERSHIP_ASOF,
        prior_history_start=DEFAULT_PRIOR_HISTORY_START,
    )
    provider = StoreStitchedHistoryProvider(frames)
    settings = Settings()

    # Start earlier than OOS so prior_outcomes / quality can mature; export
    # still hard-filters signal_date to post-train OOS only.
    research_start = DEFAULT_PRIOR_HISTORY_START

    payload = _run_historical_universe_research_backtest_resolved(
        settings=settings,
        provider=provider,  # type: ignore[arg-type]
        start_date=research_start,
        end_date=oos_end,
        max_deep=max_deep,
        top_n=top_n,
        hold_days=hold_days,
        lookback_days=lookback_days,
        max_universe_symbols=max_universe_symbols,
        use_live_snapshot=False,
        cache_dir=str((root / "data/research_cache/path_a_oos").resolve()),
        progress_every=progress_every,
        stop_loss_pct=stop_loss_pct,
        symbol_cooldown_days=symbol_cooldown_days,
        max_active_positions=max_active_positions,
        include_qualified_trades=True,
        _resolved_universe_items=seeds,
        _resolved_pit_universe=universe,
    )

    trades = list(payload.get("qualified_trades") or [])
    # Hard filter: only post-train signal dates
    oos_trades = [
        t
        for t in trades
        if str(t.get("signal_date") or "")[:10] >= OOS_COLLECTION_START
        and str(t.get("signal_date") or "")[:10] > freeze.TRAIN_INCLUSIVE_SESSION_END
    ]
    summary = dict(payload.get("summary") or {})
    summary.update(
        {
            "path_a_shadow_oos": True,
            "formal_final_oos": False,
            "train_inclusive_session_end": freeze.TRAIN_INCLUSIVE_SESSION_END,
            "oos_collection_start": OOS_COLLECTION_START,
            "oos_session_start": oos_start,
            "oos_session_end": oos_end,
            "oos_session_count": len(sessions),
            "membership_as_of": DEFAULT_MEMBERSHIP_ASOF,
            "membership_freeze_note": (
                "Membership frozen at train end; OOS days reuse that seed "
                "(survivorship / no delist refresh). Diagnostic only."
            ),
            "history_source": "train_store+oos_store_stitch",
            "raw_qualified_trade_count": len(trades),
            "oos_filtered_trade_count": len(oos_trades),
            "stitched_symbol_count": len(frames),
            "seed_symbol_count": len(seeds),
            "automatic_trading_allowed": False,
            "eligible_for_final_validation": False,
        }
    )
    export = {"summary": summary, "qualified_trades": oos_trades}
    raw = (
        json.dumps(export, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(raw)

    dates = sorted(
        {
            str(t.get("signal_date") or "")[:10]
            for t in oos_trades
            if str(t.get("signal_date") or "")[:10]
        }
    )
    return {
        "ok": True,
        "output_path": str(out_path),
        "file_sha256": _sha_bytes(raw),
        "oos_trade_count": len(oos_trades),
        "raw_trade_count_before_filter": len(trades),
        "signal_day_count": len(dates),
        "first_signal_date": dates[0] if dates else None,
        "last_signal_date": dates[-1] if dates else None,
        "oos_session_start": oos_start,
        "oos_session_end": oos_end,
        "stitched_symbol_count": len(frames),
        "seed_symbol_count": len(seeds),
        "summary_excerpt": {
            "portfolio_compounded_return_pct": summary.get(
                "portfolio_compounded_return_pct"
            ),
            "portfolio_max_drawdown_pct": summary.get("portfolio_max_drawdown_pct"),
            "trade_win_rate_pct": summary.get("trade_win_rate_pct"),
            "selected_trade_count": summary.get("selected_trade_count"),
        },
    }


__all__ = [
    "DEFAULT_OUTPUT",
    "DEFAULT_OOS_STORE",
    "DEFAULT_TRAIN_STORE",
    "OOS_COLLECTION_START",
    "PathAF1QualifiedError",
    "PathAShadowOosUniverse",
    "StoreStitchedHistoryProvider",
    "generate_path_a_oos_qualified_trades",
]
