"""研究回测的文件缓存 fetcher。

从 research_backtest.py 抽出，K 线与公告 miss 时拉外部源并落 JSON 文件缓存
（按 market/symbol/lookback/adjust 或 symbol/日期区间命名），与回测编排解耦。
"""
import os
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from app.announcement_context import fetch_cninfo_announcements
from app.jiaoch_live_market import is_jiaoch_stk_mins_v2_source
from app.market_data import AkshareDataProvider
from app.storage import read_json, write_json


def _history_with_file_cache(
    provider: AkshareDataProvider,
    market: str,
    symbol: str,
    lookback_days: int,
    adjust: str,
    cache_dir: str,
    *,
    require_jiaoch_stk_mins_v2: bool = False,
):
    require_v2 = require_jiaoch_stk_mins_v2 or os.getenv(
        "RESEARCH_REQUIRE_JIAOCHI_STK_MINS_V2", ""
    ).strip() in {"1", "true", "TRUE", "yes"}
    cache_path = Path(cache_dir) / ("%s_%s_%s_%s.json" % (market, symbol, lookback_days, adjust or "none"))
    cached = read_json(str(cache_path), {})
    if isinstance(cached, dict) and cached.get("records"):
        source = cached.get("source", "research-cache")
        if not require_v2 or is_jiaoch_stk_mins_v2_source(str(source)):
            return pd.DataFrame(cached["records"]), source

    frame, source = provider.history(symbol, market, lookback_days=lookback_days, adjust=adjust)
    write_json(
        str(cache_path),
        {
            "source": source,
            "records": frame.to_dict(orient="records"),
        },
    )
    return frame, source


def _announcements_with_file_cache(
    symbol: str,
    start_date: str,
    end_date: str,
    cache_dir: str,
) -> List[Dict[str, Any]]:
    cache_path = Path(cache_dir) / ("announcements_%s_%s_%s.json" % (symbol, start_date, end_date))
    cached = read_json(str(cache_path), {})
    if isinstance(cached, dict) and isinstance(cached.get("items"), list):
        return cached["items"]

    items = fetch_cninfo_announcements(symbol, start_date, end_date)
    write_json(
        str(cache_path),
        {
            "source": "CNINFO stock_zh_a_disclosure_report_cninfo",
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "items": items,
        },
    )
    return items
