import contextlib
import io
import math
from datetime import datetime
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from app.akshare_client import akshare_call
from app.storage import read_json, write_json


SOURCE_NAME = "AKShare stock_board_industry_*"


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


def _first_existing(row: Dict[str, Any], names: List[str], default: Any = None) -> Any:
    for name in names:
        if name in row:
            return row.get(name)
    return default


class IndustryStrengthProvider:
    def __init__(self, cache_path: str, top_n: int = 12) -> None:
        self.cache_path = cache_path
        self.top_n = top_n

    def build_map(self, use_cache_on_error: bool = True) -> Dict[str, Any]:
        try:
            payload = self._fetch()
            write_json(self.cache_path, payload)
            return payload
        except Exception:
            if use_cache_on_error:
                cached = read_json(self.cache_path, {})
                if cached:
                    return cached
            return {"updated_at": None, "industries": [], "symbol_map": {}, "errors": ["industry_fetch_failed"]}

    def _fetch(self) -> Dict[str, Any]:
        ak = _load_akshare()
        raw = self._fetch_industries(ak)
        industries = self._normalize_industries(raw)
        top_industries = industries[: self.top_n]
        symbol_map: Dict[str, Dict[str, Any]] = {}
        errors = []

        for rank, industry in enumerate(top_industries, start=1):
            name = industry["name"]
            code = industry.get("code")
            try:
                cons = self._fetch_constituents(ak, str(code or ""), name)
                for symbol in self._extract_symbols(cons):
                    symbol_map[symbol] = {
                        "industry": name,
                        "industry_code": code,
                        "industry_rank": rank,
                        "industry_change_pct": industry["change_pct"],
                        "industry_score": industry["industry_score"],
                    }
            except Exception as exc:
                errors.append({"industry": name, "message": str(exc)})

        return {
            "source": SOURCE_NAME,
            "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "industries": top_industries,
            "symbol_map": symbol_map,
            "errors": errors[:20],
        }

    def _fetch_industries(self, ak: Any) -> Any:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return akshare_call(
                "stock_board_industry_name_em",
                lambda: ak.stock_board_industry_name_em(),
            )

    def _fetch_constituents(self, ak: Any, board_code: str, board_name: str) -> Any:
        symbol = board_code or board_name
        if not symbol:
            raise RuntimeError("AKShare industry constituents require board code or name")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return akshare_call(
                "stock_board_industry_cons_em",
                lambda: ak.stock_board_industry_cons_em(symbol=symbol),
            )

    def _normalize_industries(self, frame: Any) -> List[Dict[str, Any]]:
        output = []
        if frame is None:
            return output
        rows = frame.to_dict(orient="records") if hasattr(frame, "to_dict") else list(frame)
        if not rows:
            return output
        for row in rows:
            name = str(_first_existing(row, ["name", "板块名称", "名称", "行业名称"], "") or "").strip()
            if not name:
                continue
            code = str(_first_existing(row, ["code", "板块代码", "代码"], "") or "").strip()
            change_pct = _number(_first_existing(row, ["change_pct", "涨跌幅", "涨跌幅%", "涨幅"], 0))
            turnover = _number(_first_existing(row, ["turnover", "换手率"], 0))
            rising = _number(_first_existing(row, ["rising", "上涨家数"], 0))
            falling = _number(_first_existing(row, ["falling", "下跌家数"], 0))
            if change_pct < 0:
                continue
            breadth = (rising - falling) / max(rising + falling, 1)
            industry_score = change_pct * 2 + turnover * 0.2 + breadth * 3
            output.append(
                {
                    "name": name,
                    "code": code,
                    "change_pct": round(change_pct, 2),
                    "turnover": round(turnover, 2),
                    "breadth": round(breadth, 3),
                    "industry_score": round(industry_score, 4),
                }
            )
        output.sort(key=lambda item: item["industry_score"], reverse=True)
        return output

    def _extract_symbols(self, frame: Any) -> List[str]:
        symbols = []
        if frame is None:
            return symbols
        rows = frame.to_dict(orient="records") if hasattr(frame, "to_dict") else list(frame)
        if not rows:
            return symbols
        for row in rows:
            symbol = _plain_code(_first_existing(row, ["symbol", "代码", "股票代码", "证券代码"], ""))
            if len(symbol) == 6 and symbol.isdigit():
                symbols.append(symbol)
        return symbols
