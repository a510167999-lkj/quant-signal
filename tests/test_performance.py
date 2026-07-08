from app.performance import evaluate_recommendation_performance
from app.storage import append_jsonl
from tests.test_signals import sample_frame


class FakeProvider:
    def history(self, symbol, market, lookback_days=900, adjust="qfq"):
        return sample_frame("up", periods=80), "fake-provider"


def test_evaluate_recommendation_performance_tracks_next_bar_open(tmp_path):
    history_path = tmp_path / "recommendations_history.jsonl"
    append_jsonl(
        str(history_path),
        {
            "generated_at": "2025-01-10T09:00:00+08:00",
            "trade_date": "2025-01-10",
            "items": [
                {
                    "symbol": "600519",
                    "market": "a",
                    "name": "测试股票",
                    "as_of": "2025-01-10",
                    "action": "BUY",
                    "score": 3.5,
                }
            ],
        },
    )

    result = evaluate_recommendation_performance(str(history_path), FakeProvider(), limit=100)

    assert result["summary"]["total_recommendations"] == 1
    assert result["summary"]["matured_10d_count"] == 1
    assert result["summary"]["win_rate_10d_pct"] == 100
    item = result["items"][0]
    assert item["entry_date"] > "2025-01-10"
    assert item["return_10d_pct"] > 0
    assert item["source"] == "fake-provider"
