from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


Market = Literal["a", "etf"]


class AnalyzeRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16, pattern=r"^[0-9A-Za-z.]+$")
    market: Market = "a"
    name: Optional[str] = Field(None, max_length=64)
    lookback_days: int = Field(360, ge=120, le=1600)
    adjust: Literal["", "qfq", "hfq"] = "qfq"


class WatchItem(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16, pattern=r"^[0-9A-Za-z.]+$")
    market: Market
    name: Optional[str] = Field(None, max_length=64)


class WatchlistUpdate(BaseModel):
    items: List[WatchItem] = Field(default_factory=list, max_length=100)


class HoldingItem(WatchItem):
    cost_price: Optional[float] = Field(None, gt=0)
    shares: Optional[float] = Field(None, gt=0)


class HoldingsUpdate(BaseModel):
    items: List[HoldingItem] = Field(default_factory=list, max_length=50)


class PricePoint(BaseModel):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class AnalyzeResponse(BaseModel):
    symbol: str
    market: Market
    name: Optional[str] = None
    source: str
    as_of: str
    action: str
    action_label: str
    score: float
    confidence: int
    last_close: float
    entry_zone: Dict[str, Optional[float]]
    levels: Dict[str, Optional[float]]
    trade_plans: Dict[str, object]
    reasons: List[str]
    risks: List[str]
    confirmations: List[str]
    indicators: Dict[str, Optional[float]]
    candles: List[PricePoint]
    backtest: Dict[str, object]
    disclaimer: str
