from app.announcement_context import (
    AnnouncementContextProvider,
    build_announcement_context,
    classify_announcement_title,
    _has_hard_blocker,
    _keyword_score,
)


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
