import json
from pathlib import Path
from typing import Any, Dict, List

from app.analysis import build_analysis
from app.compat import model_to_dict
from app.schemas import AnalyzeRequest
from app.storage import write_json
from app.trading_calendar import now_cn


DEFAULT_HOLDINGS = [
    {"symbol": "159567", "market": "etf", "name": "港股创新药ETF银华"},
    {"symbol": "520700", "market": "etf", "name": "港股通创新药ETF万家"},
]

PRICE_FIELDS = ["price", "previous_close", "open", "high", "low", "bid1", "ask1"]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number


def load_holdings(path: str) -> List[Dict[str, Any]]:
    file_path = Path(path)
    if not file_path.exists():
        save_holdings(path, DEFAULT_HOLDINGS)
        return list(DEFAULT_HOLDINGS)
    with file_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        return list(DEFAULT_HOLDINGS)
    return data


def save_holdings(path: str, items: List[Dict[str, Any]]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, items)


def _normalize_l1_quote(quote: Dict[str, Any], analysis: Dict[str, Any]) -> Dict[str, Any]:
    if not quote:
        return {}
    normalized = dict(quote)
    reference_price = _num(analysis.get("last_close"))
    quote_price = _num(normalized.get("price"))
    scale = 1.0
    if reference_price > 0 and quote_price > 0:
        ratio = quote_price / reference_price
        if 9.5 <= ratio <= 10.5:
            scale = 0.1
        elif 0.095 <= ratio <= 0.105:
            scale = 10.0
    if scale != 1.0:
        for field in PRICE_FIELDS:
            if field in normalized and normalized[field] is not None:
                normalized[field] = round(_num(normalized[field]) * scale, 4)
        normalized["normalization_scale"] = scale
    return normalized


def _distance_pct(price: float, level: Any) -> float:
    level_num = _num(level)
    if price <= 0 or level_num <= 0:
        return None
    return round((price / level_num - 1) * 100, 2)


def _holding_status(analysis: Dict[str, Any], quote: Dict[str, Any]) -> Dict[str, Any]:
    levels = analysis.get("levels") or {}
    action = analysis.get("action")
    price = _num(quote.get("price")) or _num(analysis.get("last_close"))
    stop_loss = _num(levels.get("stop_loss"))
    support = _num(levels.get("support"))
    take_profit = _num(levels.get("take_profit"))
    if stop_loss > 0 and price <= stop_loss:
        return {"level": "critical", "label": "跌破止损", "message": "短线信号失效，优先减仓或退出。"}
    if support > 0 and price < support:
        return {"level": "warning", "label": "跌破支撑", "message": "持仓转弱，控制仓位。"}
    if action in {"SELL", "REDUCE"}:
        return {"level": "warning", "label": analysis.get("action_label") or "策略转弱", "message": "日线策略已转弱。"}
    if take_profit > 0 and price >= take_profit:
        return {"level": "info", "label": "达到止盈", "message": "可按计划止盈或上移止损。"}
    return {"level": "ok", "label": "持仓观察", "message": "未触发止损/支撑/止盈。"}


def build_holding_snapshot(item: Dict[str, Any], analysis: Dict[str, Any], quote: Dict[str, Any]) -> Dict[str, Any]:
    normalized_quote = _normalize_l1_quote(quote, analysis)
    current_price = _num(normalized_quote.get("price")) or _num(analysis.get("last_close"))
    levels = analysis.get("levels") or {}
    cost_price = _num(item.get("cost_price"), 0.0)
    shares = _num(item.get("shares"), 0.0)
    status = _holding_status(analysis, normalized_quote)
    return {
        "symbol": analysis.get("symbol") or item.get("symbol"),
        "market": analysis.get("market") or item.get("market"),
        "name": analysis.get("name") or item.get("name"),
        "as_of": analysis.get("as_of"),
        "source": analysis.get("source"),
        "current_price": round(current_price, 4) if current_price else None,
        "change_pct": normalized_quote.get("change_pct"),
        "amount": normalized_quote.get("amount"),
        "l1_quote": normalized_quote,
        "action": analysis.get("action"),
        "action_label": analysis.get("action_label"),
        "score": analysis.get("score"),
        "confidence": analysis.get("confidence"),
        "entry_zone": analysis.get("entry_zone") or {},
        "levels": levels,
        "trade_plans": analysis.get("trade_plans") or {},
        "status": status,
        "distance_to_stop_pct": _distance_pct(current_price, levels.get("stop_loss")),
        "distance_to_support_pct": _distance_pct(current_price, levels.get("support")),
        "distance_to_take_profit_pct": _distance_pct(current_price, levels.get("take_profit")),
        "cost_price": cost_price or None,
        "shares": shares or None,
        "position_value": round(current_price * shares, 2) if current_price and shares else None,
        "pnl_pct": round((current_price / cost_price - 1) * 100, 2) if current_price and cost_price else None,
        "reasons": (analysis.get("reasons") or [])[:5],
        "risks": (analysis.get("risks") or [])[:5],
        "backtest": analysis.get("backtest") or {},
    }


def build_holdings_snapshot(path: str, data_provider, disclaimer: str, l1_provider) -> Dict[str, Any]:
    holdings = load_holdings(path)
    symbols = [str(item.get("symbol") or "") for item in holdings]
    l1_payload = l1_provider.quotes(symbols) if l1_provider else {"quotes": {}, "errors": []}
    quote_map = l1_payload.get("quotes") or {}
    items = []
    errors = []
    for item in holdings:
        try:
            request = AnalyzeRequest(
                symbol=str(item.get("symbol") or ""),
                market=item.get("market") or "etf",
                name=item.get("name"),
                lookback_days=360,
                adjust="qfq",
            )
            analysis = model_to_dict(build_analysis(request, data_provider, disclaimer))
            items.append(build_holding_snapshot(item, analysis, quote_map.get(analysis["symbol"]) or {}))
        except Exception as exc:
            errors.append(
                {
                    "symbol": item.get("symbol"),
                    "market": item.get("market"),
                    "name": item.get("name"),
                    "message": str(exc),
                }
            )
    return {
        "updated_at": now_cn().isoformat(),
        "items": items,
        "errors": errors,
        "l1_quote": {
            "enabled": bool(l1_payload.get("enabled")),
            "available": bool(l1_payload.get("available")),
            "quote_count": len(quote_map),
            "elapsed_seconds": l1_payload.get("elapsed_seconds"),
            "errors": (l1_payload.get("errors") or [])[:5],
        },
        "disclaimer": disclaimer,
    }
