from datetime import datetime
import time
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import requests

from app.network import market_data_proxy_scope
from app.storage import read_json, write_json


def _load_akshare():
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise RuntimeError("akshare is not installed. Run: pip install -r requirements.txt") from exc
    return ak


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _market_for_symbol(symbol: str) -> str:
    if str(symbol).startswith(("6", "5", "9")):
        return "sh"
    if str(symbol).startswith(("4", "8")):
        return "bj"
    return "sz"


class FundFlowContextProvider:
    def __init__(self, cache_path: str, enabled: bool = True) -> None:
        self.cache_path = cache_path
        self.enabled = enabled

    def evaluate(self, symbol: str, use_cache_on_error: bool = True) -> Dict[str, Any]:
        if not self.enabled:
            return self._neutral("disabled")
        cache = read_json(self.cache_path, {})
        cache_key = str(symbol)
        cached = cache.get(cache_key) if isinstance(cache, dict) else None
        if cached and self._cache_is_fresh(cached):
            return cached
        try:
            payload = self._fetch(symbol)
            if isinstance(cache, dict):
                cache[cache_key] = payload
                write_json(self.cache_path, cache)
            return payload
        except Exception:
            if use_cache_on_error and cached:
                return cached
            return self._neutral("fund_flow_fetch_failed")

    def _cache_is_fresh(self, payload: Dict[str, Any]) -> bool:
        try:
            updated = datetime.fromisoformat(payload.get("updated_at"))
        except Exception:
            return False
        try:
            return (_now() - updated).total_seconds() < 2 * 60 * 60
        except TypeError:
            return False

    def _fetch(self, symbol: str) -> Dict[str, Any]:
        market_map = {"sh": 1, "sz": 0, "bj": 0}
        market = _market_for_symbol(str(symbol))
        url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
        params = {
            "lmt": "0",
            "klt": "101",
            "secid": "%s.%s" % (market_map[market], symbol),
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "_": int(time.time() * 1000),
        }
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0 Safari/537.36"
            )
        }
        with market_data_proxy_scope():
            response = requests.get(url, params=params, headers=headers, timeout=8)
        response.raise_for_status()
        payload = response.json()
        klines = ((payload.get("data") or {}).get("klines") or [])[-5:]
        recent: List[Dict[str, Any]] = []
        for line in klines:
            parts = str(line).split(",")
            if len(parts) < 13:
                continue
            recent.append(
                {
                    "date": parts[0],
                    "close": _num(parts[11]),
                    "change_pct": _num(parts[12]),
                    "main_net_amount": _num(parts[1]),
                    "main_net_ratio": _num(parts[6]),
                    "super_large_net_ratio": _num(parts[10]),
                    "large_net_ratio": _num(parts[9]),
                }
            )

        last3 = recent[-3:]
        main_net_amount_3d = sum(item["main_net_amount"] for item in last3)
        main_net_ratio_3d = sum(item["main_net_ratio"] for item in last3) / len(last3) if last3 else 0
        positive_days = len([item for item in last3 if item["main_net_amount"] > 0 and item["main_net_ratio"] > 0])
        latest_ratio = last3[-1]["main_net_ratio"] if last3 else 0

        if latest_ratio <= -8 or (main_net_ratio_3d <= -5 and positive_days == 0):
            level = "high_outflow"
            adjustment = -16
            allow = False
        elif latest_ratio <= -4 or main_net_ratio_3d < -2:
            level = "outflow_risk"
            adjustment = -8
            allow = True
        elif main_net_amount_3d > 0 and main_net_ratio_3d >= 1 and positive_days >= 2:
            level = "positive"
            adjustment = 5
            allow = True
        else:
            level = "neutral"
            adjustment = 0
            allow = True

        return {
            "updated_at": _now().isoformat(),
            "level": level,
            "score_adjustment": adjustment,
            "allow_recommendation": allow,
            "main_net_amount_3d": round(main_net_amount_3d, 2),
            "main_net_ratio_3d": round(main_net_ratio_3d, 2),
            "positive_days_3d": positive_days,
            "latest_main_net_ratio": round(latest_ratio, 2),
            "recent": recent,
            "errors": [],
        }

    def _neutral(self, reason: str) -> Dict[str, Any]:
        return {
            "updated_at": _now().isoformat(),
            "level": "neutral",
            "score_adjustment": 0,
            "allow_recommendation": True,
            "main_net_amount_3d": 0,
            "main_net_ratio_3d": 0,
            "positive_days_3d": 0,
            "latest_main_net_ratio": 0,
            "recent": [],
            "errors": [reason],
        }
