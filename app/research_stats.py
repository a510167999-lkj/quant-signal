"""研究回测的统计聚合与分桶。

从 research_backtest.py 抽出，把 trades 列表 / 横截面数值聚合成统计 dict
（胜率 / 均收益 / 公告分组 / 各维度分桶），与回测编排解耦，供 research_backtest 调用。
"""
from collections import defaultdict
from typing import Any, Dict, List

from app.research_common import _num
from app.signal_tags import build_signal_tags, score_bucket


def _add_counts(target: Dict[str, int], counts: Dict[str, Any]) -> None:
    for key, value in (counts or {}).items():
        try:
            amount = int(value)
        except (TypeError, ValueError):
            amount = 1
        target[key] = target.get(key, 0) + max(amount, 1)


def _trade_stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not trades:
        return {
            "trade_count": 0,
            "win_rate_pct": None,
            "avg_return_pct": None,
            "avg_adverse_pct": None,
        }
    wins = [item for item in trades if item.get("return_pct", 0) > 0]
    adverse = [item for item in trades if item.get("max_adverse_pct") is not None]
    return {
        "trade_count": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 2),
        "avg_return_pct": round(sum(item.get("return_pct", 0) for item in trades) / len(trades), 2),
        "avg_adverse_pct": round(sum(item.get("max_adverse_pct", 0) for item in adverse) / len(adverse), 2)
        if adverse
        else None,
    }


def _announcement_group_stats(selected: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_level: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for item in selected:
        context = item.get("announcement_context") or {}
        level = context.get("level") or "disabled"
        by_level[level].append(item)
        event_counts = context.get("event_counts") or {}
        if event_counts:
            for category in event_counts:
                by_event[category].append(item)
        else:
            by_event["no_announcement_event"].append(item)

    return {
        "by_level": {
            key: _trade_stats(value)
            for key, value in sorted(by_level.items(), key=lambda pair: (-len(pair[1]), pair[0]))
        },
        "by_event": {
            key: _trade_stats(value)
            for key, value in sorted(by_event.items(), key=lambda pair: (-len(pair[1]), pair[0]))
        },
    }


def _score_bucket(value: Any) -> str:
    return score_bucket(value)


def _rate_bucket(value: Any, prefix: str) -> str:
    rate = _num(value)
    if rate >= 70:
        return "%s_gte_70" % prefix
    if rate >= 60:
        return "%s_60_to_70" % prefix
    if rate >= 50:
        return "%s_50_to_60" % prefix
    return "%s_lt_50" % prefix


def _return_bucket(value: Any, prefix: str) -> str:
    result = _num(value)
    if result >= 3:
        return "%s_gte_3" % prefix
    if result >= 1:
        return "%s_1_to_3" % prefix
    if result >= 0:
        return "%s_0_to_1" % prefix
    return "%s_lt_0" % prefix


def _rank_bucket(position: Any) -> str:
    rank = int(_num(position))
    if rank <= 80:
        return "rank_1_80"
    if rank <= 150:
        return "rank_81_150"
    if rank <= 300:
        return "rank_151_300"
    if rank <= 600:
        return "rank_301_600"
    return "rank_gt_600"


def _amount_bucket(value: Any) -> str:
    amount = _num(value)
    if amount >= 1_000_000_000:
        return "amount_gte_1b"
    if amount >= 300_000_000:
        return "amount_300m_to_1b"
    if amount >= 100_000_000:
        return "amount_100m_to_300m"
    if amount >= 30_000_000:
        return "amount_30m_to_100m"
    return "amount_lt_30m"


def _relative_strength_bucket(value: Any, prefix: str, high: float, mid: float) -> str:
    if value is None:
        return "%s_unknown" % prefix
    result = _num(value)
    if result >= high:
        return "%s_gte_%s" % (prefix, int(high))
    if result >= mid:
        return "%s_%s_to_%s" % (prefix, int(mid), int(high))
    if result >= 0:
        return "%s_0_to_%s" % (prefix, int(mid))
    return "%s_lt_0" % prefix


def _signal_tags(signal: Dict[str, Any]) -> List[str]:
    return build_signal_tags(signal)


def _research_group_stats(selected: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_market_level: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_action: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_signal_tag: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_score_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_prior_win_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_prior_return_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_candidate_rank_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_amount_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_rs20_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_rs60_bucket: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

    for item in selected:
        by_market_level[str(item.get("market_level") or "unknown")].append(item)
        by_action[str(item.get("action") or "unknown")].append(item)
        by_score_bucket[_score_bucket(item.get("score"))].append(item)
        by_prior_win_bucket[_rate_bucket(item.get("prior_win_rate_pct"), "prior_win")].append(item)
        by_prior_return_bucket[_return_bucket(item.get("prior_avg_return_pct"), "prior_return")].append(item)
        by_candidate_rank_bucket[_rank_bucket(item.get("candidate_rank"))].append(item)
        by_amount_bucket[_amount_bucket(item.get("candidate_amount"))].append(item)
        relative = item.get("relative_strength") or {}
        by_rs20_bucket[
            _relative_strength_bucket(relative.get("relative_strength_20d_pct"), "rs20", 10, 5)
        ].append(item)
        by_rs60_bucket[
            _relative_strength_bucket(relative.get("relative_strength_60d_pct"), "rs60", 20, 10)
        ].append(item)
        tags = item.get("signal_tags") or []
        if tags:
            for tag in tags:
                by_signal_tag[str(tag)].append(item)
        else:
            by_signal_tag["untagged"].append(item)

    def build(payload: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        return {
            key: _trade_stats(value)
            for key, value in sorted(payload.items(), key=lambda pair: (-len(pair[1]), pair[0]))
        }

    return {
        "by_market_level": build(by_market_level),
        "by_action": build(by_action),
        "by_signal_tag": build(by_signal_tag),
        "by_score_bucket": build(by_score_bucket),
        "by_prior_win_bucket": build(by_prior_win_bucket),
        "by_prior_return_bucket": build(by_prior_return_bucket),
        "by_candidate_rank_bucket": build(by_candidate_rank_bucket),
        "by_amount_bucket": build(by_amount_bucket),
        "by_rs20_bucket": build(by_rs20_bucket),
        "by_rs60_bucket": build(by_rs60_bucket),
    }


def _split_events(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]
