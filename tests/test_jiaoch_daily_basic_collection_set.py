from __future__ import annotations

from datetime import date
import json
from types import SimpleNamespace

import pytest

from app import jiaoch_daily_basic_collection_set as collection


def test_closed_collection_has_exactly_one_daily_basic_points_route() -> None:
    spec = collection._request_spec(date(2024, 1, 2))

    assert spec["api_name"] == "daily_basic"
    assert spec["route_id"] == "factor-v3-daily-basic:points-primary:daily_basic"
    assert spec["params"] == {"trade_date": "20240102", "ts_code": ""}
    assert "moneyflow" not in repr(spec)


def test_synthetic_daily_basic_call_publishes_and_postverifies_one_raw_cas_set(
    monkeypatch, tmp_path
) -> None:
    spec = collection._request_spec(date(2024, 1, 2))
    response = {
        "code": 0,
        "msg": "success",
        "data": {
            "fields": spec["response_fields"],
            "items": [["000001.SZ", "20240102", 1, 1, 1, 1, 1, 1]],
        },
    }
    calls: list[str] = []

    class Transport:
        def post(self, *, url, **_kwargs):
            calls.append(url)
            return SimpleNamespace(status=200, body_complete=True, body=json.dumps(response).encode())

    monkeypatch.setattr(collection.points_common, "_transport_factory", lambda: Transport())
    publication = collection._collect_jiaoch_daily_basic_collection_set_with_route_credential(
        credential="test-only-credential",
        generation_id="11111111-1111-4111-8111-111111111111",
        policy_sha256=collection._validated_policy(
            __import__("app.factor_v3_daily_basic_runner", fromlist=["FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR"])
            .FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR["sha256"]
        ),
        output_root=tmp_path,
        trade_date="2024-01-02",
        timeout_seconds=1,
        max_attempts=1,
    )

    verified = collection.verify_jiaoch_daily_basic_collection_set(
        output_root=tmp_path,
        collection_set_relative_path=publication["collection_set_relative_path"],
        expected_collection_set_sha256=publication["collection_set_sha256"],
    )
    resumed = collection._collect_jiaoch_daily_basic_collection_set_with_route_credential(
        credential="test-only-credential",
        generation_id="11111111-1111-4111-8111-111111111111",
        policy_sha256=collection._validated_policy(
            __import__("app.factor_v3_daily_basic_runner", fromlist=["FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR"])
            .FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR["sha256"]
        ),
        output_root=tmp_path,
        trade_date="2024-01-02",
        timeout_seconds=1,
        max_attempts=1,
    )

    assert calls == ["https://jiaoch.site/daily_basic"]
    assert resumed["collection_set_sha256"] == publication["collection_set_sha256"]
    assert verified["verified"] is True


def test_failed_attempts_expose_only_bounded_safe_diagnostics(
    monkeypatch, tmp_path
) -> None:
    class Transport:
        def post(self, *, url, **_kwargs):
            return SimpleNamespace(status=503, body_complete=True, body=b"{}")

    monkeypatch.setattr(collection.points_common, "_transport_factory", lambda: Transport())
    policy = __import__(
        "app.factor_v3_daily_basic_runner",
        fromlist=["FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR"],
    ).FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR["sha256"]

    with pytest.raises(collection.JiaochDailyBasicCollectionError) as caught:
        collection._collect_jiaoch_daily_basic_collection_set_with_route_credential(
            credential="test-only-credential",
            generation_id="11111111-1111-4111-8111-111111111111",
            policy_sha256=policy,
            output_root=tmp_path,
            trade_date="2024-01-02",
            timeout_seconds=1,
            max_attempts=2,
        )

    diagnostic = caught.value.diagnostic
    assert diagnostic["schema"] == "jiaoch-factor-v3-daily-basic-collection-failure/v1"
    assert diagnostic["trade_date"] == "2024-01-02"
    assert len(diagnostic["attempts"]) == 2
    assert all(item["http_status"] == 503 for item in diagnostic["attempts"])
    assert all(item["outcome"] == "http_entity_rejected" for item in diagnostic["attempts"])
    assert all("token" not in repr(item).lower() for item in diagnostic["attempts"])
