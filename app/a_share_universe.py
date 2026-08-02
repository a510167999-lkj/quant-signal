import math
from datetime import datetime
from collections.abc import Callable
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from app.akshare_client import akshare_call
from app.storage import read_json, write_json


def _load_akshare():
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise RuntimeError("akshare is not installed. Run: pip install -r requirements.txt") from exc
    return ak


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _plain_code(value: Any) -> str:
    code = str(value or "").strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if code.startswith(prefix):
            return code[len(prefix) :]
    return code


def _is_excluded_name(name: str) -> bool:
    upper = name.upper()
    excluded_tokens = ["ST", "*ST", "退", "退市", "N "]
    return any(token in upper for token in excluded_tokens)


class AShareUniverseProvider:
    def __init__(
        self,
        cache_path: str,
        snapshot_loader: Callable[..., List[Dict[str, Any]]] | None = None,
    ) -> None:
        self.cache_path = cache_path
        self.snapshot_loader = snapshot_loader

    def snapshot(self, use_cache_on_error: bool = True) -> List[Dict[str, Any]]:
        if self.snapshot_loader is not None:
            return self.snapshot_loader(use_cache_on_error=use_cache_on_error)
        try:
            ak = _load_akshare()
            raw = akshare_call("stock_zh_a_spot", lambda: ak.stock_zh_a_spot())
            items = self._normalize_snapshot(raw)
            write_json(
                self.cache_path,
                {
                    "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                    "market_snapshot_source": "akshare:stock_zh_a_spot",
                    "items": items,
                },
            )
            return items
        except Exception:
            if use_cache_on_error:
                cached = read_json(self.cache_path, {"items": []})
                items = cached.get("items", []) if isinstance(cached, dict) else []
                if items:
                    cached_source = (
                        cached.get("market_snapshot_source")
                        if isinstance(cached, dict)
                        else None
                    )
                    return [
                        {
                            **item,
                            "market_snapshot_source": item.get(
                                "market_snapshot_source",
                                cached_source or "unknown:legacy_cache",
                            ),
                        }
                        for item in items
                    ]
            raise

    def _normalize_snapshot(self, frame: pd.DataFrame) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if frame is None or frame.empty:
            return items

        for row in frame.to_dict(orient="records"):
            code = _plain_code(row.get("代码"))
            name = str(row.get("名称") or "").strip()
            if len(code) != 6 or not code.isdigit() or not name:
                continue
            latest = _number(row.get("最新价"))
            amount = _number(row.get("成交额"))
            change_pct = _number(row.get("涨跌幅"))
            volume = _number(row.get("成交量"))
            items.append(
                {
                    "symbol": code,
                    "market": "a",
                    "name": name,
                    "market_snapshot_source": "akshare:stock_zh_a_spot",
                    "latest": latest,
                    "amount": amount,
                    "change_pct": change_pct,
                    "volume": volume,
                    "timestamp": str(row.get("时间戳") or ""),
                }
            )
        return items


def select_deep_scan_candidates(
    snapshot: List[Dict[str, Any]],
    max_deep: int,
    min_amount: float,
    min_price: float,
    max_price: float,
    industry_map: Optional[Dict[str, Dict[str, Any]]] = None,
    per_industry_top_n: int = 0,
    industry_top_n: int = 0,
) -> List[Dict[str, Any]]:
    candidates = []
    for item in snapshot:
        name = str(item.get("name") or "")
        latest = _number(item.get("latest"))
        amount = _number(item.get("amount"))
        change_pct = _number(item.get("change_pct"))
        if _is_excluded_name(name):
            continue
        if latest < min_price or latest > max_price:
            continue
        if amount < min_amount:
            continue
        if change_pct >= 9.8 or change_pct <= -9.8:
            continue
        amount_score = math.log10(max(amount, 1))
        momentum_score = 2.0 if -2 <= change_pct <= 5.5 else 0.0
        if 0.5 <= change_pct <= 4.5:
            momentum_score += 1.0
        if latest <= 0:
            continue
        item = dict(item)
        item["prefilter_score"] = round(amount_score + momentum_score, 4)
        industry = (industry_map or {}).get(str(item.get("symbol") or ""))
        if industry:
            item["industry"] = industry.get("industry")
            item["industry_rank"] = industry.get("industry_rank")
            item["industry_score"] = industry.get("industry_score")
        candidates.append(item)

    amount_sorted = sorted(candidates, key=lambda row: _number(row.get("amount")), reverse=True)
    amount_pool_count = len(amount_sorted)
    for rank, item in enumerate(amount_sorted, 1):
        item["amount_rank"] = rank
        item["amount_rank_pct"] = round(rank / amount_pool_count * 100, 2) if amount_pool_count else None

    candidates.sort(key=lambda row: (row["prefilter_score"], _number(row.get("amount"))), reverse=True)
    selected = candidates[:max_deep]
    if industry_map and per_industry_top_n and per_industry_top_n > 0:
        selected_by_symbol: Dict[str, Dict[str, Any]] = {}
        by_industry: Dict[str, List[Dict[str, Any]]] = {}
        for item in candidates:
            industry_name = str(item.get("industry") or "")
            if not industry_name:
                continue
            by_industry.setdefault(industry_name, []).append(item)
        for industry_items in by_industry.values():
            industry_items.sort(
                key=lambda row: (
                    _number(row.get("amount")),
                    row["prefilter_score"],
                    _number(row.get("change_pct")),
                ),
                reverse=True,
            )
        industry_rank = {
            industry_name: min(_number(item.get("industry_rank"), 999) for item in industry_items)
            for industry_name, industry_items in by_industry.items()
        }
        industry_names = sorted(
            by_industry,
            key=lambda industry_name: (
                industry_rank.get(industry_name, 999),
                -max(_number(item.get("amount")) for item in by_industry[industry_name]),
                industry_name,
            ),
        )
        if industry_top_n and industry_top_n > 0:
            industry_names = industry_names[:industry_top_n]
        for offset in range(per_industry_top_n):
            if len(selected_by_symbol) >= max_deep:
                break
            for industry_name in industry_names:
                industry_items = by_industry[industry_name]
                if offset >= len(industry_items):
                    continue
                item = industry_items[offset]
                selected_by_symbol[str(item.get("symbol"))] = item
                if len(selected_by_symbol) >= max_deep:
                    break
        if selected_by_symbol:
            if not industry_top_n or industry_top_n <= 0:
                for item in candidates:
                    if len(selected_by_symbol) >= max_deep:
                        break
                    selected_by_symbol[str(item.get("symbol"))] = item
            selected = list(selected_by_symbol.values())
            selected.sort(
                key=lambda row: (
                    row["prefilter_score"],
                    _number(row.get("amount")),
                    -_number(row.get("industry_rank"), 999),
                ),
                reverse=True,
            )
            selected = selected[:max_deep]
    selected_count = len(selected)
    for rank, item in enumerate(selected, 1):
        item["candidate_rank"] = rank
        item["candidate_rank_pct"] = round(rank / selected_count * 100, 2) if selected_count else None
    return selected
