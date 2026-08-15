from typing import Iterable

import numpy as np
import pandas as pd


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _sma_tdx(series: pd.Series, n: int = 3) -> pd.Series:
    """Tongdaxin SMA(X,N,1): alpha = 1/N recursive average."""

    return series.ewm(alpha=1 / n, adjust=False).mean()


def _kdj(df: pd.DataFrame, window: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Textbook China KDJ (9,3,3)."""

    lowest = df["low"].rolling(window, min_periods=window).min()
    highest = df["high"].rolling(window, min_periods=window).max()
    denom = (highest - lowest).replace(0, np.nan)
    rsv = (df["close"] - lowest) / denom * 100.0
    k = _sma_tdx(rsv, 3)
    d = _sma_tdx(k, 3)
    j = 3.0 * k - 2.0 * d
    return k, d, j


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)


def _true_range(df: pd.DataFrame) -> pd.Series:
    previous_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - previous_close).abs(),
            (df["low"] - previous_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add common daily technical indicators used by the signal engine."""
    enriched = df.copy()
    close = enriched["close"]

    for window in (5, 10, 20, 60, 120):
        enriched["ma%s" % window] = close.rolling(window, min_periods=max(3, window // 3)).mean()

    enriched["ema12"] = _ema(close, 12)
    enriched["ema26"] = _ema(close, 26)
    enriched["macd"] = enriched["ema12"] - enriched["ema26"]
    enriched["macd_signal"] = _ema(enriched["macd"], 9)
    enriched["macd_hist"] = enriched["macd"] - enriched["macd_signal"]
    enriched["rsi14"] = _rsi(close, 14)

    middle = enriched["ma20"]
    std20 = close.rolling(20, min_periods=10).std()
    enriched["boll_upper"] = middle + 2 * std20
    enriched["boll_lower"] = middle - 2 * std20
    kdj_k, kdj_d, kdj_j = _kdj(enriched)
    enriched["kdj_k"] = kdj_k
    enriched["kdj_d"] = kdj_d
    enriched["kdj_j"] = kdj_j

    enriched["tr"] = _true_range(enriched)
    enriched["atr14"] = enriched["tr"].rolling(14, min_periods=7).mean()
    enriched["high20_prev"] = enriched["high"].rolling(20, min_periods=10).max().shift(1)
    enriched["low20_prev"] = enriched["low"].rolling(20, min_periods=10).min().shift(1)
    enriched["volume_ma20"] = enriched["volume"].rolling(20, min_periods=10).mean()
    enriched["volume_ratio"] = enriched["volume"] / enriched["volume_ma20"].replace(0, np.nan)
    enriched["return_20d"] = close.pct_change(20)
    enriched["return_60d"] = close.pct_change(60)
    enriched["volatility_20d"] = close.pct_change().rolling(20, min_periods=10).std() * np.sqrt(252)
    enriched["drawdown_60d"] = close / close.rolling(60, min_periods=20).max() - 1
    return enriched


def ensure_indicator_columns(df: pd.DataFrame, required: Iterable[str]) -> pd.DataFrame:
    missing = [column for column in required if column not in df.columns]
    if missing:
        return add_indicators(df)
    return df.copy()

