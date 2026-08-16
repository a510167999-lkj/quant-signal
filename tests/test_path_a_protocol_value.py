from __future__ import annotations

from app.factor_v3_path_a_protocol_baseline_specs import apply_rank_key
from app.factor_v3_path_a_protocol_signal_specs import (
    VALUE_PE_NULL_TAG,
    VALUE_PE_TTM_OK_TAG,
)
from app.factor_v3_path_a_protocol_value import attach_value_fields


def test_pb_rank_prefers_cheaper_and_keeps_null_pe() -> None:
    trades = [
        {"symbol": "a", "pb": 3.0, "pe": None, "rank_score": 1.0},
        {"symbol": "b", "pb": 0.8, "pe": 12.0, "rank_score": 9.0},
        {"symbol": "c", "pb": None, "pe": None, "rank_score": 8.0},
    ]
    ranked = apply_rank_key(trades, "pb_asc")
    by_symbol = {row["symbol"]: row["rank_score"] for row in ranked}
    assert by_symbol["b"] > by_symbol["a"] > by_symbol["c"]
    assert trades[0]["rank_score"] == 1.0


def test_attach_value_tags_null_pe_without_dropping() -> None:
    trades = [
        {"symbol": "000001", "signal_date": "2025-07-01", "signal_tags": ["dn2"]},
        {"symbol": "000002", "signal_date": "2025-07-01", "signal_tags": ["dn2"]},
    ]
    attached = attach_value_fields(
        trades,
        value_index={
            ("000001.SZ", "2025-07-01"): {
                "pe": None,
                "pe_ttm": None,
                "pb": 0.9,
                "total_mv": 1.0,
                "circ_mv": 1.0,
            },
            ("000002.SZ", "2025-07-01"): {
                "pe": 8.0,
                "pe_ttm": 7.5,
                "pb": 1.2,
                "total_mv": 2.0,
                "circ_mv": 2.0,
            },
        },
        ts_by_symbol={"000001": "000001.SZ", "000002": "000002.SZ"},
    )
    assert len(attached) == 2
    assert VALUE_PE_NULL_TAG in attached[0]["signal_tags"]
    assert VALUE_PE_TTM_OK_TAG in attached[1]["signal_tags"]
    assert attached[0]["pb"] == 0.9
