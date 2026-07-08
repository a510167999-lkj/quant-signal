import math
from typing import Any, Dict, List


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def score_bucket(value: Any) -> str:
    score = _num(value)
    if score >= 5:
        return "score_gte_5"
    if score >= 4:
        return "score_4_to_5"
    if score >= 3:
        return "score_3_to_4"
    if score >= 2:
        return "score_2_to_3"
    return "score_lt_2"


def build_signal_tags(signal: Dict[str, Any]) -> List[str]:
    indicators = signal.get("indicators") or {}
    reasons_text = " ".join(
        str(item)
        for item in (
            list(signal.get("reasons") or [])
            + list(signal.get("confirmations") or [])
            + list(signal.get("risks") or [])
        )
    )
    tags = {
        "action_%s" % str(signal.get("action", "unknown")).lower(),
        score_bucket(signal.get("score")),
    }

    volume_ratio = _num(indicators.get("volume_ratio"))
    return_20d = _num(indicators.get("return_20d_pct"))
    drawdown_60d = _num(indicators.get("drawdown_60d_pct"))
    volatility_20d = _num(indicators.get("volatility_20d_pct"))
    rsi = _num(indicators.get("rsi14"))

    if "20/60 日均线" in reasons_text or "20 日均线" in reasons_text:
        tags.add("ma_structure")
    if "MACD" in reasons_text:
        tags.add("macd_confirmed")
    if "RSI" in reasons_text:
        tags.add("rsi_repair")
    if "突破近 20 日高点" in reasons_text:
        tags.add("breakout_20d")
    if "放量" in reasons_text or volume_ratio >= 1.2:
        tags.add("volume_confirmed")
    if 0 <= return_20d <= 12:
        tags.add("moderate_20d_momentum")
    elif return_20d > 12:
        tags.add("extended_20d_momentum")
    elif return_20d < 0:
        tags.add("weak_20d_momentum")
    if drawdown_60d > -8:
        tags.add("near_60d_high")
    elif drawdown_60d < -18:
        tags.add("deep_60d_drawdown")
    if 45 <= rsi <= 70:
        tags.add("balanced_rsi")
    elif rsi > 75:
        tags.add("overbought_rsi")
    elif rsi < 40:
        tags.add("weak_rsi")
    if volatility_20d and volatility_20d <= 35:
        tags.add("controlled_volatility")
    elif volatility_20d >= 45:
        tags.add("high_volatility")

    return sorted(tags)


