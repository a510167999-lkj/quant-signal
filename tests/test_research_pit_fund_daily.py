"""Pure-function validation layer for the ``fund_daily`` PIT receipt dataset.

These tests pin the contract constants and the three pure normalisation
functions (``_normalize_request_params`` / ``_canonical_partition_key`` /
``_canonical_wire_params`` / ``_normalize_rows``) for ``fund_daily``. They do
NOT exercise persistence: ``fund_daily`` is not yet wired into the promotion
pipeline, so we fail closed at the receipt-normalisation boundary only.
"""

from __future__ import annotations

import pytest

from app.research_pit_store import (
    DATASET_ENDPOINTS,
    FULL_SNAPSHOT_PARAM_KEYS,
    MAX_RAW_BYTES,
    NORMALIZED_FIELDS,
    OFFICIAL_ROW_CAPS,
    PITReceiptError,
    PRIMARY_KEYS,
    _canonical_partition_key,
    _canonical_wire_params,
    _normalize_request_params,
    _normalize_rows,
)
from app.research_proxy_data import (
    ETF_PROXY_REQUIRED_SYMBOLS,
    FUND_DAILY_FIELDS,
    FUND_DAILY_ROW_CAP,
)

PROXY_SYMBOL = ETF_PROXY_REQUIRED_SYMBOLS[0]  # 510300.SH
THIRD_ENTITY_SYMBOL = "510050.SH"  # real ETF, deliberately NOT in the contract


# --- frozen contract --------------------------------------------------------


def test_fund_daily_contract_constants_reference_proxy_module_without_drift():
    # Constants are imported from research_proxy_data so the ETF proxy layer and
    # the PIT receipt layer cannot drift apart on field shape or row cap.
    assert DATASET_ENDPOINTS["fund_daily"] == "fund_daily"
    assert NORMALIZED_FIELDS["fund_daily"] == FUND_DAILY_FIELDS
    assert OFFICIAL_ROW_CAPS["fund_daily"] == FUND_DAILY_ROW_CAP
    assert FULL_SNAPSHOT_PARAM_KEYS["fund_daily"] == {"ts_code", "start_date", "end_date"}
    assert "fund_daily" in MAX_RAW_BYTES
    assert PRIMARY_KEYS["fund_daily"] == ("ts_code", "trade_date")


# --- request normalisation --------------------------------------------------


def test_normalize_request_params_canonicalises_fund_daily():
    normalized = _normalize_request_params(
        "fund_daily",
        {"ts_code": "510300.sh", "start_date": "20240102", "end_date": "20240103"},
    )
    # ts_code is canonicalised to upper; dates to ISO; order is deterministic.
    assert normalized == {
        "ts_code": "510300.SH",
        "start_date": "2024-01-02",
        "end_date": "2024-01-03",
    }


def test_normalize_request_params_rejects_extra_param():
    with pytest.raises(PITReceiptError):
        _normalize_request_params(
            "fund_daily",
            {
                "ts_code": PROXY_SYMBOL,
                "start_date": "2024-01-02",
                "end_date": "2024-01-03",
                "trade_date": "2024-01-02",
            },
        )


def test_normalize_request_params_rejects_missing_param():
    with pytest.raises(PITReceiptError):
        _normalize_request_params(
            "fund_daily", {"ts_code": PROXY_SYMBOL, "start_date": "2024-01-02"}
        )


def test_normalize_request_params_rejects_inverted_window():
    with pytest.raises(PITReceiptError):
        _normalize_request_params(
            "fund_daily",
            {"ts_code": PROXY_SYMBOL, "start_date": "2024-01-03", "end_date": "2024-01-02"},
        )


def test_normalize_request_params_rejects_third_etf_symbol():
    # 510050.SH is a real, format-valid ETF but is NOT in the frozen proxy
    # contract. It must be rejected at param normalisation — before any wire
    # call — so a stray symbol can never be fetched. The window/format are
    # intentionally valid to prove the symbol gate fires independently.
    with pytest.raises(PITReceiptError):
        _normalize_request_params(
            "fund_daily",
            {
                "ts_code": THIRD_ENTITY_SYMBOL,
                "start_date": "2024-01-02",
                "end_date": "2024-01-03",
            },
        )


# --- partition key & wire params --------------------------------------------


def test_canonical_partition_key_is_symbol_colon_start_colon_end():
    key = _canonical_partition_key(
        "fund_daily",
        {"ts_code": "510300.SH", "start_date": "2024-01-02", "end_date": "2024-01-03"},
    )
    assert key == "510300.SH:2024-01-02:2024-01-03"


def test_canonical_wire_params_emits_yyyymmdd_dates():
    wire = _canonical_wire_params(
        "fund_daily",
        {"ts_code": "510300.SH", "start_date": "2024-01-02", "end_date": "2024-01-03"},
    )
    assert wire == {
        "ts_code": "510300.SH",
        "start_date": "20240102",
        "end_date": "20240103",
    }


# --- row normalisation (reuses normalize_etf_proxy_rows) --------------------


