from __future__ import annotations

import time
from typing import Any, Callable, Optional

import pandas as pd

from app.market_data import MarketDataError


def market_code_for_symbol(symbol: str) -> int:
    code = str(symbol or "").strip()
    if len(code) != 6 or not code.isdigit():
        raise MarketDataError("Invalid MOOTDX symbol: %s" % symbol)
    if code.startswith(("4", "8", "92")):
        return 2
    if code.startswith(("5", "6", "9")):
        return 1
    return 0


def _parse_servers(value: str) -> list[Optional[tuple[str, int]]]:
    servers: list[Optional[tuple[str, int]]] = []
    for item in str(value or "").split(","):
        raw = item.strip()
        if not raw or ":" not in raw:
            continue
        host, port_text = raw.rsplit(":", 1)
        try:
            servers.append((host.strip(), int(port_text)))
        except ValueError:
            continue
    return servers or [None]


def _default_client_factory(server: Optional[tuple[str, int]], timeout: float):
    from mootdx.quotes import Quotes

    kwargs = {
        "market": "std",
        "multithread": False,
        "heartbeat": False,
        "timeout": timeout,
        "auto_retry": False,
        "raise_exception": True,
    }
    if server is not None:
        kwargs["server"] = server
    return Quotes.factory(**kwargs)


class MootdxDailyProvider:
    def __init__(
        self,
        servers: str = "",
        timeout_seconds: float = 3.0,
        max_pages: int = 3,
        max_elapsed_seconds: float = 12.0,
        client_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.servers = _parse_servers(servers)
        self.timeout_seconds = max(float(timeout_seconds or 3.0), 0.5)
        self.max_pages = max(1, min(int(max_pages or 3), 10))
        self.max_elapsed_seconds = max(float(max_elapsed_seconds or 12.0), 1.0)
        self.client_factory = client_factory or _default_client_factory

    def _client(self, server):
        client = self.client_factory(server, self.timeout_seconds)
        if client is None:
            raise RuntimeError("client factory returned no client")
        connect = getattr(client, "connect", None)
        if server is not None and connect is not None and not connect(server[0], server[1]):
            self._close(client)
            raise RuntimeError("connection returned false")
        return client

    @staticmethod
    def _close(client) -> None:
        if client is None:
            return
        close = getattr(client, "close", None)
        if close is not None:
            close()

    @staticmethod
    def _normalize(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
        if raw is None or raw.empty:
            raise MarketDataError("MOOTDX returned no daily bars for %s" % symbol)
        frame = raw.copy()
        if "date" not in frame.columns and "datetime" in frame.columns:
            frame = frame.rename(columns={"datetime": "date"})
        if "volume" not in frame.columns and "vol" in frame.columns:
            frame = frame.rename(columns={"vol": "volume"})
        required = ["date", "open", "high", "low", "close", "volume"]
        missing = [name for name in required if name not in frame.columns]
        if missing:
            raise MarketDataError("MOOTDX bars missing columns: %s" % ", ".join(missing))
        columns = required + (["amount"] if "amount" in frame.columns else [])
        frame = frame[columns]
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
        for column in ["open", "high", "low", "close", "volume", "amount"]:
            if column in frame.columns:
                frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=required).sort_values("date").drop_duplicates("date", keep="last")
        price_ok = (
            (frame[["open", "high", "low", "close"]] > 0).all(axis=1)
            & (frame["low"] <= frame[["open", "close"]].min(axis=1))
            & (frame["high"] >= frame[["open", "close"]].max(axis=1))
            & (frame["low"] <= frame["high"])
        )
        amount_ok = frame["amount"].ge(0) if "amount" in frame.columns else True
        if not bool((price_ok & frame["volume"].ge(0) & amount_ok).all()):
            raise MarketDataError("MOOTDX daily bar quality validation failed for %s" % symbol)
        if pd.to_datetime(frame["date"]).dt.dayofweek.ge(5).any():
            raise MarketDataError("MOOTDX daily bars contain weekend dates for %s" % symbol)
        return frame.reset_index(drop=True)

    def history(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        errors = []
        started = time.monotonic()
        for server in self.servers:
            client = None
            try:
                client = self._client(server)
                pages = []
                for page_index in range(self.max_pages):
                    if time.monotonic() - started > self.max_elapsed_seconds:
                        raise TimeoutError("MOOTDX daily fallback exceeded total budget")
                    raw = client.bars(
                        symbol=str(symbol),
                        frequency=9,
                        start=page_index * 800,
                        offset=800,
                    )
                    if raw is None or raw.empty:
                        if page_index == 0:
                            raise MarketDataError("MOOTDX server returned empty first page")
                        break
                    pages.append(raw)
                    if len(raw) < 800:
                        break
                    page_dates = pd.to_datetime(raw.get("datetime"), errors="coerce")
                    if not page_dates.empty and page_dates.min().strftime("%Y-%m-%d") <= start_date:
                        break
                if not pages:
                    raise MarketDataError("MOOTDX returned no pages")
                frame = self._normalize(pd.concat(pages, ignore_index=True), symbol)
                frame = frame[(frame["date"] >= start_date) & (frame["date"] <= end_date)]
                if frame.empty:
                    raise MarketDataError("MOOTDX returned no bars in requested range")
                return frame.reset_index(drop=True)
            except Exception as exc:
                label = "default" if server is None else "%s:%s" % server
                errors.append("%s %s: %s" % (label, type(exc).__name__, exc))
            finally:
                self._close(client)
        raise MarketDataError("MOOTDX daily fallback failed: %s" % "; ".join(errors))

    def corporate_actions(self, symbol: str, after_date: str) -> list[dict[str, Any]]:
        errors = []
        for server in self.servers:
            client = None
            try:
                client = self._client(server)
                raw = client.xdxr(symbol=str(symbol))
                if raw is None:
                    raise MarketDataError("MOOTDX XDXR returned no response")
                if raw.empty:
                    return []
                frame = raw.copy()
                frame["date"] = pd.to_datetime(frame[["year", "month", "day"]], errors="coerce").dt.strftime("%Y-%m-%d")
                frame = frame[frame["date"] > after_date].sort_values("date")
                return frame.to_dict(orient="records")
            except Exception as exc:
                label = "default" if server is None else "%s:%s" % server
                errors.append("%s %s: %s" % (label, type(exc).__name__, exc))
            finally:
                self._close(client)
        raise MarketDataError("MOOTDX XDXR fallback failed: %s" % "; ".join(errors))
