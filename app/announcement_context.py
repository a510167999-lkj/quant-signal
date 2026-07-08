import contextlib
import io
from datetime import datetime, timedelta
from typing import Any, Dict, List
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.akshare_client import akshare_call
from app.storage import read_json, write_json


NEGATIVE_KEYWORDS = {
    "立案": -18,
    "调查": -12,
    "处罚": -14,
    "问询函": -10,
    "监管函": -12,
    "警示函": -12,
    "退市": -20,
    "风险警示": -18,
    "特别处理": -16,
    "诉讼": -10,
    "仲裁": -8,
    "冻结": -10,
    "质押": -6,
    "减持": -7,
    "业绩预亏": -10,
    "预亏": -9,
    "业绩预减": -7,
    "亏损": -7,
    "更正": -5,
    "补充更正": -5,
    "停牌": -8,
    "异常波动": -4,
    "资金占用": -18,
    "违规": -14,
}

POSITIVE_KEYWORDS = {
    "回购": 5,
    "增持": 5,
    "业绩预增": 6,
    "预增": 5,
    "扭亏": 5,
    "中标": 4,
    "合同": 4,
    "订单": 3,
    "分红": 1,
    "权益分派": 1,
    "股权激励": 2,
}

HARD_BLOCKER_KEYWORDS = {
    "立案",
    "退市",
    "风险警示",
    "特别处理",
    "资金占用",
    "处罚",
    "违规",
}


EVENT_RULES = [
    ("regulatory_penalty", ["立案", "调查", "处罚", "警示函", "监管函", "违规", "资金占用"]),
    ("exchange_inquiry", ["问询函", "关注函", "监管工作函"]),
    ("delisting_or_st", ["退市", "风险警示", "特别处理"]),
    ("litigation_freeze", ["诉讼", "仲裁", "冻结"]),
    ("pledge_or_reduction", ["质押", "减持"]),
    ("earnings_positive", ["业绩预增", "预增", "扭亏"]),
    ("earnings_negative", ["业绩预亏", "预亏", "业绩预减", "亏损"]),
    ("contract_or_order", ["中标", "合同", "订单", "战略合作", "许可协议"]),
    ("equity_incentive", ["股权激励", "限制性股票激励", "员工持股"]),
    ("dividend_distribution", ["分红", "权益分派"]),
    ("financing_or_guarantee", ["担保", "融资券", "债券", "可转债"]),
    ("routine_governance", ["股东大会", "董事会", "监事会", "独立董事", "制度", "月报表"]),
]


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


def _clean_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        return ""
    if not parsed.netloc.endswith("cninfo.com.cn"):
        return ""
    return text[:300]


def _date_value(value: Any):
    if value is None:
        return None
    text = str(value)[:10]
    try:
        return datetime.fromisoformat(text).date()
    except Exception:
        return None


def _keyword_score(title: str) -> int:
    lowered = title.lower()
    score = 0
    for keyword, weight in NEGATIVE_KEYWORDS.items():
        if keyword.lower() in lowered:
            score += weight
    if "ST" in title.upper():
        score -= 14
    for keyword, weight in POSITIVE_KEYWORDS.items():
        if keyword.lower() in lowered:
            score += weight
    return int(max(min(score, 12), -30))


def _has_hard_blocker(title: str) -> bool:
    lowered = title.lower()
    if "ST" in title.upper():
        return True
    return any(keyword.lower() in lowered for keyword in HARD_BLOCKER_KEYWORDS)


def classify_announcement_title(title: str) -> List[str]:
    lowered = title.lower()
    categories = []
    for category, keywords in EVENT_RULES:
        for keyword in keywords:
            if keyword.lower() in lowered:
                categories.append(category)
                break
    incentive_like = any(keyword in title for keyword in ["限制性股票", "股权激励", "解除限售", "员工持股"])
    buyback_cancel_like = any(keyword in title for keyword in ["回购注销", "注销部分"])
    if any(keyword in title for keyword in ["回购", "增持"]):
        if not (incentive_like and buyback_cancel_like):
            categories.append("buyback_or_increase")
    if "ST" in title.upper() and "delisting_or_st" not in categories:
        categories.append("delisting_or_st")
    return categories or ["other"]