def _fund_row(ts_code: str, trade_date: str) -> list:
    return [
        ts_code,
        trade_date,
        10.0,  # open
        10.5,  # high
        9.8,  # low
        10.2,  # close
        10.0,  # pre_close
        0.2,  # change
        2.0,  # pct_chg
        1000.0,  # vol
        10000.0,  # amount
    ]


def _fund_params() -> dict:
    return {
        "ts_code": PROXY_SYMBOL,
        "start_date": "2024-01-02",
        "end_date": "2024-01-03",
    }


def test_normalize_rows_accepts_well_formed_fund_daily_sorted_by_date():
    params = _fund_params()
    partition_key = _canonical_partition_key("fund_daily", params)
    fields = list(FUND_DAILY_FIELDS)
    items = [
        _fund_row(PROXY_SYMBOL, "20240103"),
        _fund_row(PROXY_SYMBOL, "20240102"),  # out of order on purpose
    ]
    normalized = _normalize_rows("fund_daily", partition_key, params, fields, items)
    assert [row["trade_date"] for row in normalized] == ["2024-01-02", "2024-01-03"]
    assert normalized[0]["ts_code"] == PROXY_SYMBOL
    # numeric values are preserved as floats in declared field order
    assert normalized[0]["open"] == 10.0
    assert normalized[0]["high"] == 10.5


def test_normalize_rows_rejects_extra_field():
    params = _fund_params()
    partition_key = _canonical_partition_key("fund_daily", params)
    fields = [*FUND_DAILY_FIELDS, "surplus"]
    items = [_fund_row(PROXY_SYMBOL, "20240102") + [99]]
    with pytest.raises(PITReceiptError):
        _normalize_rows("fund_daily", partition_key, params, fields, items)


def test_normalize_rows_rejects_missing_field():
    params = _fund_params()
    partition_key = _canonical_partition_key("fund_daily", params)
    fields = list(FUND_DAILY_FIELDS[:-1])  # drop amount
    items = [_fund_row(PROXY_SYMBOL, "20240102")[:-1]]
    with pytest.raises(PITReceiptError):
        _normalize_rows("fund_daily", partition_key, params, fields, items)


def test_normalize_rows_rejects_reordered_fields():
    # Items are positional, so a complete-but-reordered field header would
    # silently relabel columns (e.g. swap `open` and `high`) if only the set
    # matched. The exact frozen order is enforced — order drift is rejected.
    params = _fund_params()
    partition_key = _canonical_partition_key("fund_daily", params)
    fields = [FUND_DAILY_FIELDS[1], FUND_DAILY_FIELDS[0], *FUND_DAILY_FIELDS[2:]]
    items = [_fund_row(PROXY_SYMBOL, "20240102")]
    with pytest.raises(PITReceiptError):
        _normalize_rows("fund_daily", partition_key, params, fields, items)


def test_normalize_rows_rejects_empty_items():
    # An empty items payload is never an acceptable proxy session: returning []
    # would mask a missing session as "legitimately empty". The receipt layer
    # refuses — only the coverage audit may reason about session absence.
    params = _fund_params()
    partition_key = _canonical_partition_key("fund_daily", params)
    with pytest.raises(PITReceiptError):
        _normalize_rows(
            "fund_daily", partition_key, params, list(FUND_DAILY_FIELDS), []
        )


def test_normalize_rows_rejects_duplicate_session():
    params = _fund_params()
    partition_key = _canonical_partition_key("fund_daily", params)
    fields = list(FUND_DAILY_FIELDS)
    items = [
        _fund_row(PROXY_SYMBOL, "20240102"),
        _fund_row(PROXY_SYMBOL, "20240102"),
    ]
    with pytest.raises(PITReceiptError):
        _normalize_rows("fund_daily", partition_key, params, fields, items)


def test_normalize_rows_rejects_invalid_ohlc():
    params = _fund_params()
    partition_key = _canonical_partition_key("fund_daily", params)
    fields = list(FUND_DAILY_FIELDS)
    bad = _fund_row(PROXY_SYMBOL, "20240102")
    bad[fields.index("high")] = 9.0  # high below open(10.0)/close(10.2)
    with pytest.raises(PITReceiptError):
        _normalize_rows("fund_daily", partition_key, params, fields, [bad])


def test_normalize_rows_rejects_row_outside_window():
    params = _fund_params()  # window [2024-01-02, 2024-01-03]
    partition_key = _canonical_partition_key("fund_daily", params)
    fields = list(FUND_DAILY_FIELDS)
    items = [_fund_row(PROXY_SYMBOL, "20240109")]  # outside window
    with pytest.raises(PITReceiptError):
        _normalize_rows("fund_daily", partition_key, params, fields, items)


def test_normalize_rows_does_not_mutate_input_items():
    params = _fund_params()
    partition_key = _canonical_partition_key("fund_daily", params)
    fields = list(FUND_DAILY_FIELDS)
    row = _fund_row(PROXY_SYMBOL, "20240102")
    items = [row]
    _normalize_rows("fund_daily", partition_key, params, fields, items)
    assert items == [row]
    assert items[0] == _fund_row(PROXY_SYMBOL, "20240102")
