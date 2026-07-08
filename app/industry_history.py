import contextlib
import io
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from app.akshare_client import akshare_call
from app.signal_tags import build_industry_rotation_tags
from app.storage import read_json, write_json


SOURCE_NAME = "AKShare stock_board_industry_*"
MEMBERSHIP_CAVEAT = (
    "Industry board history is usable as-of by date; current board constituents are not "
    "historical constituents and must not be treated as no-lookahead membership."
)


def _load_akshare():
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise RuntimeError("akshare is not installed. Run: pip install -r requirements.txt") from exc
    return ak


def _first_existing(row: Dict[str, Any], names: List[str], default: Any = None) -> Any:
    for name in names:
        if name in row:
            return row.get(name)
    return default


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


def _akshare_board_items() -> List[Dict[str, Any]]:
    ak = _load_akshare()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        raw = akshare_call(
            "stock_board_industry_name_em",
            lambda: ak.stock_board_industry_name_em(),
        )
    return IndustryHistoryProvider._normalize_boards_frame(raw)


def _akshare_board_history(symbol: str, start_date: str, end_date: str) -> List[Dict[str, Any]]:
    ak = _load_akshare()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        raw = akshare_call(
            "stock_board_industry_hist_em",
            lambda: ak.stock_board_industry_hist_em(
                symbol=symbol,
                start_date=_yyyymmdd(start_date),
                end_date=_yyyymmdd(end_date),
                period="日k",
                adjust="",
            ),
        )
    return IndustryHistoryProvider._normalize_history_frame(raw)


