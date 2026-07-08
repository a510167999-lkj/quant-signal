from datetime import datetime
from typing import Any, Dict, List, Optional

from app.storage import read_jsonl


HORIZONS = [1, 3, 5, 10]


def _date_only(value: Any) -> Optional[str]:
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except Exception:
        return text[:10]


def _pct(value: float) -> float:
    return round(value * 100, 2)


def _first_entry_index(records: List[Dict[str, Any]], signal_date: str) -> Optional[int]:
    for index, row in enumerate(records):
        if str(row.get("date", "")) > signal_date:
            return index
    return None


def _recommendation_rows(history_path: str, limit: int) -> List[Dict[str, Any]]:
    rows = []
    runs = read_jsonl(history_path, limit=limit)
    for run in runs:
        run_date = _date_only(run.get("trade_date") or run.get("generated_at"))
        for item in run.get("items", []):
            if item.get("market") != "a":
                continue
            row = dict(item)
            row["generated_at"] = run.get("generated_at")
            row["trade_date"] = run_date
            rows.append(row)
    return rows


def evaluate_recommendation_performance(
    history_path: str,
    data_provider,
    limit: int = 500,
) -> Dict[str, Any]:
    tracked = []
    errors = []
    for item in _recommendation_rows(history_path, limit=limit):
        symbol = item.get("symbol")
        signal_date = _date_only(item.get("as_of") or item.get("trade_date") or item.get("generated_at"))
        if not symbol or not signal_date:
            continue
        try:
            frame, source = data_provider.history(symbol=symbol, market="a", lookback_days=900, adjust="qfq")
            records = frame.to_dict(orient="records")
            entry_index = _first_entry_index(records, signal_date)
            if entry_index is None:
                tracked.append(_pending_item(item, signal_date, "waiting_entry"))
                continue
            entry = records[entry_index]
            entry_price = float(entry.get("open") or entry.get("close"))
            returns = {}
            for horizon in HORIZONS:
                target_index = entry_index + horizon - 1
                if target_index < len(records):
                    exit_price = float(records[target_index].get("close"))
                    returns["return_%sd_pct" % horizon] = _pct(exit_price / entry_price - 1)
                    returns["exit_%sd_date" % horizon] = records[target_index].get("date")
            window = records[entry_index : min(entry_index + 10, len(records))]
            max_adverse = None
            if window:
                max_adverse = _pct(min(float(row.get("low") or entry_price) for row in window) / entry_price - 1)
            tracked.append(
                {
                    "symbol": symbol,
                    "name": item.get("name"),
                    "action": item.get("action"),
                    "score": item.get("score"),
                    "recommended_at": item.get("generated_at"),
                    "signal_date": signal_date,
                    "entry_date": entry.get("date"),
                    "entry_price": round(entry_price, 4),
                    "max_adverse_10d_pct": max_adverse,
                    "source": source,
                    "status": "matured_10d" if "return_10d_pct" in returns else "pending_10d",
                    **returns,
                }
            )
        except Exception as exc:
            errors.append({"symbol": symbol, "message": str(exc)})

    summary = _summarize(tracked)
    return {"summary": summary, "items": tracked[-200:][::-1], "errors": errors[:50]}


def _pending_item(item: Dict[str, Any], signal_date: str, status: str) -> Dict[str, Any]:
    return {
        "symbol": item.get("symbol"),
        "name": item.get("name"),
        "action": item.get("action"),
        "score": item.get("score"),
        "recommended_at": item.get("generated_at"),
        "signal_date": signal_date,
        "status": status,
    }


def _summarize(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    matured_10d = [item for item in items if item.get("return_10d_pct") is not None]
    wins_10d = [item for item in matured_10d if float(item.get("return_10d_pct") or 0) > 0]
    avg_10d = (
        sum(float(item.get("return_10d_pct") or 0) for item in matured_10d) / len(matured_10d)
        if matured_10d
        else 0
    )
    adverse_items = [item for item in matured_10d if item.get("max_adverse_10d_pct") is not None]
    avg_adverse = (
        sum(float(item.get("max_adverse_10d_pct") or 0) for item in adverse_items) / len(adverse_items)
        if adverse_items
        else 0
    )
    return {
        "total_recommendations": len(items),
        "matured_10d_count": len(matured_10d),
        "pending_10d_count": len(items) - len(matured_10d),
        "win_rate_10d_pct": round(len(wins_10d) / len(matured_10d) * 100, 2) if matured_10d else None,
        "avg_return_10d_pct": round(avg_10d, 2) if matured_10d else None,
        "avg_adverse_10d_pct": round(avg_adverse, 2) if adverse_items else None,
    }
