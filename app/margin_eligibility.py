import re
import warnings
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from app.storage import read_json, write_json


SSE_MARGIN_URLS = {
    "collateral": "https://www.sse.com.cn/services/tradingservice/margin/info/againstmargin/data_960.js",
    "financing": "https://www.sse.com.cn/services/tradingservice/margin/info/againstmargin/data_961.js",
    "short": "https://www.sse.com.cn/services/tradingservice/margin/info/againstmargin/data_962.js",
}

SZSE_REPORT_URL = "https://www.szse.cn/api/report/ShowReport"
SZSE_UNDERLYING_CATALOG = "1834_xxpl"
SZSE_COLLATERAL_CATALOG = "1835_xxpl_snapshot"


def _plain_code(value: Any) -> str:
    code = str(value or "").strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if code.startswith(prefix):
            code = code[len(prefix) :]
    digits = re.sub(r"\D", "", code)
    return digits[-6:] if len(digits) >= 6 else digits


def _yes(value: Any) -> bool:
    return str(value or "").strip().upper() in {"Y", "YES", "TRUE", "1", "是"}


def _request_headers(referer: str) -> Dict[str, str]:
    return {
        "Referer": referer,
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
    }


def _parse_sse_static_date(text: str) -> Optional[str]:
    match = re.search(r"staticDate\s*:\s*['\"]([^'\"]+)['\"]", text)
    if match:
        return match.group(1).strip()
    match = re.search(r"showdate\s*=\s*['\"](\d{8})['\"]", text)
    if match:
        value = match.group(1)
        return "%s-%s-%s" % (value[:4], value[4:6], value[6:])
    return None


def parse_sse_margin_js(text: str) -> Tuple[List[Dict[str, str]], Optional[str]]:
    rows: List[Dict[str, str]] = []
    for seq, code, name in re.findall(
        r"\[\s*'([^']*)'\s*,\s*'([^']*)'\s*,\s*'([^']*)'\s*\]",
        text,
        flags=re.S,
    ):
        normalized_code = _plain_code(code)
        if len(normalized_code) != 6:
            continue
        rows.append(
            {
                "seq": seq.strip(),
                "symbol": normalized_code,
                "name": name.strip(),
            }
        )
    return rows, _parse_sse_static_date(text)


def _merge_symbol(
    symbol_map: Dict[str, Dict[str, Any]],
    symbol: str,
    **updates: Any,
) -> None:
    if len(symbol) != 6:
        return
    current = symbol_map.setdefault(
        symbol,
        {
            "symbol": symbol,
            "exchange": None,
            "name": None,
            "financing_underlying": False,
            "financing_eligible": False,
            "short_underlying": False,
            "short_eligible": False,
            "collateral_eligible": False,
            "price_limit": None,
            "as_of": None,
            "sources": [],
        },
    )
    for key, value in updates.items():
        if key == "sources":
            for item in value or []:
                if item and item not in current["sources"]:
                    current["sources"].append(item)
        elif value not in (None, ""):
            current[key] = value


