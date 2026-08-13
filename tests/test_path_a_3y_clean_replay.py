from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.jiaoch_live_market import (
    JIAOCH_DAILY_CACHE_SOURCE_VERSION,
    _daily_cache_source,
    is_jiaoch_stk_mins_v2_source,
)
from app.research_cache import _history_with_file_cache
from app.storage import write_json


def test_v2_source_accepts_only_clean_jiaoch_label() -> None:
    good = _daily_cache_source("qfq")
    assert JIAOCH_DAILY_CACHE_SOURCE_VERSION in good
    assert is_jiaoch_stk_mins_v2_source(good)
    assert not is_jiaoch_stk_mins_v2_source("Jiaoch stk_mins daily qfq")
    assert not is_jiaoch_stk_mins_v2_source(
        "Jiaoch SQLite daily cache stale fallback qfq; " + JIAOCH_DAILY_CACHE_SOURCE_VERSION
    )
    assert not is_jiaoch_stk_mins_v2_source("AKShare stock_zh_a_daily fallback")


def test_history_cache_rejects_mixed_source_when_required(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    write_json(
        str(cache_dir / "a_600519_1400_qfq.json"),
        {
            "source": "AKShare stock_zh_a_daily fallback",
            "records": [{"date": "2023-07-03", "open": 1, "high": 1, "low": 1, "close": 1}],
        },
    )

    class Provider:
        def history(self, symbol, market, lookback_days=1400, adjust="qfq"):
            frame = pd.DataFrame(
                [
                    {
                        "date": "2023-07-03",
                        "open": 10.0,
                        "high": 11.0,
                        "low": 9.0,
                        "close": 10.5,
                        "volume": 1000,
                        "amount": 10000,
                    }
                ]
            )
            return frame, _daily_cache_source("qfq")

    frame, source = _history_with_file_cache(
        Provider(),
        "a",
        "600519",
        1400,
        "qfq",
        str(cache_dir),
        require_jiaoch_stk_mins_v2=True,
    )
    assert is_jiaoch_stk_mins_v2_source(source)
    assert float(frame.iloc[0]["close"]) == 10.5
