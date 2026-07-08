import math
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _limit_threshold_pct(symbol: Any) -> float:
    code = str(symbol or "")
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    return 10.0


def _parse_server(value: str) -> Optional[Tuple[str, int]]:
    raw = str(value or "").strip()
    if not raw or ":" not in raw:
        return None
    host, port_text = raw.rsplit(":", 1)
    try:
        return host.strip(), int(port_text)
    except ValueError:
        return None


def _split_symbols(symbols: Iterable[str]) -> List[str]:
    output = []
    seen = set()
    for symbol in symbols:
        code = str(symbol or "").strip()
        if len(code) != 6 or not code.isdigit() or code.startswith(("4", "8", "9")):
            continue
        if code in seen:
            continue
        seen.add(code)
        output.append(code)
    return output


class MootdxL1QuoteProvider:
    def __init__(
        self,
        servers: str = "",
        timeout_seconds: float = 3.0,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self.timeout_seconds = max(float(timeout_seconds or 3.0), 0.5)
        self.servers = [server for server in (_parse_server(item) for item in str(servers or "").split(",")) if server]

    def available(self) -> bool:
        if not self.enabled:
            return False
        try:
            import mootdx  # noqa: F401
        except ImportError:
            return False
        return True

    def _client(self):
        from mootdx.quotes import Quotes

        kwargs = {
            "market": "std",
            "multithread": True,
            "heartbeat": False,
            "timeout": self.timeout_seconds,
            "auto_retry": False,
            "raise_exception": False,
        }
        if self.servers:
            errors = []
            for server in self.servers:
                try:
                    return Quotes.factory(server=server, **kwargs)
                except Exception as exc:
                    errors.append("%s:%s %s" % (server[0], server[1], exc))
            raise RuntimeError("; ".join(errors))
        return Quotes.factory(**kwargs)

    def quotes(self, symbols: Iterable[str]) -> Dict[str, Any]:
        codes = _split_symbols(symbols)
        if not self.available():
            return {"enabled": self.enabled, "available": False, "quotes": {}, "errors": []}
        if not codes:
            return {"enabled": self.enabled, "available": True, "quotes": {}, "errors": []}

        started = time.perf_counter()
        try:
            frame = self._client().quotes(symbol=codes)
        except Exception as exc:
            return {
                "enabled": self.enabled,
                "available": True,
                "quotes": {},
                "errors": [{"source": "mootdx_l1", "message": str(exc)}],
                "elapsed_seconds": round(time.perf_counter() - started, 4),
            }
        quotes = self._normalize_frame(frame)
        return {
            "enabled": self.enabled,
            "available": True,
            "quotes": quotes,
            "errors": [],
            "elapsed_seconds": round(time.perf_counter() - started, 4),
        }

    def _normalize_frame(self, frame: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
        if frame is None or frame.empty:
            return {}
        output = {}
        for row in frame.to_dict(orient="records"):
            code = str(row.get("code") or "").strip()
            if not code:
                continue
            price = _num(row.get("price"))
            previous_close = _num(row.get("last_close"))
            open_price = _num(row.get("open"))
            bid1 = _num(row.get("bid1"))
            ask1 = _num(row.get("ask1"))
            high = _num(row.get("high"))
            low = _num(row.get("low"))
            limit_threshold = _limit_threshold_pct(code)
            change_pct = (price / previous_close - 1) * 100 if price > 0 and previous_close > 0 else None
            open_gap_pct = (open_price / previous_close - 1) * 100 if open_price > 0 and previous_close > 0 else None
            spread_pct = ((ask1 - bid1) / price * 100) if ask1 > 0 and bid1 > 0 and price > 0 else None
            output[code] = {
                "source": "mootdx",
                "symbol": code,
                "market": "a",
                "price": round(price, 4),
                "previous_close": round(previous_close, 4),
                "open": round(open_price, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "change_pct": round(change_pct, 2) if change_pct is not None else None,
                "open_gap_pct": round(open_gap_pct, 2) if open_gap_pct is not None else None,
                "amount": _num(row.get("amount")),
                "volume": _num(row.get("volume"), _num(row.get("vol"))),
                "servertime": row.get("servertime"),
                "bid1": round(bid1, 4),
                "ask1": round(ask1, 4),
                "bid_vol1": _num(row.get("bid_vol1")),
                "ask_vol1": _num(row.get("ask_vol1")),
                "spread_pct": round(spread_pct, 4) if spread_pct is not None else None,
                "limit_threshold_pct": limit_threshold,
                "near_limit_up": bool(change_pct is not None and change_pct >= limit_threshold - 0.7),
                "near_limit_down": bool(change_pct is not None and change_pct <= -limit_threshold + 0.7),
                "tags": self._tags(code, change_pct, open_gap_pct, spread_pct),
            }
        return output

    def _tags(
        self,
        symbol: str,
        change_pct: Optional[float],
        open_gap_pct: Optional[float],
        spread_pct: Optional[float],
    ) -> List[str]:
        tags = []
        limit_threshold = _limit_threshold_pct(symbol)
        if open_gap_pct is not None:
            if 2 <= open_gap_pct <= 5:
                tags.append("l1_open_gap_2_to_5")
            elif open_gap_pct > 5:
                tags.append("l1_open_gap_gt_5")
            elif open_gap_pct < -1:
                tags.append("l1_open_gap_lt_neg1")
        if change_pct is not None:
            if change_pct >= limit_threshold - 0.7:
                tags.append("l1_near_limit_up")
            if change_pct <= -limit_threshold + 0.7:
                tags.append("l1_near_limit_down")
        if spread_pct is not None:
            if spread_pct <= 0.2:
                tags.append("l1_spread_lte_0_2")
            elif spread_pct >= 0.8:
                tags.append("l1_spread_gte_0_8")
        return tags
