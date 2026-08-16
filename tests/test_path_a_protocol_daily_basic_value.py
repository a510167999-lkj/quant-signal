from __future__ import annotations

import json
import math
from pathlib import Path

from app.factor_v3_path_a_protocol_daily_basic_value import (
    NULL_PE_IS_NOT_EXCLUSION,
    SOURCE_VERSION,
    VALUE_FIELDS,
    day_is_complete,
    locked_trade_dates,
    scrub_nonfinite,
)


def test_value_fields_are_valuation_and_size() -> None:
    assert VALUE_FIELDS == (
        "ts_code",
        "trade_date",
        "pe",
        "pe_ttm",
        "pb",
        "total_mv",
        "circ_mv",
    )
    assert SOURCE_VERSION == "jiaoch-daily-basic-value/v1"
    assert NULL_PE_IS_NOT_EXCLUSION is True


def test_scrub_nonfinite_maps_nan_to_none() -> None:
    payload = {"pe": math.nan, "pb": 1.2, "nested": [math.inf, 3]}
    cleaned = scrub_nonfinite(payload)
    assert cleaned["pe"] is None
    assert cleaned["pb"] == 1.2
    assert cleaned["nested"] == [None, 3]


def test_locked_trade_dates_clip_to_protocol_window(tmp_path: Path) -> None:
    spec = tmp_path / "run-spec.json"
    spec.write_text(
        json.dumps(
            {
                "sessions": [
                    "2023-06-26",
                    "2023-07-03",
                    "2025-07-01",
                    "2026-07-03",
                    "2026-07-04",
                ]
            }
        ),
        encoding="utf-8",
    )
    assert locked_trade_dates(spec) == [
        "2023-07-03",
        "2025-07-01",
        "2026-07-03",
    ]


def test_day_is_complete_requires_rows(tmp_path: Path) -> None:
    path = tmp_path / "2025-07-01.json"
    path.write_text(
        json.dumps(
            {
                "source_version": SOURCE_VERSION,
                "fields": list(VALUE_FIELDS),
                "row_count": 2,
                "rows": [
                    {"ts_code": "000001.SZ"},
                    {"ts_code": "000002.SZ"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert day_is_complete(path) is True
    path.write_text(json.dumps({"row_count": 0, "rows": []}), encoding="utf-8")
    assert day_is_complete(path) is False
