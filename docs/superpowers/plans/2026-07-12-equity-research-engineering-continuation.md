# Equity Research Engineering Continuation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or an equivalent local execution loop to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Advance the existing A-share research goal from PIT data safety toward a stock-only daily recommendation system, starting with the current membership artifact export blocker.

**Architecture:** The system must first produce audited, offline-replayable point-in-time artifacts before any strategy result can count. Exact daily membership receipts and derived membership generations are both valid authorities, but derived sessions must remain full-session BUY-ineligible and must carry their own immutable evidence, attempts, raw lineage, table roots, and manifest entries into exported artifacts.

**Tech Stack:** Python 3.12, SQLite, pytest, Ruff, local PIT receipt store, audited universe artifacts, stock-only research pipeline.

---

## File Structure

- Modify: `app/research_pit_store.py`
  - Owns coverage audit, artifact export, artifact load, table-root verification, and offline replay integrity checks.
- Modify: `tests/test_research_membership_audit.py`
  - Owns the mixed exact/quarantined membership artifact regression.
- Possibly modify: `tests/test_research_pit_store.py`
  - Only if existing helper assumptions need explicit table targeting.
- Do not modify strategy/backtest files until the artifact export/load gate is green.
- Do not stage or commit in this workspace unless the user explicitly asks; keep checkpoints in test output and the experiment ledger.

## Acceptance Gates

- `./.venv/bin/pytest tests/test_research_membership_audit.py -q`
  - Expected after Task 1: `1 passed`.
- `./.venv/bin/pytest tests/test_research_membership_audit.py tests/test_research_pit_store.py tests/test_research_pit_market_generation_audit.py tests/test_research_pit_generation_audit.py -q`
  - Expected after Task 2: all selected PIT artifact/store regressions pass.
- `./.venv/bin/ruff check .`
  - Expected after Task 2: `All checks passed!`
- Full suite only after the targeted gate is clean:
  - `./.venv/bin/pytest -q`
  - Expected: no new failures. Existing unrelated warnings may remain.

## Task 1: Make Membership Generations Exportable In Artifacts

**Files:**
- Modify: `app/research_pit_store.py`
- Test: `tests/test_research_membership_audit.py`

- [x] **Step 1: Verify the failing regression**

Run:

```bash
./.venv/bin/pytest tests/test_research_membership_audit.py -q
```

Observed failure:

```text
PITReceiptError: selected receipt snapshot is incomplete
```

Root cause:

```text
publish_universe_artifact currently selects every open-session bak_basic date as a required receipt.
For quarantined sessions, audit_coverage correctly uses membership_session_generations instead of a receipt, so the exporter must select exact bak_basic receipts only and export derived membership evidence separately.
```

- [x] **Step 2: Select only exact membership receipts**

In `publish_universe_artifact`, replace the `selected_keys.update(("bak_basic", row["cal_date"]) ...)` block with an exact-receipt query:

```python
selected_keys.update(
    ("bak_basic", row["cal_date"])
    for row in source.execute(
        """
        SELECT session.cal_date
        FROM trade_sessions AS session
        JOIN receipts AS receipt
          ON receipt.dataset = 'bak_basic'
         AND receipt.partition_key = session.cal_date
        WHERE session.exchange = 'SSE'
          AND session.is_open = 1
          AND session.cal_date BETWEEN ? AND ?
        ORDER BY session.cal_date
        """,
        (audit["start_date"], audit["end_date"]),
    )
)
```

- [x] **Step 3: Add selected membership generation temp table**

Create this temp table near `artifact_selected_market_generations`:

```python
source.execute(
    """
    CREATE TEMP TABLE artifact_selected_membership_generations (
        generation_id TEXT NOT NULL PRIMARY KEY
    ) WITHOUT ROWID
    """
)
source.executemany(
    "INSERT INTO artifact_selected_membership_generations VALUES (?)",
    [
        (str(ref["generation_id"]),)
        for ref in audit.get("membership_generation_refs", [])
    ],
)
```

