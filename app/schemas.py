from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.recommendation_contract import (
    recommendation_operation_contract_errors,
    recommendation_snapshot_publication_errors,
)


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


class PersonalFillRequest(BaseModel):
    kind: Literal["fill", "skip"]
    symbol: str = Field(min_length=1, max_length=16)
    side: Optional[Literal["buy", "sell"]] = None
    name: Optional[str] = Field(None, max_length=32)
    price: Optional[float] = Field(None, gt=0)
    shares: Optional[int] = Field(None, gt=0)
    commission: Optional[float] = Field(None, ge=0)
    as_of: Optional[str] = Field(None, min_length=10, max_length=10)
    planned_exit: Optional[str] = Field(None, min_length=10, max_length=10)


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


class RecommendationPriceRange(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)

    low: float = Field(..., gt=0)
    high: float = Field(..., gt=0)

    @model_validator(mode="after")
    def validate_order(self):
        if self.low > self.high:
            raise ValueError("entry range low must not exceed high")
        return self


class RecommendationLevels(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)

    support: float = Field(..., gt=0)
    resistance: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)


class TakeProfitOrReduceAdvice(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )

    condition: str = Field(..., min_length=1)
    trigger_price: float = Field(..., gt=0)
    action: str = Field(..., min_length=1)


class RecommendationOperationAdvice(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )

    action: str = Field(..., min_length=1)
    entry_zone: RecommendationPriceRange
    stop_loss: float = Field(..., gt=0)
    take_profit: float = Field(..., gt=0)
    holding_period: str = Field(..., min_length=1)
    invalidation: str = Field(..., min_length=1)
    trigger_conditions: List[str] = Field(..., min_length=1)
    take_profit_or_reduce: TakeProfitOrReduceAdvice
    invalidation_conditions: List[str] = Field(..., min_length=1)
    expected_holding_period: str = Field(..., min_length=1)


class RecommendationItem(BaseModel):
    model_config = ConfigDict(
        extra="allow",
        allow_inf_nan=False,
        str_strip_whitespace=True,
    )

    symbol: str = Field(..., min_length=1, max_length=16)
    market: Literal["a"]
    entry_zone: RecommendationPriceRange
    levels: RecommendationLevels
    trade_plans: Dict[str, object]
    operation_advice: RecommendationOperationAdvice
    auto_order: Literal[False]
    risks: List[str] = Field(..., min_length=1)

    @model_validator(mode="after")
    def validate_operation_contract(self):
        errors = recommendation_operation_contract_errors(
            self.model_dump()
        )
        if errors:
            raise ValueError(
                "invalid recommendation operation contract: "
                + ",".join(errors)
            )
        return self


class RecommendationPublicationReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["recommendation-publication-ledger/v1"]
    sequence: int = Field(..., ge=1)
    generated_at: str = Field(..., min_length=1)
    target_trade_date: str = Field(..., min_length=10, max_length=10)
    prior_symbols: List[str] = Field(default_factory=list, max_length=3)
    published_symbols: List[str] = Field(..., min_length=1, max_length=3)
    symbols_after_commit: List[str] = Field(..., min_length=1, max_length=3)
    seeded_from_legacy_history: bool
    previous_record_hash: Optional[str] = Field(
        None,
        pattern=r"^[0-9a-f]{64}$",
    )
    snapshot_sha256: str = Field(..., pattern=r"^[0-9a-f]{64}$")
    current_pool_audit_sha256: str = Field(
        ...,
        pattern=r"^[0-9a-f]{64}$",
    )
    profile_evidence_receipt_sha256: str = Field(
        ...,
        pattern=r"^[0-9a-f]{64}$",
    )
    record_hash: str = Field(..., pattern=r"^[0-9a-f]{64}$")


class RecommendationSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)

    generated_at: Optional[str] = None
    trade_date: Optional[str] = None
    signal_date: Optional[str] = None
    target_trade_date: Optional[str] = None
    items: List[RecommendationItem] = Field(default_factory=list, max_length=3)
    errors: List[Dict[str, object]] = Field(default_factory=list)
    recommendation_status: str = "no_snapshot"
    evidence_scope: str = "development_only"
    live_proof: bool = False
    auto_order: Literal[False] = False
    publication_receipt: Optional[RecommendationPublicationReceipt] = None
    summary: Dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_publication_contract(self):
        errors = recommendation_snapshot_publication_errors(
            self.model_dump()
        )
        if errors:
            raise ValueError(
                "invalid recommendation snapshot: "
                + ",".join(errors)
            )
        return self