class IndustryHistoryProvider:
    def __init__(self, cache_dir: str = "data/industry_history") -> None:
        self.cache_dir = Path(cache_dir)

    def boards(self, use_cache_on_error: bool = True) -> List[Dict[str, Any]]:
        cache_path = self.cache_dir / "boards.json"
        try:
            boards = self._fetch_boards()
            write_json(
                str(cache_path),
                {
                    "source": SOURCE_NAME,
                    "updated_at": datetime.now().isoformat(),
                    "items": boards,
                },
            )
            return boards
        except Exception:
            if use_cache_on_error:
                cached = read_json(str(cache_path), {"items": []})
                if isinstance(cached, dict) and cached.get("items"):
                    return cached["items"]
            raise

    def history(
        self,
        board_name: str,
        start_date: str,
        end_date: str,
        board_code: str = None,
        use_cache_on_error: bool = True,
    ) -> List[Dict[str, Any]]:
        cache_path = self.cache_dir / ("%s_%s_%s.json" % (board_name, _yyyymmdd(start_date), _yyyymmdd(end_date)))
        try:
            records = self._fetch_history(board_name, start_date, end_date, board_code=board_code)
            write_json(
                str(cache_path),
                {
                    "source": SOURCE_NAME,
                    "board_name": board_name,
                    "start_date": _yyyymmdd(start_date),
                    "end_date": _yyyymmdd(end_date),
                    "records": records,
                },
            )
            return records
        except Exception:
            if use_cache_on_error:
                cached = read_json(str(cache_path), {"records": []})
                if isinstance(cached, dict) and cached.get("records"):
                    return cached["records"]
            raise

    def strength_as_of(self, records: List[Dict[str, Any]], as_of_date: str) -> Dict[str, Any]:
        frame = pd.DataFrame(records)
        if frame.empty:
            return {"as_of": as_of_date, "available": False, "reason": "empty_history"}
        frame = frame[frame["date"] <= _date_text(as_of_date)].copy()
        if len(frame) < 20:
            return {"as_of": as_of_date, "available": False, "reason": "insufficient_history", "rows": len(frame)}
        frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
        close = frame["close"]
        ma20 = close.rolling(20, min_periods=10).mean()
        ma60 = close.rolling(60, min_periods=20).mean()
        return20 = close.pct_change(20).iloc[-1] if len(close) > 20 else 0
        return60 = close.pct_change(60).iloc[-1] if len(close) > 60 else 0
        latest = frame.iloc[-1]
        trend_bonus = 0
        if latest["close"] > ma20.iloc[-1]:
            trend_bonus += 1
        if len(frame) >= 60 and latest["close"] > ma60.iloc[-1]:
            trend_bonus += 1
        strength_score = _num(return20) * 120 + _num(return60) * 40 + trend_bonus
        return {
            "as_of": str(latest["date"]),
            "available": True,
            "rows": len(frame),
            "close": round(_num(latest["close"]), 4),
            "return_20d_pct": round(_num(return20) * 100, 2),
            "return_60d_pct": round(_num(return60) * 100, 2),
            "above_ma20": bool(latest["close"] > ma20.iloc[-1]),
            "above_ma60": bool(len(frame) >= 60 and latest["close"] > ma60.iloc[-1]),
            "strength_score": round(strength_score, 4),
        }

    def rotation_contexts(
        self,
        start_date: str,
        end_date: str,
        max_boards: int = 40,
        use_cache_on_error: bool = True,
    ) -> Dict[str, Dict[str, Any]]:
        boards = self.boards(use_cache_on_error=use_cache_on_error)
        rows_by_date: Dict[str, List[Dict[str, Any]]] = {}
        for board in boards[: max(max_boards, 0)]:
            name = board["name"]
            records = self.history(
                name,
                start_date=start_date,
                end_date=end_date,
                board_code=board.get("code"),
                use_cache_on_error=use_cache_on_error,
            )
            frame = pd.DataFrame(records)
            if frame.empty:
                continue
            frame = frame.sort_values("date").copy()
            frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
            frame["change_pct"] = pd.to_numeric(frame["change_pct"], errors="coerce").fillna(0)
            frame["ma20"] = frame["close"].rolling(20, min_periods=10).mean()
            frame["return_20d_pct"] = frame["close"].pct_change(20) * 100
            for row in frame.to_dict(orient="records"):
                date = str(row.get("date"))
                if date < _date_text(start_date):
                    continue
                close = _num(row.get("close"))
                ma20 = _num(row.get("ma20"))
                ret20 = _num(row.get("return_20d_pct"))
                rows_by_date.setdefault(date, []).append(
                    {
                        "above_ma20": bool(close > 0 and ma20 > 0 and close >= ma20),
                        "return_20d_positive": bool(ret20 > 0),
                        "return_20d_pct": ret20,
                        "advancing": bool(_num(row.get("change_pct")) > 0),
                    }
                )

        contexts: Dict[str, Dict[str, Any]] = {}
        for date, rows in rows_by_date.items():
            sample_count = len(rows)
            if not sample_count:
                continue
            returns = pd.Series([row["return_20d_pct"] for row in rows], dtype="float64")
            context = {
                "sample_count": sample_count,
                "above_ma20_pct": round(
                    sum(1 for row in rows if row["above_ma20"]) / sample_count * 100,
                    2,
                ),
                "return_20d_positive_pct": round(
                    sum(1 for row in rows if row["return_20d_positive"]) / sample_count * 100,
                    2,
                ),
                "advancing_pct": round(
                    sum(1 for row in rows if row["advancing"]) / sample_count * 100,
                    2,
                ),
                "median_return_20d_pct": round(float(returns.median()), 2),
                "top_return_20d_pct": round(float(returns.max()), 2),
                "return_20d_dispersion_pct": round(float(returns.quantile(0.75) - returns.quantile(0.25)), 2),
            }
            context["tags"] = build_industry_rotation_tags(context)
            contexts[date] = context
        return contexts

    def source_check(self, start_date: str, end_date: str, max_boards: int = 3) -> Dict[str, Any]:
        errors = []
        try:
            boards = self.boards(use_cache_on_error=True)
        except Exception as exc:
            boards = []
            errors.append({"stage": "boards", "message": str(exc)})
        checked = []
        for board in boards[: max(max_boards, 0)]:
            name = board["name"]
            try:
                records = self.history(
                    name,
                    start_date,
                    end_date,
                    board_code=board.get("code"),
                    use_cache_on_error=True,
                )
                checked.append(
                    {
                        "name": name,
                        "code": board.get("code"),
                        "history_rows": len(records),
                        "strength": self.strength_as_of(records, end_date),
                    }
                )
            except Exception as exc:
                errors.append({"name": name, "message": str(exc)})
        return {
            "source": SOURCE_NAME,
            "membership_caveat": MEMBERSHIP_CAVEAT,
            "start_date": _yyyymmdd(start_date),
            "end_date": _yyyymmdd(end_date),
            "board_count": len(boards),
            "checked": checked,
            "errors": errors[:20],
        }

    def _fetch_boards(self) -> List[Dict[str, Any]]:
        return _akshare_board_items()

    def _fetch_history(
        self,
        board_name: str,
        start_date: str,
        end_date: str,
        board_code: str = None,
    ) -> List[Dict[str, Any]]:
        return _akshare_board_history(board_code or board_name, start_date, end_date)

    @staticmethod
    def _normalize_boards_frame(frame: pd.DataFrame) -> List[Dict[str, Any]]:
        output = []
        if frame is None or frame.empty:
            return output
        for row in frame.to_dict(orient="records"):
            name = str(_first_existing(row, ["板块名称", "名称", "行业名称"], "") or "").strip()
            if not name:
                continue
            output.append(
                {
                    "name": name,
                    "code": str(_first_existing(row, ["板块代码", "代码"], "") or "").strip(),
                    "change_pct": round(_num(_first_existing(row, ["涨跌幅", "涨跌幅%", "涨幅"], 0)), 2),
                    "turnover": round(_num(_first_existing(row, ["换手率"], 0)), 2),
                }
            )
        return output

    @staticmethod
    def _normalize_history_frame(frame: pd.DataFrame) -> List[Dict[str, Any]]:
        records = []
        if frame is None or frame.empty:
            return records
        for row in frame.to_dict(orient="records"):
            date = _date_text(_first_existing(row, ["日期", "date"], ""))
            if not date:
                continue
            records.append(
                {
                    "date": date,
                    "open": _num(_first_existing(row, ["开盘", "open"], 0)),
                    "close": _num(_first_existing(row, ["收盘", "close"], 0)),
                    "high": _num(_first_existing(row, ["最高", "high"], 0)),
                    "low": _num(_first_existing(row, ["最低", "low"], 0)),
                    "volume": _num(_first_existing(row, ["成交量", "volume"], 0)),
                    "amount": _num(_first_existing(row, ["成交额", "amount"], 0)),
                    "change_pct": _num(_first_existing(row, ["涨跌幅", "change_pct"], 0)),
                    "turnover": _num(_first_existing(row, ["换手率", "turnover"], 0)),
                }
            )
        records.sort(key=lambda item: item["date"])
        return records
