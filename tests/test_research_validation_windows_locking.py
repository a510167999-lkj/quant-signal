from concurrent.futures import ThreadPoolExecutor


def test_experiment_ledger_locking_is_cross_platform(tmp_path):
    from app.research_validation import (
        append_experiment_event,
        read_experiment_ledger,
    )

    ledger = tmp_path / "windows-locking.jsonl"
    registered = append_experiment_event(
        str(ledger),
        {
            "event_id": "windows-sequential:registered",
            "experiment_id": "windows-sequential",
            "event_type": "registered",
        },
    )
    completed = append_experiment_event(
        str(ledger),
        {
            "event_id": "windows-sequential:completed",
            "experiment_id": "windows-sequential",
            "event_type": "completed",
        },
    )

    assert read_experiment_ledger(str(ledger)) == [registered, completed]

    def append_pair(index):
        experiment_id = f"windows-concurrent-{index}"
        append_experiment_event(
            str(ledger),
            {
                "event_id": f"{experiment_id}:registered",
                "experiment_id": experiment_id,
                "event_type": "registered",
            },
        )
        append_experiment_event(
            str(ledger),
            {
                "event_id": f"{experiment_id}:completed",
                "experiment_id": experiment_id,
                "event_type": "completed",
            },
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(append_pair, range(4)))

    rows = read_experiment_ledger(str(ledger))
    assert len(rows) == 10
    assert [row["sequence"] for row in rows] == list(range(1, 11))
    assert len({row["event_id"] for row in rows}) == 10
    assert len({row["record_hash"] for row in rows}) == 10
    assert rows[0]["previous_record_hash"] is None
    assert [row["previous_record_hash"] for row in rows[1:]] == [
        row["record_hash"] for row in rows[:-1]
    ]