- [x] **Step 4: Add membership evidence attempts to selected attempts**

Add after market generation attempts are selected:

```python
source.execute(
    """
    INSERT OR IGNORE INTO artifact_selected_attempts
    SELECT evidence.attempt_id
    FROM membership_generation_evidence AS evidence
    JOIN artifact_selected_membership_generations AS selected
      ON selected.generation_id = evidence.generation_id
    """
)
```

- [x] **Step 5: Add membership schema to destination DDL**

Append `_MEMBERSHIP_GENERATION_SCHEMA_SQL` after `_MARKET_SESSION_GENERATION_SCHEMA_SQL` and before artifact verification copies that need the membership tables:

```python
+ _MARKET_SESSION_GENERATION_SCHEMA_SQL
+ _MEMBERSHIP_GENERATION_SCHEMA_SQL
+ _ETF_PROXY_GENERATION_SCHEMA_SQL
```

- [x] **Step 6: Copy membership tables into the artifact**

Copy in foreign-key order:

```text
membership_session_generations
membership_generation_rows
membership_generation_evidence
membership_session_head
```

Each query must filter by `artifact_selected_membership_generations` and preserve deterministic ordering. The copied rows must be enough for `verify_membership_generation(trade_date)` to pass on the exported artifact without any network or source-store access.

- [x] **Step 7: Run the focused RED-to-GREEN test**

Run:

```bash
./.venv/bin/pytest tests/test_research_membership_audit.py -q
```

Expected:

```text
1 passed
```

## Task 2: Make Exported Membership Evidence Verifiable Offline

**Files:**
- Modify: `app/research_pit_store.py`
- Test: `tests/test_research_membership_audit.py`

- [x] **Step 1: Add membership table roots**

Extend `_verify_table_roots` so the manifest includes deterministic roots for:

```text
membership_session_generations
membership_generation_rows
membership_generation_evidence
membership_session_head
```

The root queries must use stable `ORDER BY` clauses matching primary-key order.

- [x] **Step 2: Add manifest membership section**

Add a top-level manifest key such as `membership_generations` containing:

```python
{
    "refs": audit.get("membership_generation_refs", []),
    "root_sha256": audit.get("membership_generation_root_sha256"),
    "quarantined_session_count": audit.get("quarantined_membership_session_count", 0),
    "maximum_consecutive_quarantined_sessions": audit.get(
        "maximum_consecutive_quarantined_sessions", 0
    ),
}
```

- [x] **Step 3: Teach the artifact loader the new manifest key**

Update the strict manifest key set in the artifact loader to include `membership_generations`. The loader must reject a manifest where the membership refs/root disagree with a fresh offline `audit_coverage` on the artifact database.

- [x] **Step 4: Strengthen the audit test to load and replay**

Extend `test_coverage_audit_accepts_exact_and_quarantined_membership_authorities` with:

```python
loaded = PITReceiptStore.load_universe_artifact(descriptor["path"])
try:
    loaded_audit = loaded.audit_coverage(start_date=EXACT, end_date=QUARANTINED)
    assert loaded_audit["coverage_audit_sha256"] == audit["coverage_audit_sha256"]
    assert loaded.verify_membership_generation(QUARANTINED)["source_kind"] == (
        "causal_carry_forward"
    )
finally:
    loaded.close()
```

Use the actual load API name present in `app/research_pit_store.py`; do not introduce a second artifact loader.

- [x] **Step 5: Run targeted artifact/store regressions**

Run:

```bash
./.venv/bin/pytest \
  tests/test_research_membership_audit.py \
  tests/test_research_pit_store.py \
  tests/test_research_pit_market_generation_audit.py \
  tests/test_research_pit_generation_audit.py \
  -q
```

Expected:

```text
all selected tests pass
```

## Task 3: Run Static And Full Local Gates

**Files:**
- No production changes unless a gate exposes a real regression.

- [x] **Step 1: Run Ruff**

