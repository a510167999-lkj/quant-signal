import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from app.akshare_client import akshare_call
from app.storage import read_json, write_json


SOURCE_NAME = "AKShare/Eastmoney dragon-tiger detail"
SOURCE_URL = "https://data.eastmoney.com/stock/tradedetail.html"
OFFICIAL_SOURCE_CAVEAT = (
    "Dragon-tiger data is sourced through AKShare's Eastmoney interface. "
    "SSE/SZSE exchange public trading information pages are preferred official references, "
    "but this adapter keeps only same-day list fields and drops future-return columns."
)
FUTURE_COLUMNS = {"上榜后1日", "上榜后2日", "上榜后5日", "上榜后10日"}


def _load_akshare():
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise RuntimeError("akshare is not installed. Run: pip install -r requirements.txt") from exc
    return ak


def _plain_code(value: Any) -> str:
    code = str(value or "").strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if code.startswith(prefix):
            code = code[len(prefix) :]
    digits = re.sub(r"\D", "", code)
    return digits[-6:] if len(digits) >= 6 else digits


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if pd.isna(number):
        return default
    return number


def _date_text(value: Any) -> str:
    text = str(value or "")[:10]
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except Exception:
        return text


def _yyyymmdd(value: str) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return text
    return datetime.fromisoformat(text[:10]).strftime("%Y%m%d")


def _institution_count(text: Any, side: str) -> int:
    match = re.search(r"(\d+)\s*家机构%s" % side, str(text or ""))
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def build_dragon_tiger_tags(context: Dict[str, Any]) -> List[str]:
    if not context:
        return []
    tags = {"lhb_on_list"}

    net_buy = _num(context.get("net_buy_amount"))
    net_ratio = _num(context.get("net_buy_ratio_pct"))
    turnover_ratio = _num(context.get("lhb_turnover_ratio_pct"))
    turnover_rate = _num(context.get("turnover_rate_pct"))
    change_pct = _num(context.get("change_pct"))
    institution_buy_count = int(_num(context.get("institution_buy_count")))
    institution_sell_count = int(_num(context.get("institution_sell_count")))
    reasons = " ".join(str(item) for item in context.get("reasons") or [])

    if net_buy > 0:
        tags.add("lhb_net_buy_positive")
    elif net_buy < 0:
        tags.add("lhb_net_buy_negative")
    if net_ratio >= 3:
        tags.add("lhb_net_buy_ratio_gte_3")
    if net_ratio >= 5:
        tags.add("lhb_net_buy_ratio_gte_5")
    if net_ratio <= -3:
        tags.add("lhb_net_buy_ratio_lte_neg3")
    if turnover_ratio >= 20:
        tags.add("lhb_turnover_ratio_gte_20")
    if turnover_ratio >= 40:
        tags.add("lhb_turnover_ratio_gte_40")
    if turnover_rate >= 10:
        tags.add("lhb_turnover_rate_gte_10")
    if turnover_rate >= 20:
        tags.add("lhb_turnover_rate_gte_20")
    if institution_buy_count > 0:
        tags.add("lhb_institution_buy")
    if institution_sell_count > 0:
        tags.add("lhb_institution_sell")

    if "涨幅" in reasons or change_pct >= 7:
        tags.add("lhb_reason_up")
    if "跌幅" in reasons or change_pct <= -7:
        tags.add("lhb_reason_down")
    if "换手率" in reasons:
        tags.add("lhb_reason_turnover")
    if "振幅" in reasons:
        tags.add("lhb_reason_amplitude")
    if "连续三个交易日" in reasons:
        tags.add("lhb_reason_multi_day")
    if "退市整理期" in reasons:
        tags.add("lhb_reason_delisting")

    return sorted(tags)


def _row_context(row: Dict[str, Any]) -> Dict[str, Any]:
    symbol = _plain_code(row.get("代码"))
    trade_date = _date_text(row.get("上榜日"))
    reason = str(row.get("上榜原因") or "").strip()
    interpretation = str(row.get("解读") or "").strip()
    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "name": str(row.get("名称") or "").strip(),
        "close": round(_num(row.get("收盘价")), 4),
        "change_pct": round(_num(row.get("涨跌幅")), 4),
        "net_buy_amount": round(_num(row.get("龙虎榜净买额")), 2),
        "buy_amount": round(_num(row.get("龙虎榜买入额")), 2),
        "sell_amount": round(_num(row.get("龙虎榜卖出额")), 2),
        "lhb_turnover": round(_num(row.get("龙虎榜成交额")), 2),
        "market_turnover": round(_num(row.get("市场总成交额")), 2),
        "net_buy_ratio_pct": round(_num(row.get("净买额占总成交比")), 4),
        "lhb_turnover_ratio_pct": round(_num(row.get("成交额占总成交比")), 4),
        "turnover_rate_pct": round(_num(row.get("换手率")), 4),
        "float_market_cap": round(_num(row.get("流通市值")), 2),
        "interpretations": [interpretation] if interpretation else [],
        "reasons": [reason] if reason else [],
        "institution_buy_count": _institution_count(interpretation, "买入"),
        "institution_sell_count": _institution_count(interpretation, "卖出"),
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
    }


