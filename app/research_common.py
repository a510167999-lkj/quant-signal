"""研究回测共用的底层 helper。

从 research_backtest.py 抽出，供 research_backtest / research_equity 等模块共享，
避免循环 import（这些纯函数无外部依赖，放在最底层）。
"""
from datetime import datetime
from typing import Any


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _date_value(value: Any) -> datetime:
    return datetime.fromisoformat(str(value)[:10])


def _date_yyyymmdd(value: Any) -> str:
    moment = _date_value(value)
    return moment.strftime("%Y%m%d")
