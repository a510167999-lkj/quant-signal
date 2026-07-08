import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


def _load_env_file() -> None:
    if "pytest" in sys.modules and os.getenv("LOAD_ENV_IN_TESTS", "").strip() != "1":
        return
    env_path = Path(".env")
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path, override=False)


def _split_csv(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    api_title: str = "A-share Quant Signal Platform"
    api_version: str = "0.1.0"
    cors_origins: List[str] = field(default_factory=list)
    cache_ttl_seconds: int = 1800
    market_data_provider: str = "akshare"
    market_data_cache_path: str = "data/market_data_cache.sqlite"
    tushare_token: str = ""
    tushare_fallback_to_akshare: bool = True
    enable_mootdx_l1_context: bool = False
    mootdx_servers: str = ""
    mootdx_timeout_seconds: float = 3.0
    basic_auth_user: str = ""
    basic_auth_password: str = ""
    watchlist_path: str = "data/watchlist.json"
    holdings_path: str = "data/holdings.json"
    latest_recommendations_path: str = "data/recommendations_latest.json"
    recommendation_history_path: str = "data/recommendations_history.jsonl"
    recommendation_lock_path: str = "data/recommendations.lock"
    alerts_path: str = "data/alerts.jsonl"
    universe_cache_path: str = "data/a_share_universe.json"
    industry_cache_path: str = "data/industry_strength.json"
    industry_history_cache_dir: str = "data/industry_history"
    news_cache_path: str = "data/news_context.json"
    announcement_cache_path: str = "data/announcement_context.json"
    margin_eligibility_cache_path: str = "data/margin_eligibility.json"
    akshare_status_path: str = "data/akshare_status.json"
    scan_max_deep: int = 500
    intraday_scan_max_deep: int = 120
    scan_result_limit: int = 3
    scan_min_amount: float = 30000000
    scan_min_price: float = 3.0
    scan_max_price: float = 300.0
    scan_industry_top_n: int = 3
    scan_per_industry_top_n: int = 3
    industry_top_n: int = 50
    min_backtest_trades: int = 1
    min_backtest_win_rate: float = 35.0
    min_backtest_avg_return: float = 0.0
    max_backtest_drawdown: float = 28.0
    max_backtest_avg_adverse: float = 5.0
    max_entry_gap_up_pct: float = 6.0
    max_entry_gap_down_pct: float = 7.0
    locked_limit_gap_pct: float = 9.3
    max_entry_intraday_range_pct: float = 8.0
    recommendation_min_signal_score: float = 2.0
    recommendation_required_signal_tags: List[str] = field(
        default_factory=lambda: ["breadth_advancing_gte_50", "breakout_20d", "price_gap_up_2_to_5"]
    )
    recommendation_require_all_signal_tags: bool = True
    recommendation_excluded_signal_tags: List[str] = field(default_factory=list)
    recommendation_allowed_market_levels: List[str] = field(default_factory=lambda: ["favorable", "neutral"])
    recommendation_symbol_cooldown_days: int = 5
    fund_flow_cache_path: str = "data/fund_flow_context.json"
    enable_fund_flow_context: bool = True
    enable_news_context: bool = True
    enable_announcement_context: bool = True
    enable_margin_eligibility_context: bool = True
    news_lookback_days: int = 14
    announcement_lookback_days: int = 30
    monitor_recent_days: int = 5
    monitor_intraday_drop_pct: float = 4.0
    monitor_profit_lock_activation_pct: float = 18.0
    monitor_pre_exit_calendar_gap_days: int = 7
    performance_strategy_hold_days: int = 5
    alert_webhook_url: str = ""


def get_settings() -> Settings:
    _load_env_file()
    ttl_raw = os.getenv("CACHE_TTL_SECONDS", "1800")
    try:
        cache_ttl_seconds = max(60, int(ttl_raw))
    except ValueError:
        cache_ttl_seconds = 1800

    def int_setting(name: str, default: int, minimum: int, maximum: int) -> int:
        raw = os.getenv(name, str(default))
        try:
            value = int(raw)
        except ValueError:
            return default
        return min(max(value, minimum), maximum)

    def float_setting(name: str, default: float, minimum: float, maximum: float) -> float:
        raw = os.getenv(name, str(default))
        try:
            value = float(raw)
        except ValueError:
            return default
        return min(max(value, minimum), maximum)

    return Settings(
        cors_origins=_split_csv(os.getenv("CORS_ORIGINS", "")),
        cache_ttl_seconds=cache_ttl_seconds,
        market_data_provider=os.getenv("MARKET_DATA_PROVIDER", "akshare"),
        market_data_cache_path=os.getenv("MARKET_DATA_CACHE_PATH", "data/market_data_cache.sqlite"),
        tushare_token=os.getenv("TUSHARE_TOKEN", ""),
        tushare_fallback_to_akshare=os.getenv("TUSHARE_FALLBACK_TO_AKSHARE", "1").strip() != "0",
        enable_mootdx_l1_context=os.getenv("ENABLE_MOOTDX_L1_CONTEXT", "0").strip() == "1",
        mootdx_servers=os.getenv("MOOTDX_SERVERS", ""),
        mootdx_timeout_seconds=float_setting("MOOTDX_TIMEOUT_SECONDS", 3.0, 0.5, 30.0),
        basic_auth_user=os.getenv("BASIC_AUTH_USER", ""),
        basic_auth_password=os.getenv("BASIC_AUTH_PASSWORD", ""),
        watchlist_path=os.getenv("WATCHLIST_PATH", "data/watchlist.json"),
        holdings_path=os.getenv("HOLDINGS_PATH", "data/holdings.json"),
        latest_recommendations_path=os.getenv(
            "LATEST_RECOMMENDATIONS_PATH", "data/recommendations_latest.json"
        ),
        recommendation_history_path=os.getenv(
            "RECOMMENDATION_HISTORY_PATH", "data/recommendations_history.jsonl"
        ),
        alerts_path=os.getenv("ALERTS_PATH", "data/alerts.jsonl"),
        recommendation_lock_path=os.getenv("RECOMMENDATION_LOCK_PATH", "data/recommendations.lock"),
        universe_cache_path=os.getenv("UNIVERSE_CACHE_PATH", "data/a_share_universe.json"),
        industry_cache_path=os.getenv("INDUSTRY_CACHE_PATH", "data/industry_strength.json"),
        industry_history_cache_dir=os.getenv("INDUSTRY_HISTORY_CACHE_DIR", "data/industry_history"),
        news_cache_path=os.getenv("NEWS_CACHE_PATH", "data/news_context.json"),
        announcement_cache_path=os.getenv("ANNOUNCEMENT_CACHE_PATH", "data/announcement_context.json"),
        margin_eligibility_cache_path=os.getenv(
            "MARGIN_ELIGIBILITY_CACHE_PATH", "data/margin_eligibility.json"
        ),
        akshare_status_path=os.getenv("AKSHARE_STATUS_PATH", "data/akshare_status.json"),
        scan_max_deep=int_setting("SCAN_MAX_DEEP", 500, 20, 3000),
        intraday_scan_max_deep=int_setting("INTRADAY_SCAN_MAX_DEEP", 120, 20, 1000),
        scan_result_limit=int_setting("SCAN_RESULT_LIMIT", 3, 3, 100),
        scan_min_amount=float_setting("SCAN_MIN_AMOUNT", 30000000, 0, 1000000000),
        scan_min_price=float_setting("SCAN_MIN_PRICE", 3.0, 0.01, 10000),
        scan_max_price=float_setting("SCAN_MAX_PRICE", 300.0, 1, 100000),
        scan_industry_top_n=int_setting("SCAN_INDUSTRY_TOP_N", 3, 0, 120),
        scan_per_industry_top_n=int_setting("SCAN_PER_INDUSTRY_TOP_N", 3, 0, 200),
        industry_top_n=int_setting("INDUSTRY_TOP_N", 50, 0, 120),
        min_backtest_trades=int_setting("MIN_BACKTEST_TRADES", 1, 0, 30),
        min_backtest_win_rate=float_setting("MIN_BACKTEST_WIN_RATE", 35.0, 0, 100),
        min_backtest_avg_return=float_setting("MIN_BACKTEST_AVG_RETURN", 0.0, -20, 50),
        max_backtest_drawdown=float_setting("MAX_BACKTEST_DRAWDOWN", 28.0, 1, 100),
        max_backtest_avg_adverse=float_setting("MAX_BACKTEST_AVG_ADVERSE", 5.0, 0.5, 50),
        max_entry_gap_up_pct=float_setting("MAX_ENTRY_GAP_UP_PCT", 6.0, 0.5, 30),
        max_entry_gap_down_pct=float_setting("MAX_ENTRY_GAP_DOWN_PCT", 7.0, 0.5, 30),
        locked_limit_gap_pct=float_setting("LOCKED_LIMIT_GAP_PCT", 9.3, 3, 30),
        max_entry_intraday_range_pct=float_setting("MAX_ENTRY_INTRADAY_RANGE_PCT", 8.0, 0.5, 50),
        recommendation_min_signal_score=float_setting("RECOMMENDATION_MIN_SIGNAL_SCORE", 2.0, 0, 20),
        recommendation_required_signal_tags=_split_csv(
            os.getenv(
                "RECOMMENDATION_REQUIRED_SIGNAL_TAGS",
                "breadth_advancing_gte_50,breakout_20d,price_gap_up_2_to_5",
            )
        ),
        recommendation_require_all_signal_tags=os.getenv(
            "RECOMMENDATION_REQUIRE_ALL_SIGNAL_TAGS", "1"
        ).strip()
        != "0",
        recommendation_excluded_signal_tags=_split_csv(
            os.getenv("RECOMMENDATION_EXCLUDED_SIGNAL_TAGS", "")
        ),
        recommendation_allowed_market_levels=_split_csv(
            os.getenv("RECOMMENDATION_ALLOWED_MARKET_LEVELS", "favorable,neutral")
        ),
        recommendation_symbol_cooldown_days=int_setting("RECOMMENDATION_SYMBOL_COOLDOWN_DAYS", 5, 0, 60),
        fund_flow_cache_path=os.getenv("FUND_FLOW_CACHE_PATH", "data/fund_flow_context.json"),
        enable_fund_flow_context=os.getenv("ENABLE_FUND_FLOW_CONTEXT", "1").strip() != "0",
        enable_news_context=os.getenv("ENABLE_NEWS_CONTEXT", "1").strip() != "0",
        enable_announcement_context=os.getenv("ENABLE_ANNOUNCEMENT_CONTEXT", "1").strip() != "0",
        enable_margin_eligibility_context=os.getenv("ENABLE_MARGIN_ELIGIBILITY_CONTEXT", "1").strip() != "0",
        news_lookback_days=int_setting("NEWS_LOOKBACK_DAYS", 14, 1, 90),
        announcement_lookback_days=int_setting("ANNOUNCEMENT_LOOKBACK_DAYS", 30, 1, 180),
        monitor_recent_days=int_setting("MONITOR_RECENT_DAYS", 5, 1, 60),
        monitor_intraday_drop_pct=float_setting("MONITOR_INTRADAY_DROP_PCT", 4.0, 0.5, 20.0),
        monitor_profit_lock_activation_pct=float_setting(
            "MONITOR_PROFIT_LOCK_ACTIVATION_PCT",
            18.0,
            0.0,
            100.0,
        ),
        monitor_pre_exit_calendar_gap_days=int_setting(
            "MONITOR_PRE_EXIT_CALENDAR_GAP_DAYS",
            7,
            0,
            30,
        ),
        performance_strategy_hold_days=int_setting("PERFORMANCE_STRATEGY_HOLD_DAYS", 5, 1, 30),
        alert_webhook_url=os.getenv("ALERT_WEBHOOK_URL", ""),
    )
