"""Lightweight immutable field contracts shared by Tushare collectors."""

from __future__ import annotations


STOCK_BASIC_FIELDS = (
    "ts_code",
    "symbol",
    "name",
    "exchange",
    "market",
    "list_status",
    "list_date",
    "delist_date",
)

# ``stock_basic`` is the provider wire contract.  Current-pool descriptors add
# one local provenance field without requesting it from the provider.
CURRENT_POOL_UNIVERSE_ITEM_FIELDS = (*STOCK_BASIC_FIELDS, "market_evidence")
CURRENT_POOL_MARKET_EVIDENCE_VALUES = ("provider", "inferred_nonlisted_code")

# A current-pool stock master receipt binds both the normalized universe rows
# and any provider-public legacy identities that were explicitly rejected.
# Keeping the rejected raw rows inside the receipt lets an offline verifier
# recompute the evidence hash instead of trusting an unexplained drop count.
CURRENT_POOL_UNIVERSE_RECEIPT_FIELDS = (
    "api_name",
    "params",
    "raw_row_count",
    "normalized_row_count",
    "rows_sha256",
    "excluded_invalid_identity_count",
    "excluded_invalid_identity_rows",
    "excluded_rows_sha256",
)
# Exact Jiaoch/Tushare provider defects observed on the 2026-07-13 VPS fetch.
# This is intentionally a closed allowlist: a future malformed identity must
# fail until its complete public-row fingerprint is separately audited.
# Tuple fields: ts_code, symbol, exchange, list_status, name, market.
CURRENT_POOL_EXCLUDED_IDENTITY_ALLOWLIST = (
    ("T600018.SH", "T600018", "SSE", "D", "上港集箱(退)", None),
    ("TS0018.SH", "TS0018", "SSE", "D", "上港集箱(退)", "主板"),
)
CURRENT_POOL_UNIVERSE_UNSIGNED_FIELDS = (
    "schema",
    "source_id",
    "as_of",
    "retrieved_at",
    "items",
    "partition_receipts",
    "excluded_invalid_identity_count",
    "partition_coverage",
    "risk_snapshot_complete",
    "risk_coverage",
    "production_recommendation_eligible",
)
