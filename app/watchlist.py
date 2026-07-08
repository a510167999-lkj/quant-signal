import json
from pathlib import Path
from typing import Any, Dict, List

from app.storage import write_json


DEFAULT_WATCHLIST = [
    {"symbol": "510300", "market": "etf", "name": "沪深300ETF"},
    {"symbol": "159915", "market": "etf", "name": "创业板ETF"},
    {"symbol": "600519", "market": "a", "name": "贵州茅台"},
    {"symbol": "000001", "market": "a", "name": "平安银行"},
]


def load_watchlist(path: str) -> List[Dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        save_watchlist(path, DEFAULT_WATCHLIST)
        return list(DEFAULT_WATCHLIST)
    with file_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        return list(DEFAULT_WATCHLIST)
    return data


def save_watchlist(path: str, items: List[Dict[str, Any]]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, items)