def normalize_lhb_frame(frame: pd.DataFrame) -> List[Dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    safe_frame = frame.drop(columns=[column for column in FUTURE_COLUMNS if column in frame.columns])
    by_key: Dict[tuple, Dict[str, Any]] = {}

    for row in safe_frame.to_dict(orient="records"):
        context = _row_context(row)
        symbol = context.get("symbol")
        trade_date = context.get("trade_date")
        if len(str(symbol)) != 6 or not trade_date:
            continue
        key = (trade_date, symbol)
        current = by_key.get(key)
        if not current:
            by_key[key] = context
            continue

        for reason in context.get("reasons") or []:
            if reason and reason not in current["reasons"]:
                current["reasons"].append(reason)
        for interpretation in context.get("interpretations") or []:
            if interpretation and interpretation not in current["interpretations"]:
                current["interpretations"].append(interpretation)
        current["institution_buy_count"] = max(
            int(current.get("institution_buy_count") or 0),
            int(context.get("institution_buy_count") or 0),
        )
        current["institution_sell_count"] = max(
            int(current.get("institution_sell_count") or 0),
            int(context.get("institution_sell_count") or 0),
        )
        if _num(context.get("lhb_turnover")) > _num(current.get("lhb_turnover")):
            for field in [
                "close",
                "change_pct",
                "net_buy_amount",
                "buy_amount",
                "sell_amount",
                "lhb_turnover",
                "market_turnover",
                "net_buy_ratio_pct",
                "lhb_turnover_ratio_pct",
                "turnover_rate_pct",
                "float_market_cap",
            ]:
                current[field] = context[field]

    items = []
    for item in by_key.values():
        item = dict(item)
        item["tags"] = build_dragon_tiger_tags(item)
        items.append(item)
    return sorted(items, key=lambda row: (row["trade_date"], row["symbol"]))


class DragonTigerProvider:
    def __init__(self, cache_dir: str = "data/research_cache") -> None:
        self.cache_dir = cache_dir

    def build_contexts(
        self,
        start_date: str,
        end_date: str,
        use_cache_on_error: bool = True,
    ) -> Dict[str, Any]:
        start_key = _date_text(start_date)
        end_key = _date_text(end_date)
        cache_path = Path(self.cache_dir) / "dragon_tiger" / ("lhb_%s_%s.json" % (start_key, end_key))
        cached = read_json(str(cache_path), {})
        if isinstance(cached, dict) and cached.get("by_date"):
            return cached

        try:
            ak = _load_akshare()
            frame = akshare_call(
                "stock_lhb_detail_em",
                lambda: ak.stock_lhb_detail_em(start_date=_yyyymmdd(start_key), end_date=_yyyymmdd(end_key)),
            )
            items = normalize_lhb_frame(frame)
            by_date: Dict[str, Dict[str, Any]] = {}
            for item in items:
                by_date.setdefault(item["trade_date"], {})[item["symbol"]] = item
            payload = {
                "source": SOURCE_NAME,
                "source_url": SOURCE_URL,
                "start_date": start_key,
                "end_date": end_key,
                "fetched_at": datetime.now().isoformat(),
                "item_count": len(items),
                "day_count": len(by_date),
                "by_date": by_date,
                "summary": {
                    "source": SOURCE_NAME,
                    "start_date": start_key,
                    "end_date": end_key,
                    "item_count": len(items),
                    "day_count": len(by_date),
                    "caveat": OFFICIAL_SOURCE_CAVEAT,
                },
                "errors": [],
            }
            write_json(str(cache_path), payload)
            return payload
        except Exception as exc:
            if use_cache_on_error:
                cached = read_json(str(cache_path), {})
                if isinstance(cached, dict) and cached.get("by_date"):
                    cached = dict(cached)
                    cached.setdefault("errors", []).append(
                        {"source": "dragon_tiger_refresh", "message": str(exc), "used_cache": True}
                    )
                    return cached
            raise