def build_candidate_context_tags(candidate: Dict[str, Any], quality: Dict[str, Any] = None) -> List[str]:
    tags = set()

    rank = int(_num(candidate.get("candidate_rank") or candidate.get("rank")))
    if 0 < rank <= 20:
        tags.add("candidate_rank_lte_20")
    if 0 < rank <= 40:
        tags.add("candidate_rank_lte_40")

    amount = _num(candidate.get("candidate_amount") or candidate.get("amount"))
    if amount >= 1_000_000_000:
        tags.add("amount_gte_1b")
    if amount >= 300_000_000:
        tags.add("amount_gte_300m")
    if amount >= 100_000_000:
        tags.add("amount_gte_100m")

    amount_rank_pct = _num(candidate.get("candidate_amount_rank_pct") or candidate.get("amount_rank_pct"))
    if 0 < amount_rank_pct <= 10:
        tags.add("amount_rank_pct_top_10")
    if 0 < amount_rank_pct <= 20:
        tags.add("amount_rank_pct_top_20")
    if 0 < amount_rank_pct <= 40:
        tags.add("amount_rank_pct_top_40")

    change_pct = _num(candidate.get("candidate_change_pct") or candidate.get("change_pct"))
    if change_pct < 0:
        tags.add("candidate_change_negative")
    elif change_pct < 3:
        tags.add("candidate_change_0_to_3")
    elif change_pct < 6:
        tags.add("candidate_change_3_to_6")
    else:
        tags.add("candidate_change_gte_6")

    prefilter_score = _num(candidate.get("candidate_prefilter_score") or candidate.get("prefilter_score"))
    if prefilter_score >= 12:
        tags.add("prefilter_score_gte_12")
    if prefilter_score >= 13:
        tags.add("prefilter_score_gte_13")

    rank_pct = _num(candidate.get("candidate_rank_pct"))
    if 0 < rank_pct <= 25:
        tags.add("candidate_rank_pct_top_25")
    if 0 < rank_pct <= 50:
        tags.add("candidate_rank_pct_top_50")

    quality = quality or {}
    prior_win = _num(quality.get("win_rate_pct") or candidate.get("prior_win_rate_pct"))
    if prior_win >= 60:
        tags.add("prior_win_gte_60")
    if prior_win >= 70:
        tags.add("prior_win_gte_70")

    prior_return = _num(quality.get("avg_return_pct") or candidate.get("prior_avg_return_pct"))
    if prior_return >= 1:
        tags.add("prior_return_gte_1")
    if prior_return >= 3:
        tags.add("prior_return_gte_3")

    prior_adverse = abs(_num(quality.get("avg_adverse_pct") or candidate.get("prior_avg_adverse_pct")))
    if prior_adverse and prior_adverse <= 4:
        tags.add("prior_adverse_lte_4")
    if prior_adverse and prior_adverse <= 3:
        tags.add("prior_adverse_lte_3")

    margin = candidate.get("margin_eligibility") or {}
    if margin.get("financing_underlying"):
        tags.add("margin_financing_underlying")
    if margin.get("financing_eligible"):
        tags.add("margin_financing_eligible")
    if margin.get("short_underlying"):
        tags.add("margin_short_underlying")
    if margin.get("short_eligible"):
        tags.add("margin_short_eligible")
    if margin.get("collateral_eligible"):
        tags.add("margin_collateral_eligible")
    exchange = str(margin.get("exchange") or "").strip().lower()
    if exchange in {"sse", "szse"}:
        tags.add("margin_exchange_%s" % exchange)
    price_limit = str(margin.get("price_limit") or "").strip()
    if price_limit.startswith("20"):
        tags.add("margin_price_limit_20")
    elif price_limit.startswith("10"):
        tags.add("margin_price_limit_10")

    return sorted(tags)


def build_entry_executability_tags(context: Dict[str, Any]) -> List[str]:
    tags = set()
    if not context:
        return []

    executable = context.get("executable")
    if executable is True:
        tags.add("entry_executable")
    elif executable is False:
        tags.add("entry_not_executable")

    gap_pct = context.get("gap_pct")
    if gap_pct is not None:
        gap_pct = _num(gap_pct)
        if gap_pct < -1:
            tags.add("entry_gap_lt_neg1")
        else:
            tags.add("entry_gap_gte_neg1")
        if gap_pct >= 0:
            tags.add("entry_gap_gte_0")
        if -1 <= gap_pct <= 2:
            tags.add("entry_gap_neg1_to_2")
        if gap_pct > 2:
            tags.add("entry_gap_gt_2")

    intraday_range_pct = context.get("intraday_range_pct")
    if intraday_range_pct is not None:
        intraday_range_pct = _num(intraday_range_pct)
        if intraday_range_pct < 6:
            tags.add("entry_range_lt_6")
        else:
            tags.add("entry_range_gte_6")

    return sorted(tags)


