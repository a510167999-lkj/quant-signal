from __future__ import annotations

from datetime import date

from app import jiaoch_daily_basic_collection_set as collection


def test_closed_collection_has_exactly_one_daily_basic_points_route() -> None:
    spec = collection._request_spec(date(2024, 1, 2))

    assert spec["api_name"] == "daily_basic"
    assert spec["route_id"] == "factor-v3-daily-basic:points-primary:daily_basic"
    assert spec["params"] == {"trade_date": "20240102", "ts_code": ""}
    assert "moneyflow" not in repr(spec)