def _announcement_from_row(row: Dict[str, Any]) -> Dict[str, Any]:
    title = str(_first_existing(row, ["公告标题", "标题", "title"], "") or "").strip()
    if not title:
        return {}
    return {
        "title": title[:140],
        "published_at": _parse_time(_first_existing(row, ["公告时间", "时间", "日期"], "")),
        "url": _clean_url(_first_existing(row, ["公告链接", "链接", "url"], "")),
        "score": _keyword_score(title),
        "hard_blocker": _has_hard_blocker(title),
        "categories": classify_announcement_title(title),
    }


def fetch_cninfo_announcements(symbol: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
    ak = _load_akshare()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        raw = akshare_call(
            "stock_zh_a_disclosure_report_cninfo",
            lambda: ak.stock_zh_a_disclosure_report_cninfo(
                symbol=str(symbol),
                market="沪深京",
                start_date=start_date,
                end_date=end_date,
            ),
        )
    announcements = []
    for row in raw.to_dict(orient="records") if raw is not None else []:
        item = _announcement_from_row(row)
        if item:
            announcements.append(item)
    return announcements


def build_announcement_context(
    announcements: List[Dict[str, Any]],
    as_of_date: Any = None,
    lookback_days: int = 60,
    updated_at: str = None,
) -> Dict[str, Any]:
    as_of = _date_value(as_of_date)
    cutoff = as_of - timedelta(days=lookback_days) if as_of else None
    scoped = []
    for item in announcements:
        published = _date_value(item.get("published_at"))
        if as_of:
            if not published or published > as_of:
                continue
            if cutoff and published < cutoff:
                continue
        scoped.append(dict(item))

    scoped.sort(key=lambda item: item.get("published_at") or "", reverse=True)
    negative_score = sum(item["score"] for item in scoped if item.get("score", 0) < 0)
    positive_score = min(sum(item["score"] for item in scoped if item.get("score", 0) > 0), 8)
    total_score = int(max(min(negative_score + positive_score, 10), -30))
    negative_count = len([item for item in scoped if item.get("score", 0) < 0])
    positive_count = len([item for item in scoped if item.get("score", 0) > 0])
    has_hard_blocker = any(item.get("hard_blocker") for item in scoped)
    event_counts: Dict[str, int] = {}
    for item in scoped:
        categories = classify_announcement_title(str(item.get("title") or ""))
        item["categories"] = categories
        for category in categories:
            event_counts[category] = event_counts.get(category, 0) + 1

    if has_hard_blocker or total_score <= -14:
        level = "high_risk"
        adjustment = -20
        allow = False
    elif total_score < 0:
        level = "watch_risk"
        adjustment = max(total_score, -3)
        allow = True
    elif total_score > 0:
        level = "positive"
        adjustment = 0
        allow = True
    else:
        level = "neutral"
        adjustment = 0
        allow = True

    return {
        "updated_at": updated_at or _now().isoformat(),
        "level": level,
        "score": total_score,
        "score_adjustment": adjustment,
        "allow_recommendation": allow,
        "announcement_count": len(scoped),
        "negative_count": negative_count,
        "positive_count": positive_count,
        "event_counts": event_counts,
        "announcements": scoped[:5],
        "errors": [],
    }


class AnnouncementContextProvider:
    def __init__(self, cache_path: str, lookback_days: int = 30, enabled: bool = True) -> None:
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
            return self._neutral("announcement_fetch_failed")

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
        end = _now().date()
        start = end - timedelta(days=self.lookback_days)
        return build_announcement_context(
            fetch_cninfo_announcements(
                str(symbol),
                start.strftime("%Y%m%d"),
                end.strftime("%Y%m%d"),
            ),
            as_of_date=end.isoformat(),
            lookback_days=self.lookback_days,
        )

    def _neutral(self, reason: str) -> Dict[str, Any]:
        return {
            "updated_at": _now().isoformat(),
            "level": "neutral",
            "score": 0,
            "score_adjustment": 0,
            "allow_recommendation": True,
            "announcement_count": 0,
            "negative_count": 0,
            "positive_count": 0,
            "event_counts": {},
            "announcements": [],
            "errors": [reason],
        }