Run:

```bash
./.venv/bin/ruff check .
```

Expected:

```text
All checks passed!
```

- [x] **Step 2: Run full pytest**

Run:

```bash
./.venv/bin/pytest -q
```

Expected:

```text
No test failures.
```

- [x] **Step 3: Record the gate result**

Update the experiment/iteration ledger with:

```text
iteration: auto-iter-022-causal-membership-quarantine
gate: membership artifact export/load
status: passed or failed
tests: exact commands and outputs
next_action: real data smoke if passed, root-cause investigation if failed
```

Use the repo's existing ledger mechanism; do not create a parallel tracking format.

## Task 4: Real Data Smoke For Stock-Only PIT Closure

**Files:**
- Modify only collector or ledger files if the smoke exposes a defect.

- [x] **Step 1: Pick a tiny development window**

Use a development-only date window with two or three open sessions. The goal is evidence closure, not strategy performance.

Acceptance:

```text
trade calendar receipts exist
market session generations exist for every open session
exact or derived membership authority exists for every open session
artifact exports
artifact loads
offline audit hash matches source audit hash
```

- [x] **Step 2: Run bounded collection**

Run the repo's existing collector CLI or job entrypoint with:

```text
stocks only
workers=1
development temporal role
no ETF
no order placement
```

Do not print tokens or secrets.

- [ ] **Step 3: Publish and load the artifact**

Blocked evidence:

```text
auto-iter-022 aborted at ledger sequence 76:
- run 1 exposed out-of-scope BJ daily rows with non-finite pre_close/change/pct_chg; fixed by test_daily_skips_nonfinite_out_of_scope_bj_rows.
- run 2 exhausted source transport attempts for stock_basic/SSE:P with connection refused.

auto-iter-023 aborted at ledger sequence 78:
- the single registered retry failed before source fetch because system clock synchronization could not be proven.
```

Use the same artifact path shape the repo already uses. Reject the smoke if export or load requires network access after the artifact is created.

- [ ] **Step 4: Record smoke result**

Record:

```text
date window
receipt counts
membership exact count
membership quarantined count
market generation count
artifact root sha256
coverage audit sha256
failure reason if any
```

## Task 5: Prepare Stock-Only Strategy Iteration After Evidence Closure

**Files:**
- Modify strategy/backtest files only after Task 1-4 pass.

- [ ] **Step 1: Freeze the experiment protocol**

Record these fields before any new signal work:

```text
universe artifact root
signal timestamp
next-open execution rule
cost model
slippage model
limit-up/limit-down handling
halt handling
position count
max gross exposure
single-name and industry caps
exit rule
kill-switch rule
primary metrics
rejection metrics
```

- [ ] **Step 2: Create the next hypothesis**

The next hypothesis must be one sentence and falsifiable, for example:

```text
Using only causal signal bars and audited membership, a low-turnover relative-strength plus liquidity filter improves OOS Wilson lower-bound win rate versus the frozen baseline without increasing max drawdown.
```

- [ ] **Step 3: Add one failing acceptance test before strategy code**

The test must prove that the strategy consumes an audited artifact/replay adapter and cannot fetch live provider data during backtest.

- [ ] **Step 4: Run walk-forward only after local evidence gates pass**

Report:

```text
overall win rate
95% Wilson lower bound
trade count
rolling 12-month return distribution
max drawdown
profit factor
cost stress
regime breakdown
reason for promotion or rejection
```

## Self-Review

- Spec coverage: The plan covers the current artifact blocker, offline verification, local test gates, real data smoke, and the first stock-only strategy iteration.
- Placeholder scan: No step uses TBD, TODO, or "implement later"; every task has concrete files, commands, and acceptance behavior.
- Type consistency: The plan uses existing concepts already present in the codebase: `audit_coverage`, `publish_universe_artifact`, `membership_session_generations`, `membership_generation_rows`, `membership_generation_evidence`, `membership_session_head`, and the existing artifact loader.
