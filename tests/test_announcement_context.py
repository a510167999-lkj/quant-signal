import json
import time
from concurrent.futures import ThreadPoolExecutor

from app.announcement_context import (
    AnnouncementSourceError,
    AnnouncementContextProvider,
    build_announcement_context,
    classify_announcement_title,
    fetch_jiaoch_announcements,
    _has_hard_blocker,
    _keyword_score,
)
from app.research_pit_collector import HttpEntityResponse
from app.research_pit_sources import TushareSource


def test_announcement_keyword_score_blocks_hard_negative_events():
    title = "测试公司关于收到行政处罚决定书暨被立案调查的公告"

    assert _keyword_score(title) <= -14
    assert _has_hard_blocker(title) is True


def test_announcement_keyword_score_rewards_positive_events_without_blocking():
    title = "测试公司关于回购股份暨控股股东增持计划的公告"

    assert _keyword_score(title) > 0
    assert _has_hard_blocker(title) is False


def test_announcement_title_classification_tags_event_types():
    assert "regulatory_penalty" in classify_announcement_title("关于收到行政处罚决定书的公告")
    assert "contract_or_order" in classify_announcement_title("关于签订重大合同并获得订单的公告")
    assert "earnings_positive" in classify_announcement_title("2025年半年度业绩预增公告")
    assert "buyback_or_increase" in classify_announcement_title("关于回购公司股份进展的公告")
    categories = classify_announcement_title("关于回购注销部分限制性股票暨股权激励解除限售的公告")
    assert "equity_incentive" in categories
    assert "buyback_or_increase" not in categories


def test_announcement_provider_disabled_is_neutral(tmp_path):
    provider = AnnouncementContextProvider(str(tmp_path / "announcement.json"), enabled=False)

    payload = provider.evaluate("600519")

    assert payload["level"] == "neutral"
    assert payload["allow_recommendation"] is True
    assert payload["errors"] == ["disabled"]


def test_announcement_context_does_not_leak_future_announcements():
    payload = build_announcement_context(
        [
            {
                "title": "测试公司关于收到行政处罚决定书的公告",
                "published_at": "2025-08-01",
                "url": "https://www.cninfo.com.cn/test",
                "score": -14,
                "hard_blocker": True,
            }
        ],
        as_of_date="2025-07-01",
        lookback_days=60,
        updated_at="2025-07-01",
    )

    assert payload["level"] == "neutral"
    assert payload["allow_recommendation"] is True
    assert payload["announcement_count"] == 0


def test_announcement_context_blocks_known_recent_negative_announcements():
    payload = build_announcement_context(
        [
            {
                "title": "测试公司关于收到行政处罚决定书的公告",
                "published_at": "2025-06-15",
                "url": "https://www.cninfo.com.cn/test",
                "score": -14,
                "hard_blocker": True,
            }
        ],
        as_of_date="2025-07-01",
        lookback_days=60,
        updated_at="2025-07-01",
    )

    assert payload["level"] == "high_risk"
    assert payload["allow_recommendation"] is False
    assert payload["announcement_count"] == 1
    assert payload["event_counts"]["regulatory_penalty"] == 1


def test_fetch_jiaoch_announcements_uses_anns_d_and_maps_native_rows():
    captured = {}

    class Transport:
        def post(self, **kwargs):
            captured.update(kwargs)
            return HttpEntityResponse(
                status=200,
                headers={"Content-Type": "application/json"},
                body=json.dumps(
                    {
                        "code": 0,
                        "msg": None,
                        "data": {
                            "fields": [
                                "ann_date",
                                "ts_code",
                                "name",
                                "title",
                                "url",
                                "rec_time",
                            ],
                            "items": [
                                [
                                    "20260710",
                                    "600519.SH",
                                    "贵州茅台",
                                    "关于回购股份的公告",
                                    "https://static.cninfo.com.cn/finalpage/example.PDF",
                                    "2026-07-10 18:30:00",
                                ]
                            ],
                        },
                    },
                    ensure_ascii=False,
                ).encode("utf-8"),
            )

    source = TushareSource(
        name="jiaoch",
        api_url="https://jiaoch.site",
        allowed_hosts=("jiaoch.site",),
        token="unit-test-secret",
        request_protocol="tushare-path-per-interface/v1",
    )

    rows = fetch_jiaoch_announcements(
        "600519",
        "20260701",
        "20260713",
        source=source,
        transport=Transport(),
    )

    assert captured["url"] == "https://jiaoch.site/anns_d"
    request = json.loads(captured["body"])
    assert request["api_name"] == "anns_d"
    assert request["params"] == {
        "ts_code": "600519.SH",
        "start_date": "20260701",
        "end_date": "20260713",
    }
    assert rows == [
        {
            "title": "关于回购股份的公告",
            "published_at": "2026-07-10T18:30:00",
            "url": "https://static.cninfo.com.cn/finalpage/example.PDF",
            "score": 5,
            "hard_blocker": False,
            "categories": ["buyback_or_increase"],
        }
    ]


def test_announcement_provider_prefers_jiaoch_then_falls_back_with_stable_provenance(
    tmp_path, monkeypatch
):
    calls = {"jiaoch": 0, "cninfo": 0}

    def denied(*args, **kwargs):
        calls["jiaoch"] += 1
        raise AnnouncementSourceError("permission_denied")

    def fallback(symbol, start_date, end_date):
        calls["cninfo"] += 1
        return [
            {
                "title": "关于召开股东大会的公告",
                "published_at": "2026-07-10",
                "url": "https://static.cninfo.com.cn/finalpage/example.PDF",
                "score": 0,
                "hard_blocker": False,
                "categories": ["routine_governance"],
            }
        ]

    monkeypatch.setattr("app.announcement_context.fetch_jiaoch_announcements", denied)
    monkeypatch.setattr("app.announcement_context.fetch_cninfo_announcements", fallback)
    provider = AnnouncementContextProvider(str(tmp_path / "announcement.json"))

    first = provider.evaluate("600519")
    second = provider.evaluate("000001")

    assert calls == {"jiaoch": 1, "cninfo": 2}
    assert first["source"] == "cninfo_official_via_akshare"
    assert first["source_profile"] == "jiaoch_first"
    assert first["fallback_used"] is True
    assert first["fallback_reason_code"] == "permission_denied"
    assert second["fallback_reason_code"] == "permission_denied"


def test_announcement_provider_probes_denied_jiaoch_capability_only_once_concurrently(
    tmp_path, monkeypatch
):
    calls = {"jiaoch": 0, "cninfo": 0}

    def denied(*args, **kwargs):
        calls["jiaoch"] += 1
        time.sleep(0.02)
        raise AnnouncementSourceError("permission_denied")

    def fallback(symbol, start_date, end_date):
        calls["cninfo"] += 1
        return []

    monkeypatch.setattr("app.announcement_context.fetch_jiaoch_announcements", denied)
    monkeypatch.setattr("app.announcement_context.fetch_cninfo_announcements", fallback)
    provider = AnnouncementContextProvider(str(tmp_path / "announcement.json"))

    with ThreadPoolExecutor(max_workers=8) as executor:
        payloads = list(executor.map(provider._fetch, [f"6005{index:02d}" for index in range(8)]))

    assert calls == {"jiaoch": 1, "cninfo": 8}
    assert all(payload["fallback_reason_code"] == "permission_denied" for payload in payloads)
