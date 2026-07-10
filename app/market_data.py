import os
import sqlite3
from datetime import datetime, time as day_time, timedelta
from pathlib import Path
from threading import RLock
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd

from app.akshare_client import akshare_call
from app.trading_calendar import is_trade_day, latest_trade_date_on_or_before, previous_trade_date


class MarketDataError(RuntimeError):
    pass


class CachedFrame:
    def __init__(self, frame: pd.DataFrame, expires_at: datetime, source: str) -> None:
        self.frame = frame
        self.expires_at = expires_at
        self.source = source


def normalize_symbol(symbol: str, market: str) -> str:
    cleaned = symbol.strip().upper()
    cleaned = cleaned.replace(".SH", "").replace(".SZ", "")
    cleaned = cleaned.replace("SH", "").replace("SZ", "")
    return cleaned


def _sina_a_symbol(symbol: str) -> str:
    if symbol.startswith(("6", "5", "9")):
        return "sh%s" % symbol
    if symbol.startswith(("4", "8")):
        return "bj%s" % symbol
    return "sz%s" % symbol


def _sina_etf_symbol(symbol: str) -> str:
    return "sh%s" % symbol if symbol.startswith("5") else "sz%s" % symbol


def _tushare_ts_code(symbol: str) -> str:
    if symbol.startswith(("6", "5", "9")):
        return "%s.SH" % symbol
    if symbol.startswith(("4", "8")):
        return "%s.BJ" % symbol
    return "%s.SZ" % symbol


def _today() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _date_strings(lookback_days: int) -> Tuple[str, str]:
    end_date = _today().date()
    calendar_days = max(lookback_days * 2, 260)
    start_date = end_date - timedelta(days=calendar_days)
    return start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d")


def _load_akshare():
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise MarketDataError(
            "akshare is not installed. Run: pip install -r requirements.txt"
        ) from exc
    return ak


def _load_tushare():
    try:
        import tushare as ts  # type: ignore
    except ImportError as exc:
        raise MarketDataError(
            "tushare is not installed. Run: pip install -r requirements.txt"
        ) from exc
    return ts


def _request_timeout_seconds() -> float:
    raw = os.getenv("MARKET_DATA_TIMEOUT_SECONDS", "8")
    try:
        return max(float(raw), 1.0)
    except ValueError:
        return 8.0


def _normalize_frame(raw: pd.DataFrame, symbol: str, market: str) -> pd.DataFrame:
    if raw is None or raw.empty:
        raise MarketDataError("No market data returned for %s:%s" % (market, symbol))

    column_map = {
        "日期": "date",
        "时间": "date",
        "trade_date": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "vol": "volume",
        "成交额": "amount",
        "振幅": "amplitude",
        "涨跌幅": "change_pct",
        "涨跌额": "change_amount",
        "换手率": "turnover",
    }
    frame = raw.rename(columns={key: value for key, value in column_map.items() if key in raw.columns})
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise MarketDataError(
            "Market data for %s:%s is missing columns: %s" % (market, symbol, ", ".join(missing))
        )

    frame = frame[["date", "open", "high", "low", "close", "volume"] + [c for c in ["amount"] if c in frame]]
    frame["date"] = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame = frame.dropna(subset=["date", "open", "high", "low", "close"])
    frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    if len(frame) < 60:
        raise MarketDataError(
            "Only %s rows returned for %s:%s; at least 60 daily bars are needed."
            % (len(frame), market, symbol)
        )
    return frame.reset_index(drop=True)


