import math
from datetime import datetime, timedelta
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from app.akshare_client import akshare_call
from app.storage import read_json, write_json


NEGATIVE_KEYWORDS = {
    "立案": -14,
    "处罚": -12,
    "监管": -10,
    "问询": -8,
    "退市": -18,
    "st": -14,
    "亏损": -8,
    "减持": -6,
    "诉讼": -8,
    "仲裁": -6,
    "质押": -5,
    "解禁": -4,
    "风险": -5,
}

POSITIVE_KEYWORDS = {
    "回购": 4,
    "增持": 4,
    "预增": 5,
    "中标": 4,
    "订单": 3,
    "签订": 3,
    "合作": 2,
}


def _load_akshare():
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise RuntimeError("akshare is not installed. Run: pip install -r requirements.txt") from exc
    return ak


def _now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai"))


def _first_existing(row: Dict[str, Any], names: List[str], default: Any = None) -> Any:
    for name in names:
        if name in row:
            return row.get(name)
    return default


def _parse_time(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    try:
        return datetime.fromisoformat(text).isoformat()
    except Exception:
        return text


def _score_title(title: str) -> int:
    lowered = title.lower()
    score = 0
    for keyword, weight in NEGATIVE_KEYWORDS.items():
        if keyword in lowered:
            score += weight
    for keyword, weight in POSITIVE_KEYWORDS.items():
        if keyword in lowered:
            score += weight
    return score


class NewsContextProvider:
    def __init__(self, cache_path: str, lookback_days: int = 14, enabled: bool = True) -> None:
        self.cache_path = cache_path
        self.lookback_days = lookback_days
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
            return self._neutral("news_fetch_failed")

    def _cache_is_fresh(self, payload: Dict[str, Any]) -> bool:
        updated_at = payload.get("updated_at")
        try:
            updated = datetime.fromisoformat(updated_at)
        except Exception:
            return False
        try:
            return (_now() - updated).total_seconds() < 6 * 60 * 60
        except TypeError:
            return False

    def _fetch(self, symbol: str) -> Dict[str, Any]:
        ak = _load_akshare()
        raw = akshare_call(
            "stock_news_em",
            lambda: ak.stock_news_em(symbol=symbol),
        )
        articles = []
        cutoff = _now() - timedelta(days=self.lookback_days)
        for row in raw.to_dict(orient="records") if raw is not None else []:
            title = str(_first_existing(row, ["新闻标题", "标题", "title"], "") or "").strip()
            if not title:
                continue
            published = _parse_time(_first_existing(row, ["发布时间", "时间", "日期"], ""))
            if published:
                try:
                    moment = datetime.fromisoformat(published)
                    if moment.tzinfo is None:
                        moment = moment.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
                    if moment < cutoff:
                        continue
                except Exception:
                    pass
            articles.append(
                {
                    "title": title[:120],
                    "published_at": published,
                    "source": str(_first_existing(row, ["文章来源", "新闻来源", "来源"], "") or "")[:40],
                    "score": _score_title(title),
                }
            )

        score = sum(item["score"] for item in articles)
        score = int(max(min(score, 10), -24))
        negative_count = len([item for item in articles if item["score"] < 0])
        positive_count = len([item for item in articles if item["score"] > 0])
        if score <= -12:
            level = "high_risk"
            adjustment = -18
            allow = False
        elif score < 0:
            level = "watch_risk"
            adjustment = max(score, -8)
            allow = True
        elif score > 0:
            level = "positive"
            adjustment = min(math.ceil(score / 2), 5)
            allow = True
        else:
            level = "neutral"
            adjustment = 0
            allow = True

        return {
            "updated_at": _now().isoformat(),
            "level": level,
            "score": score,
            "score_adjustment": adjustment,
            "allow_recommendation": allow,
            "article_count": len(articles),
            "negative_count": negative_count,
            "positive_count": positive_count,
            "headlines": articles[:5],
            "errors": [],
        }

    def _neutral(self, reason: str) -> Dict[str, Any]:
        return {
            "updated_at": _now().isoformat(),
            "level": "neutral",
            "score": 0,
            "score_adjustment": 0,
            "allow_recommendation": True,
            "article_count": 0,
            "negative_count": 0,
            "positive_count": 0,
            "headlines": [],
            "errors": [reason],
        }
