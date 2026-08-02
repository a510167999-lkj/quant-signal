from typing import Any, Dict, List

from app.analysis import build_analysis
from app.compat import model_to_dict
from app.schemas import AnalyzeRequest


MARKET_PROXIES = [
    {"symbol": "510300", "market": "etf", "name": "沪深300ETF"},
    {"symbol": "159915", "market": "etf", "name": "创业板ETF"},
]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def evaluate_market_regime(data_provider, disclaimer: str) -> Dict[str, Any]:
    proxies: List[Dict[str, Any]] = []
    errors = []
    for proxy in MARKET_PROXIES:
        try:
            analysis = build_analysis(
                AnalyzeRequest(
                    symbol=proxy["symbol"],
                    market=proxy["market"],
                    name=proxy["name"],
                    lookback_days=360,
                    adjust="qfq",
                ),
                data_provider,
                disclaimer,
            )
            payload = model_to_dict(analysis)
            proxies.append(
                {
                    "symbol": proxy["symbol"],
                    "name": proxy["name"],
                    "action": payload["action"],
                    "score": payload["score"],
                    "last_close": payload["last_close"],
                    "as_of": payload["as_of"],
                    "market_data_source": payload.get("source") or "unknown",
                    "ma20": payload["indicators"].get("ma20"),
                    "ma60": payload["indicators"].get("ma60"),
                    "return_20d_pct": payload["indicators"].get("return_20d_pct"),
                }
            )
        except Exception as exc:
            errors.append({"symbol": proxy["symbol"], "message": str(exc)})

    if not proxies:
        return {
            "level": "unknown",
            "label": "大盘环境未知",
            "score_adjustment": 0,
            "min_signal_score": 2,
            "allow_watch": True,
            "proxies": proxies,
            "errors": errors,
        }

    avg_score = sum(_num(item.get("score")) for item in proxies) / len(proxies)
    weak_count = len([item for item in proxies if item.get("action") in {"SELL", "REDUCE"}])
    strong_count = len([item for item in proxies if item.get("action") == "BUY" or _num(item.get("score")) >= 2])

    if weak_count >= 2 or avg_score <= -1.5:
        level = "defensive"
        label = "大盘防守"
        score_adjustment = -18
        min_signal_score = 4.0
        allow_watch = False
    elif weak_count == 1 or avg_score < 0.5:
        level = "cautious"
        label = "大盘谨慎"
        score_adjustment = -8
        min_signal_score = 3.0
        allow_watch = True
    elif strong_count >= 2 and avg_score >= 2:
        level = "favorable"
        label = "大盘友好"
        score_adjustment = 8
        min_signal_score = 2.0
        allow_watch = True
    else:
        level = "neutral"
        label = "大盘中性"
        score_adjustment = 0
        min_signal_score = 2.5
        allow_watch = True

    return {
        "level": level,
        "label": label,
        "avg_score": round(avg_score, 2),
        "score_adjustment": score_adjustment,
        "min_signal_score": min_signal_score,
        "allow_watch": allow_watch,
        "proxies": proxies,
        "errors": errors,
    }