def build_price_action_tags(context: Dict[str, Any]) -> List[str]:
    tags = set()

    gap_pct = context.get("gap_pct")
    if gap_pct is not None:
        gap_pct = _num(gap_pct)
        if gap_pct < 0:
            tags.add("price_gap_down")
        elif gap_pct < 2:
            tags.add("price_gap_up_0_to_2")
        elif gap_pct < 5:
            tags.add("price_gap_up_2_to_5")
        else:
            tags.add("price_gap_up_gte_5")

    intraday_return_pct = context.get("intraday_return_pct")
    if intraday_return_pct is not None:
        intraday_return_pct = _num(intraday_return_pct)
        if intraday_return_pct >= 3:
            tags.add("price_intraday_gain_gte_3")
        if intraday_return_pct >= 0:
            tags.add("price_intraday_gain")
        else:
            tags.add("price_intraday_loss")

    close_position_pct = context.get("close_position_pct")
    if close_position_pct is not None:
        close_position_pct = _num(close_position_pct)
        if close_position_pct >= 80:
            tags.add("price_close_near_high")
        elif close_position_pct <= 30:
            tags.add("price_close_weak")

    range_pct = context.get("range_pct")
    if range_pct is not None:
        range_pct = _num(range_pct)
        if range_pct < 4:
            tags.add("price_range_lt_4")
        elif range_pct < 8:
            tags.add("price_range_4_to_8")
        else:
            tags.add("price_range_gte_8")

    upper_shadow_pct = context.get("upper_shadow_pct")
    if upper_shadow_pct is not None:
        upper_shadow_pct = _num(upper_shadow_pct)
        if upper_shadow_pct >= 3:
            tags.add("price_upper_shadow_gte_3")

    lower_shadow_pct = context.get("lower_shadow_pct")
    if lower_shadow_pct is not None:
        lower_shadow_pct = _num(lower_shadow_pct)
        if lower_shadow_pct >= 3:
            tags.add("price_lower_shadow_gte_3")

    signal_change_pct = _num(context.get("signal_change_pct"))
    limit_threshold_pct = _num(context.get("limit_threshold_pct"), 10)
    if signal_change_pct >= limit_threshold_pct - 0.5:
        tags.add("price_signal_limit_up")
    elif signal_change_pct >= limit_threshold_pct - 1.5:
        tags.add("price_signal_near_limit_up")

    recent_limit_up_count = int(_num(context.get("recent_limit_up_count_20d")))
    if recent_limit_up_count >= 1:
        tags.add("recent_limit_up_20d")
    if recent_limit_up_count >= 2:
        tags.add("recent_limit_up_20d_gte_2")

    recent_near_limit_up_count = int(_num(context.get("recent_near_limit_up_count_20d")))
    if recent_near_limit_up_count >= 1:
        tags.add("recent_near_limit_up_20d")
    if recent_near_limit_up_count >= 2:
        tags.add("recent_near_limit_up_20d_gte_2")

    recent_large_up_count = int(_num(context.get("recent_large_up_count_20d")))
    if recent_large_up_count >= 1:
        tags.add("recent_large_up_20d")
    if recent_large_up_count >= 3:
        tags.add("recent_large_up_20d_gte_3")

    return sorted(tags)