class MarginEligibilityProvider:
    def __init__(self, cache_path: str = "data/margin_eligibility.json", enabled: bool = True) -> None:
        self.cache_path = cache_path
        self.enabled = enabled

    def build_map(self, use_cache_on_error: bool = True, as_of: str = None) -> Dict[str, Any]:
        if not self.enabled:
            return {
                "enabled": False,
                "symbol_map": {},
                "items": [],
                "summary": {"enabled": False},
                "errors": [],
            }
        try:
            payload = self._fetch(as_of=as_of)
            write_json(self.cache_path, payload)
            return payload
        except Exception as exc:
            if use_cache_on_error:
                cached = read_json(self.cache_path, {})
                if isinstance(cached, dict) and cached.get("symbol_map"):
                    cached = dict(cached)
                    cached.setdefault("errors", []).append(
                        {"source": "margin_eligibility_refresh", "message": str(exc), "used_cache": True}
                    )
                    return cached
            raise

    def source_check(self, as_of: str = None) -> Dict[str, Any]:
        payload = self.build_map(use_cache_on_error=False, as_of=as_of)
        summary = dict(payload.get("summary") or {})
        sample_symbols = ["600519", "601318", "000001", "000858", "510300", "159915"]
        symbol_map = payload.get("symbol_map") or {}
        return {
            "source": "SSE/SZSE official margin eligibility",
            "fetched_at": payload.get("fetched_at"),
            "summary": summary,
            "sample": {symbol: symbol_map.get(symbol) for symbol in sample_symbols if symbol in symbol_map},
            "errors": payload.get("errors", []),
            "source_urls": {
                "sse": "https://www.sse.com.cn/services/tradingservice/margin/info/againstmargin/",
                "szse_underlying": "https://www.szse.cn/disclosure/margin/object/index.html",
                "szse_collateral": "https://www.szse.cn/disclosure/margin/securites/index.html",
            },
        }

    def build_szse_underlying_map(
        self,
        as_of: str,
        cache_dir: str = None,
        use_cache_on_error: bool = True,
    ) -> Dict[str, Any]:
        if not self.enabled:
            return {
                "enabled": False,
                "scope": "szse_underlying_asof",
                "symbol_map": {},
                "items": [],
                "summary": {"enabled": False},
                "errors": [],
            }

        cache_path = None
        if cache_dir:
            cache_path = Path(cache_dir) / "margin_eligibility" / ("szse_underlying_%s.json" % str(as_of)[:10])
            cached = read_json(str(cache_path), {})
            if isinstance(cached, dict) and cached.get("symbol_map"):
                return cached

        try:
            frame = self._fetch_szse_xlsx(
                SZSE_UNDERLYING_CATALOG,
                "https://www.szse.cn/disclosure/margin/object/index.html",
                list(_candidate_dates(as_of)),
            )
            symbol_map: Dict[str, Dict[str, Any]] = {}
            self._merge_szse_underlying_frame(symbol_map, frame)
            items = sorted(symbol_map.values(), key=lambda item: item["symbol"])
            payload = {
                "enabled": True,
                "scope": "szse_underlying_asof",
                "requested_as_of": str(as_of)[:10],
                "source_as_of": frame.attrs.get("as_of"),
                "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "summary": {
                    "enabled": True,
                    "scope": "szse_underlying_asof",
                    "requested_as_of": str(as_of)[:10],
                    "source_as_of": frame.attrs.get("as_of"),
                    "total_count": len(items),
                    "financing_underlying_count": sum(1 for item in items if item.get("financing_underlying")),
                    "financing_count": sum(1 for item in items if item.get("financing_eligible")),
                    "short_underlying_count": sum(1 for item in items if item.get("short_underlying")),
                    "short_count": sum(1 for item in items if item.get("short_eligible")),
                },
                "items": items,
                "symbol_map": {item["symbol"]: item for item in items},
                "errors": [],
            }
            if cache_path:
                write_json(str(cache_path), payload)
            return payload
        except Exception as exc:
            if use_cache_on_error and cache_path:
                cached = read_json(str(cache_path), {})
                if isinstance(cached, dict) and cached.get("symbol_map"):
                    cached = dict(cached)
                    cached.setdefault("errors", []).append(
                        {"source": "szse_underlying_asof_refresh", "message": str(exc), "used_cache": True}
                    )
                    return cached
            raise

    def _fetch(self, as_of: str = None) -> Dict[str, Any]:
        symbol_map: Dict[str, Dict[str, Any]] = {}
        errors: List[Dict[str, Any]] = []

        try:
            self._fetch_sse(symbol_map)
        except Exception as exc:
            errors.append({"source": "sse", "message": str(exc)})

        try:
            self._fetch_szse(symbol_map, as_of=as_of)
        except Exception as exc:
            errors.append({"source": "szse", "message": str(exc)})

        items = sorted(symbol_map.values(), key=lambda item: item["symbol"])
        summary = {
            "enabled": True,
            "total_count": len(items),
            "financing_count": sum(1 for item in items if item.get("financing_eligible")),
            "short_count": sum(1 for item in items if item.get("short_eligible")),
            "collateral_count": sum(1 for item in items if item.get("collateral_eligible")),
            "sse_count": sum(1 for item in items if item.get("exchange") == "SSE"),
            "szse_count": sum(1 for item in items if item.get("exchange") == "SZSE"),
        }
        return {
            "enabled": True,
            "fetched_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "summary": summary,
            "items": items,
            "symbol_map": {item["symbol"]: item for item in items},
            "errors": errors,
        }

    def _fetch_sse(self, symbol_map: Dict[str, Dict[str, Any]]) -> None:
        for kind, url in SSE_MARGIN_URLS.items():
            response = requests.get(url, headers=_request_headers(url), timeout=10)
            response.raise_for_status()
            rows, static_date = parse_sse_margin_js(response.text)
            for row in rows:
                updates = {
                    "exchange": "SSE",
                    "name": row.get("name"),
                    "as_of": static_date,
                    "sources": [url],
                }
                if kind == "financing":
                    updates["financing_underlying"] = True
                    updates["financing_eligible"] = True
                elif kind == "short":
                    updates["short_underlying"] = True
                    updates["short_eligible"] = True
                elif kind == "collateral":
                    updates["collateral_eligible"] = True
                _merge_symbol(symbol_map, row["symbol"], **updates)

    def _fetch_szse(self, symbol_map: Dict[str, Dict[str, Any]], as_of: str = None) -> None:
        target_dates = list(_candidate_dates(as_of))
        underlying_frame = self._fetch_szse_xlsx(
            SZSE_UNDERLYING_CATALOG,
            "https://www.szse.cn/disclosure/margin/object/index.html",
            target_dates,
        )
        self._merge_szse_underlying_frame(symbol_map, underlying_frame)

        collateral_frame = self._fetch_szse_xlsx(
            SZSE_COLLATERAL_CATALOG,
            "https://www.szse.cn/disclosure/margin/securites/index.html",
            target_dates,
        )
        for row in collateral_frame.to_dict(orient="records"):
            symbol = _plain_code(row.get("证券代码"))
            _merge_symbol(
                symbol_map,
                symbol,
                exchange="SZSE",
                name=str(row.get("证券简称") or "").strip(),
                collateral_eligible=True,
                as_of=collateral_frame.attrs.get("as_of"),
                sources=["https://www.szse.cn/disclosure/margin/securites/index.html"],
            )

    def _fetch_szse_xlsx(self, catalog_id: str, referer: str, dates: List[str]) -> pd.DataFrame:
        last_error: Optional[Exception] = None
        for item_date in dates:
            try:
                frame = self._request_szse_xlsx(catalog_id, referer, item_date)
            except Exception as exc:
                last_error = exc
                continue
            if frame is not None and not frame.empty:
                frame.attrs["as_of"] = item_date
                return frame
        if last_error:
            raise last_error
        raise RuntimeError("No SZSE margin rows returned for %s" % ",".join(dates))

    def _request_szse_xlsx(self, catalog_id: str, referer: str, item_date: str) -> pd.DataFrame:
        response = requests.get(
            SZSE_REPORT_URL,
            params={
                "SHOWTYPE": "xlsx",
                "CATALOGID": catalog_id,
                "txtDate": item_date,
                "tab1PAGENO": "1",
                "TABKEY": "tab1",
            },
            headers=_request_headers(referer),
            timeout=15,
        )
        response.raise_for_status()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            frame = pd.read_excel(BytesIO(response.content), dtype=str)
        return frame.dropna(how="all")

    def _merge_szse_underlying_frame(
        self,
        symbol_map: Dict[str, Dict[str, Any]],
        frame: pd.DataFrame,
    ) -> None:
        for row in frame.to_dict(orient="records"):
            symbol = _plain_code(row.get("证券代码"))
            financing_underlying = _yes(row.get("融资标的"))
            short_underlying = _yes(row.get("融券标的"))
            financing_today = _yes(row.get("当日可融资")) if "当日可融资" in row else financing_underlying
            short_today = _yes(row.get("当日可融券")) if "当日可融券" in row else short_underlying
            _merge_symbol(
                symbol_map,
                symbol,
                exchange="SZSE",
                name=str(row.get("证券简称") or "").strip(),
                financing_underlying=financing_underlying,
                financing_eligible=financing_today,
                short_underlying=short_underlying,
                short_eligible=short_today,
                price_limit=str(row.get("涨跌幅限制") or "").strip() or None,
                as_of=frame.attrs.get("as_of"),
                sources=["https://www.szse.cn/disclosure/margin/object/index.html"],
            )


def _candidate_dates(as_of: str = None, max_days: int = 10) -> Iterable[str]:
    if as_of:
        try:
            start = datetime.fromisoformat(str(as_of)[:10]).date()
        except ValueError:
            start = date.today()
    else:
        start = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    for offset in range(max_days):
        yield (start - timedelta(days=offset)).isoformat()