def _trim_frame(frame: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    start_iso = "%s-%s-%s" % (start_date[:4], start_date[4:6], start_date[6:8])
    end_iso = "%s-%s-%s" % (end_date[:4], end_date[4:6], end_date[6:8])
    trimmed = frame[(frame["date"] >= start_iso) & (frame["date"] <= end_iso)].reset_index(drop=True)
    if len(trimmed) < 60:
        raise MarketDataError("Only %s rows remained after date filtering." % len(trimmed))
    return trimmed


class AkshareDataProvider:
    def __init__(
        self,
        cache_ttl_seconds: int = 1800,
        disk_cache_path: Optional[str] = None,
        enable_mootdx_daily_fallback: bool = False,
        mootdx_daily=None,
        mootdx_servers: str = "",
        mootdx_timeout_seconds: float = 3.0,
        mootdx_daily_max_pages: int = 3,
        mootdx_daily_max_elapsed_seconds: float = 12.0,
    ) -> None:
        self.cache_ttl_seconds = cache_ttl_seconds
        self.disk_cache_path = disk_cache_path or os.getenv("MARKET_DATA_CACHE_PATH", "")
        self._cache: Dict[Tuple[str, str, int, str], CachedFrame] = {}
        self._memory_lock = RLock()
        self._disk_lock = RLock()
        self.enable_mootdx_daily_fallback = bool(enable_mootdx_daily_fallback)
        self.mootdx_daily = mootdx_daily
        if self.enable_mootdx_daily_fallback and self.mootdx_daily is None:
            from app.mootdx_daily import MootdxDailyProvider

            self.mootdx_daily = MootdxDailyProvider(
                servers=mootdx_servers,
                timeout_seconds=mootdx_timeout_seconds,
                max_pages=mootdx_daily_max_pages,
                max_elapsed_seconds=mootdx_daily_max_elapsed_seconds,
            )

    def history(
        self,
        symbol: str,
        market: str,
        lookback_days: int = 360,
        adjust: str = "qfq",
    ) -> Tuple[pd.DataFrame, str]:
        normalized_symbol = normalize_symbol(symbol, market)
        adjust_key = adjust if adjust in {"", "qfq", "hfq"} else "qfq"
        key = (market, normalized_symbol, lookback_days, adjust_key)
        now = _today()
        with self._memory_lock:
            cached = self._cache.get(key)
            if cached and cached.expires_at > now:
                return cached.frame.copy(), cached.source

        start_date, end_date = _date_strings(lookback_days)
        cached_frame = self._read_disk_cache(
            normalized_symbol,
            market,
            adjust_key,
            start_date,
            end_date,
            require_fresh=True,
        )
        if cached_frame is not None:
            source = "SQLite daily cache"
            self._set_memory_cache(key, cached_frame, source, now)
            return cached_frame.copy(), source

        stale_frame = self._read_disk_cache(
            normalized_symbol,
            market,
            adjust_key,
            start_date,
            end_date,
            require_fresh=False,
        )
        try:
            frame, source = self._fetch(normalized_symbol, market, lookback_days, adjust)
        except Exception as primary_error:
            fallback_error = None
            if self.enable_mootdx_daily_fallback and self.mootdx_daily is not None:
                try:
                    frame, source = self._mootdx_fallback(
                        normalized_symbol,
                        market,
                        adjust_key,
                        start_date,
                        end_date,
                        stale_frame,
                    )
                except MarketDataError as exc:
                    fallback_error = exc
                else:
                    self._write_disk_cache(normalized_symbol, market, adjust_key, frame, source)
                    self._set_memory_cache(key, frame, source, now)
                    return frame.copy(), source
            if stale_frame is not None:
                source = "SQLite daily cache stale fallback"
                self._set_memory_cache(key, stale_frame, source, now)
                return stale_frame.copy(), source
            if fallback_error is not None:
                raise fallback_error from primary_error
            raise

        self._write_disk_cache(normalized_symbol, market, adjust_key, frame, source)
        self._set_memory_cache(key, frame, source, now)
        return frame, source

    def _mootdx_fallback(
        self,
        symbol: str,
        market: str,
        adjust: str,
        start_date: str,
        end_date: str,
        cached_frame: Optional[pd.DataFrame],
    ) -> Tuple[pd.DataFrame, str]:
        start_iso = "%s-%s-%s" % (start_date[:4], start_date[4:6], start_date[6:8])
        end_iso = "%s-%s-%s" % (end_date[:4], end_date[4:6], end_date[6:8])
        if adjust == "hfq":
            raise MarketDataError("MOOTDX hfq fallback is intentionally unsupported")
        if adjust == "":
            frame = self.mootdx_daily.history(symbol, start_iso, end_iso)
            return _trim_frame(_normalize_frame(frame, symbol, market), start_date, end_date), "MOOTDX raw daily fallback"
        if cached_frame is None or cached_frame.empty:
            raise MarketDataError("MOOTDX qfq fallback requires trusted qfq cache")

        latest_cached = str(cached_frame["date"].max())[:10]
        actions = self.mootdx_daily.corporate_actions(symbol, latest_cached)
        def nonzero(value) -> bool:
            try:
                return not pd.isna(value) and float(value) != 0
            except (TypeError, ValueError):
                return False

        material = [
            item
            for item in actions
            if int(item.get("category") or 0) in {1, 11}
            or any(nonzero(item.get(key)) for key in ("fenhong", "peigu", "songzhuangu", "suogu"))
        ]
        if material:
            raise MarketDataError("MOOTDX qfq merge blocked by corporate action after trusted cache")

        raw = self.mootdx_daily.history(symbol, latest_cached, end_iso)
        required = {"date", "open", "high", "low", "close", "volume"}
        if raw is None or raw.empty or not required.issubset(raw.columns):
            raise MarketDataError("MOOTDX incremental rows are missing required fields")
        normalized = raw.copy()
        normalized["date"] = pd.to_datetime(normalized["date"]).dt.strftime("%Y-%m-%d")
        newer = normalized[normalized["date"] > latest_cached]
        if newer.empty:
            raise MarketDataError("MOOTDX returned no bars newer than trusted qfq cache")
        previous_close = float(cached_frame.iloc[-1]["close"])
        first_close = float(newer.iloc[0]["close"])
        if previous_close <= 0 or abs(first_close / previous_close - 1) > 0.25:
            raise MarketDataError("MOOTDX qfq merge failed price continuity validation")
        combined = pd.concat([cached_frame, newer], ignore_index=True)
        combined = _normalize_frame(combined, symbol, market)
        return _trim_frame(combined, start_date, end_date), "MOOTDX incremental qfq fallback"

    def _set_memory_cache(
        self,
        key: Tuple[str, str, int, str],
        frame: pd.DataFrame,
        source: str,
        now: datetime,
    ) -> None:
        expires_at = now + timedelta(seconds=self.cache_ttl_seconds)
        with self._memory_lock:
            self._cache[key] = CachedFrame(frame.copy(), expires_at, source)

    def _connect_cache(self):
        if not self.disk_cache_path:
            return None
        path = Path(self.disk_cache_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_bars (
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                adjust TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                amount REAL,
                source TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (market, symbol, adjust, date)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_daily_bars_lookup
            ON daily_bars (market, symbol, adjust, date)
            """
        )
        return conn

    def _minimum_fresh_cache_date(self) -> Optional[str]:
        now = _today()
        today = now.date()
        try:
            if is_trade_day(today):
                if now.time() >= day_time(9, 30):
                    return today.isoformat()
                previous = previous_trade_date(today)
                return previous.isoformat() if previous else None
            latest = latest_trade_date_on_or_before(today)
            return latest.isoformat() if latest else None
        except Exception:
            return None

    def _read_disk_cache(
        self,
        symbol: str,
        market: str,
        adjust: str,
        start_date: str,
        end_date: str,
        require_fresh: bool,
    ) -> Optional[pd.DataFrame]:
        if not self.disk_cache_path:
            return None
        start_iso = "%s-%s-%s" % (start_date[:4], start_date[4:6], start_date[6:8])
        end_iso = "%s-%s-%s" % (end_date[:4], end_date[4:6], end_date[6:8])
        with self._disk_lock:
            conn = self._connect_cache()
            if conn is None:
                return None
            try:
                rows = conn.execute(
                    """
                    SELECT date, open, high, low, close, volume, amount
                    FROM daily_bars
                    WHERE market = ? AND symbol = ? AND adjust = ? AND date >= ? AND date <= ?
                    ORDER BY date
                    """,
                    (market, symbol, adjust, start_iso, end_iso),
                ).fetchall()
            finally:
                conn.close()
        if not rows:
            return None
        frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
        if len(frame) < 60:
            return None
        if require_fresh:
            minimum_date = self._minimum_fresh_cache_date()
            if minimum_date and str(frame["date"].max()) < minimum_date:
                return None
        try:
            return _trim_frame(frame, start_date, end_date)
        except MarketDataError:
            return None

    def _write_disk_cache(
        self,
        symbol: str,
        market: str,
        adjust: str,
        frame: pd.DataFrame,
        source: str,
    ) -> None:
        if not self.disk_cache_path or frame is None or frame.empty:
            return
        updated_at = _today().isoformat()
        rows = []
        for row in frame.to_dict(orient="records"):
            rows.append(
                (
                    market,
                    symbol,
                    adjust,
                    str(row["date"])[:10],
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row.get("volume", 0) or 0),
                    float(row["amount"]) if row.get("amount") is not None and not pd.isna(row.get("amount")) else None,
                    source,
                    updated_at,
                )
            )
        with self._disk_lock:
            conn = self._connect_cache()
            if conn is None:
                return
            try:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO daily_bars (
                        market, symbol, adjust, date, open, high, low, close,
                        volume, amount, source, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.commit()
            finally:
                conn.close()

    def _fetch(
        self,
        symbol: str,
        market: str,
        lookback_days: int,
        adjust: str,
    ) -> Tuple[pd.DataFrame, str]:
        start_date, end_date = _date_strings(lookback_days)
        ak = _load_akshare()
        adjust_value = adjust if adjust in {"", "qfq", "hfq"} else "qfq"

        try:
            if market == "a":
                raw = akshare_call(
                    "stock_zh_a_hist",
                    lambda: ak.stock_zh_a_hist(
                        symbol=symbol,
                        period="daily",
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust_value,
                        timeout=_request_timeout_seconds(),
                    ),
                )
                source = "AKShare stock_zh_a_hist"
            elif market == "etf":
                raw = akshare_call(
                    "fund_etf_hist_em",
                    lambda: ak.fund_etf_hist_em(
                        symbol=symbol,
                        period="daily",
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust_value,
                    ),
                )
                source = "AKShare fund_etf_hist_em"
            else:
                raise MarketDataError("Unsupported market: %s" % market)
        except Exception as exc:
            if isinstance(exc, MarketDataError):
                raise
            raw, source = self._fetch_fallback(ak, symbol, market, start_date, end_date, adjust_value, exc)

        frame = _normalize_frame(raw, symbol, market)
        return _trim_frame(frame, start_date, end_date), source

    def _fetch_fallback(
        self,
        ak,
        symbol: str,
        market: str,
        start_date: str,
        end_date: str,
        adjust_value: str,
        previous_error,
    ):
        try:
            if market == "a":
                raw = akshare_call(
                    "stock_zh_a_daily",
                    lambda: ak.stock_zh_a_daily(
                        symbol=_sina_a_symbol(symbol),
                        start_date=start_date,
                        end_date=end_date,
                        adjust=adjust_value,
                    ),
                    attempts=2,
                )
                return raw, "AKShare stock_zh_a_daily fallback"
            if market == "etf":
                raw = akshare_call(
                    "fund_etf_hist_sina",
                    lambda: ak.fund_etf_hist_sina(symbol=_sina_etf_symbol(symbol)),
                    attempts=2,
                )
                return raw, "AKShare fund_etf_hist_sina fallback"
            raise MarketDataError("Unsupported market: %s" % market)
        except Exception as exc:
            if isinstance(exc, MarketDataError):
                raise
            raise MarketDataError(
                "Failed to fetch %s:%s: primary=%s; fallback=%s" % (market, symbol, previous_error, exc)
            )


class TushareDataProvider(AkshareDataProvider):
    def __init__(
        self,
        cache_ttl_seconds: int = 1800,
        disk_cache_path: Optional[str] = None,
        fallback_to_akshare: bool = True,
        token: str = "",
    ) -> None:
        super().__init__(cache_ttl_seconds=cache_ttl_seconds, disk_cache_path=disk_cache_path)
        self.fallback_to_akshare = fallback_to_akshare
        self.token = token or os.getenv("TUSHARE_TOKEN", "")

    def _fetch(
        self,
        symbol: str,
        market: str,
        lookback_days: int,
        adjust: str,
    ) -> Tuple[pd.DataFrame, str]:
        start_date, end_date = _date_strings(lookback_days)
        ts_code = _tushare_ts_code(symbol)
        adjust_value = adjust if adjust in {"", "qfq", "hfq"} else "qfq"
        try:
            if not self.token:
                raise MarketDataError("TUSHARE_TOKEN is required when MARKET_DATA_PROVIDER=tushare")
            ts = _load_tushare()
            ts.set_token(self.token)
            pro_api = ts.pro_api(self.token)
            raw = ts.pro_bar(
                ts_code=ts_code,
                api=pro_api,
                start_date=start_date,
                end_date=end_date,
                freq="D",
                asset="E",
                adj=adjust_value,
            )
            frame = _normalize_frame(raw, symbol, market)
            return _trim_frame(frame, start_date, end_date), "Tushare pro_bar"
        except Exception as exc:
            if not self.fallback_to_akshare:
                if isinstance(exc, MarketDataError):
                    raise
                raise MarketDataError("Failed to fetch Tushare data for %s:%s: %s" % (market, symbol, exc)) from exc
            try:
                return super()._fetch(symbol, market, lookback_days, adjust)
            except MarketDataError as fallback_exc:
                raise MarketDataError(
                    "Failed to fetch %s:%s from Tushare (%s) and AKShare fallback (%s)"
                    % (market, symbol, exc, fallback_exc)
                ) from fallback_exc


def build_market_data_provider(
    provider_name: str,
    cache_ttl_seconds: int,
    disk_cache_path: str,
    tushare_fallback_to_akshare: bool = True,
    tushare_token: str = "",
    enable_mootdx_daily_fallback: bool = False,
    mootdx_servers: str = "",
    mootdx_timeout_seconds: float = 3.0,
    mootdx_daily_max_pages: int = 3,
    mootdx_daily_max_elapsed_seconds: float = 12.0,
):
    normalized = (provider_name or "akshare").strip().lower()
    if normalized == "akshare":
        return AkshareDataProvider(
            cache_ttl_seconds=cache_ttl_seconds,
            disk_cache_path=disk_cache_path,
            enable_mootdx_daily_fallback=enable_mootdx_daily_fallback,
            mootdx_servers=mootdx_servers,
            mootdx_timeout_seconds=mootdx_timeout_seconds,
            mootdx_daily_max_pages=mootdx_daily_max_pages,
            mootdx_daily_max_elapsed_seconds=mootdx_daily_max_elapsed_seconds,
        )
    if normalized == "tushare":
        return TushareDataProvider(
            cache_ttl_seconds=cache_ttl_seconds,
            disk_cache_path=disk_cache_path,
            fallback_to_akshare=tushare_fallback_to_akshare,
            token=tushare_token,
        )
    raise MarketDataError("Unsupported MARKET_DATA_PROVIDER: %s" % provider_name)


def frame_to_points(frame: pd.DataFrame, limit: Optional[int] = None):
    output = frame.tail(limit).copy() if limit else frame.copy()
    records = []
    for row in output.to_dict(orient="records"):
        records.append(
            {
                "date": row["date"],
                "open": round(float(row["open"]), 4),
                "high": round(float(row["high"]), 4),
                "low": round(float(row["low"]), 4),
                "close": round(float(row["close"]), 4),
                "volume": float(row.get("volume", 0) or 0),
            }
        )
    return records