def build_industry_rotation_tags(context: Dict[str, Any]) -> List[str]:
    tags = set()
    sample_count = int(_num(context.get("sample_count")))
    if sample_count <= 0:
        return []

    tags.add("industry_sample_gte_20" if sample_count >= 20 else "industry_sample_lt_20")
    if sample_count >= 50:
        tags.add("industry_sample_gte_50")

    ma20 = _num(context.get("above_ma20_pct"))
    if ma20 >= 70:
        tags.add("industry_ma20_gte_70")
    if ma20 >= 60:
        tags.add("industry_ma20_gte_60")
    if ma20 >= 50:
        tags.add("industry_ma20_gte_50")
    else:
        tags.add("industry_ma20_lt_50")

    ret20_positive = _num(context.get("return_20d_positive_pct"))
    if ret20_positive >= 70:
        tags.add("industry_ret20_pos_gte_70")
    if ret20_positive >= 60:
        tags.add("industry_ret20_pos_gte_60")
    if ret20_positive >= 50:
        tags.add("industry_ret20_pos_gte_50")
    else:
        tags.add("industry_ret20_pos_lt_50")

    advancing = _num(context.get("advancing_pct"))
    if advancing >= 70:
        tags.add("industry_advancing_gte_70")
    if advancing >= 60:
        tags.add("industry_advancing_gte_60")
    if advancing >= 50:
        tags.add("industry_advancing_gte_50")
    else:
        tags.add("industry_advancing_lt_50")

    median_ret20 = context.get("median_return_20d_pct")
    if median_ret20 is not None:
        median_ret20 = _num(median_ret20)
        if median_ret20 >= 10:
            tags.add("industry_median_ret20_gte_10")
        if median_ret20 >= 5:
            tags.add("industry_median_ret20_gte_5")
        if median_ret20 >= 0:
            tags.add("industry_median_ret20_gte_0")
        else:
            tags.add("industry_median_ret20_lt_0")

    top_ret20 = context.get("top_return_20d_pct")
    if top_ret20 is not None:
        top_ret20 = _num(top_ret20)
        if top_ret20 >= 30:
            tags.add("industry_top_ret20_gte_30")
        if top_ret20 >= 20:
            tags.add("industry_top_ret20_gte_20")
        if top_ret20 >= 10:
            tags.add("industry_top_ret20_gte_10")

    dispersion = _num(context.get("return_20d_dispersion_pct"))
    if dispersion >= 25:
        tags.add("industry_dispersion_gte_25")
    if dispersion >= 15:
        tags.add("industry_dispersion_gte_15")

    if ret20_positive >= 60 and ma20 >= 60 and _num(median_ret20) >= 0:
        tags.add("industry_rotation_broad")
    if _num(top_ret20) >= 20 and _num(median_ret20) < 5:
        tags.add("industry_rotation_narrow_hot")

    return sorted(tags)


def build_announcement_tags(context: Dict[str, Any]) -> List[str]:
    tags = set()
    if not context:
        return []

    level = str(context.get("level") or "neutral").strip().lower()
    if level:
        tags.add("announcement_level_%s" % level)

    if context.get("allow_recommendation") is False:
        tags.add("announcement_blocked")
    elif context.get("allow_recommendation") is True:
        tags.add("announcement_allowed")

    announcement_count = int(_num(context.get("announcement_count")))
    if announcement_count >= 1:
        tags.add("announcement_count_gte_1")
    if announcement_count >= 5:
        tags.add("announcement_count_gte_5")
    if announcement_count >= 20:
        tags.add("announcement_count_gte_20")

    negative_count = int(_num(context.get("negative_count")))
    positive_count = int(_num(context.get("positive_count")))
    if negative_count > 0:
        tags.add("announcement_negative")
    if negative_count >= 2:
        tags.add("announcement_negative_gte_2")
    if positive_count > 0:
        tags.add("announcement_positive")
    if positive_count >= 2:
        tags.add("announcement_positive_gte_2")

    score = _num(context.get("score"))
    if score <= -14:
        tags.add("announcement_score_lte_neg14")
    elif score < 0:
        tags.add("announcement_score_neg")
    elif score > 0:
        tags.add("announcement_score_pos")

    for event in sorted((context.get("event_counts") or {}).keys()):
        event_name = str(event).strip().lower()
        if event_name:
            tags.add("announcement_event_%s" % event_name)

    return sorted(tags)


