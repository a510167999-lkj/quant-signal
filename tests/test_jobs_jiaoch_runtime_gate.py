from types import SimpleNamespace

import pytest

import app.jobs as jobs
from app.config import Settings


class _MustNotReadIndustry:
    def build_map(self, *args, **kwargs):
        raise AssertionError("warm cache must not read industry data before source gate")


class _MustNotReadUniverse:
    def snapshot(self, *args, **kwargs):
        raise AssertionError("warm cache must not read the market snapshot before source gate")


class _BlockedService:
    def __init__(self, *_args, **_kwargs):
        self.industry = _MustNotReadIndustry()
        self.universe = _MustNotReadUniverse()

    def _recommendation_input_gate(self):
        return {
            "required": "jiaoch",
            "passed": False,
            "observed": [],
            "reasons": ["market_data_provider_identity_not_jiaoch"],
        }


def test_warm_market_cache_blocks_before_external_market_access(monkeypatch):
    monkeypatch.setattr(jobs, "get_settings", lambda: Settings())
    monkeypatch.setattr(jobs, "_get_data_provider", lambda: object())
    monkeypatch.setattr(jobs, "RecommendationService", _BlockedService)
    args = SimpleNamespace(max_deep=None, workers=1, lookback_days=620, adjust="qfq")

    with pytest.raises(ValueError, match="Jiaoch-only market runtime gate"):
        jobs._warm_market_cache(args)


def test_mootdx_l1_check_is_disabled_without_constructing_a_market_provider(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "_get_data_provider",
        lambda: pytest.fail("Mootdx check must not construct a market provider"),
    )

    with pytest.raises(ValueError, match="Jiaoch-only market runtime disables Mootdx L1 checks"):
        jobs.main(["mootdx-l1-check"])


def test_industry_history_check_is_disabled_without_constructing_a_market_provider(monkeypatch):
    monkeypatch.setattr(
        jobs,
        "_get_data_provider",
        lambda: pytest.fail("industry history check must not construct a market provider"),
    )

    with pytest.raises(ValueError, match="Jiaoch-only market runtime disables industry history checks"):
        jobs.main(["industry-history-check"])