def build_market_breadth_tags(context: Dict[str, Any]) -> List[str]:
    tags = set()
    sample_count = int(_num(context.get("sample_count")))
    if sample_count <= 0:
        return []

    tags.add("breadth_sample_gte_20" if sample_count >= 20 else "breadth_sample_lt_20")
    if sample_count >= 100:
        tags.add("breadth_sample_gte_100")

    above_ma20 = _num(context.get("above_ma20_pct"))
    if above_ma20 >= 70:
        tags.add("breadth_ma20_gte_70")
    if above_ma20 >= 60:
        tags.add("breadth_ma20_gte_60")
    if above_ma20 >= 50:
        tags.add("breadth_ma20_gte_50")
    else:
        tags.add("breadth_ma20_lt_50")

    above_ma60 = _num(context.get("above_ma60_pct"))
    if above_ma60 >= 60:
        tags.add("breadth_ma60_gte_60")
    if above_ma60 >= 50:
        tags.add("breadth_ma60_gte_50")
    else:
        tags.add("breadth_ma60_lt_50")

    ret20_positive = _num(context.get("return_20d_positive_pct"))
    if ret20_positive >= 70:
        tags.add("breadth_ret20_pos_gte_70")
    if ret20_positive >= 60:
        tags.add("breadth_ret20_pos_gte_60")
    if ret20_positive >= 50:
        tags.add("breadth_ret20_pos_gte_50")
    else:
        tags.add("breadth_ret20_pos_lt_50")

    advancing = _num(context.get("advancing_pct"))
    if advancing >= 60:
        tags.add("breadth_advancing_gte_60")
    if advancing >= 50:
        tags.add("breadth_advancing_gte_50")
    else:
        tags.add("breadth_advancing_lt_50")

    median_return_20d = context.get("median_return_20d_pct")
    if median_return_20d is not None:
        median_return_20d = _num(median_return_20d)
        if median_return_20d >= 10:
            tags.add("breadth_median_ret20_gte_10")
        if median_return_20d >= 5:
            tags.add("breadth_median_ret20_gte_5")
        if median_return_20d >= 0:
            tags.add("breadth_median_ret20_gte_0")
        else:
            tags.add("breadth_median_ret20_lt_0")

    liquid_300m = _num(context.get("liquid_300m_pct"))
    if liquid_300m >= 50:
        tags.add("breadth_liquid_300m_gte_50")
    elif liquid_300m > 0:
        tags.add("breadth_liquid_300m_lt_50")

    return sorted(tags)


def build_proxy_market_tags(context: Dict[str, Any]) -> List[str]:
    tags = set()
    proxy20 = context.get("proxy_return_20d_avg_pct")
    proxy60 = context.get("proxy_return_60d_avg_pct")
    proxy20_max = context.get("proxy_return_20d_max_pct")
    proxy60_max = context.get("proxy_return_60d_max_pct")

    if proxy20 is not None:
        proxy20 = _num(proxy20)
        if proxy20 >= 15:
            tags.add("proxy20_avg_gte_15")
        if proxy20 >= 10:
            tags.add("proxy20_avg_gte_10")
        if proxy20 >= 5:
            tags.add("proxy20_avg_gte_5")
        if proxy20 >= 0:
            tags.add("proxy20_avg_gte_0")
        else:
            tags.add("proxy20_avg_lt_0")
        if proxy20 <= -5:
            tags.add("proxy20_avg_lte_neg5")

    if proxy60 is not None:
        proxy60 = _num(proxy60)
        if proxy60 >= 15:
            tags.add("proxy60_avg_gte_15")
        if proxy60 >= 10:
            tags.add("proxy60_avg_gte_10")
        if proxy60 >= 5:
            tags.add("proxy60_avg_gte_5")
        if proxy60 >= 0:
            tags.add("proxy60_avg_gte_0")
        else:
            tags.add("proxy60_avg_lt_0")
        if proxy60 <= -5:
            tags.add("proxy60_avg_lte_neg5")

    if proxy20_max is not None:
        proxy20_max = _num(proxy20_max)
        if proxy20_max >= 15:
            tags.add("proxy20_max_gte_15")
        if proxy20_max >= 10:
            tags.add("proxy20_max_gte_10")
        if proxy20_max >= 5:
            tags.add("proxy20_max_gte_5")

    if proxy60_max is not None:
        proxy60_max = _num(proxy60_max)
        if proxy60_max >= 15:
            tags.add("proxy60_max_gte_15")
        if proxy60_max >= 10:
            tags.add("proxy60_max_gte_10")
        if proxy60_max >= 5:
            tags.add("proxy60_max_gte_5")

    if proxy20 is not None and proxy60 is not None:
        if proxy20 >= 5 and proxy60 >= 0:
            tags.add("proxy_market_bullish")
        if proxy20 >= 10 and proxy60 >= 5:
            tags.add("proxy_market_hot")
        if proxy20 < 0 or proxy60 < 0:
            tags.add("proxy_market_fragile")

    return sorted(tags)
