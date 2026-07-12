# Causal Membership Quarantine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Represent verified historical membership snapshot gaps as immutable, auditable, BUY-quarantined sessions without fabricating vendor facts or reading future snapshots.

**Architecture:** A small pure membership-contract module classifies semantic-empty raw responses and derives a deterministic projection from a strictly past anchor plus the target day's immutable market generation. `PITReceiptStore` owns generation persistence, audit, export, and offline verification; strict consumers require explicit session and row eligibility before producing candidates or validating trades.

**Tech Stack:** Python 3.12, SQLite WAL with `BEGIN IMMEDIATE`, SHA-256 canonical JSON roots, pytest, Ruff.

**Checkout constraint:** The PIT implementation in this checkout contains pre-existing uncommitted work required by this feature. Agents must edit only the files listed per task and must not commit implementation files; the primary agent owns diff review and any later integration decision.

---

### Task 0: Prospectively register iteration 022

**Files:**
- Append through API: `data/research_experiments/ledger.jsonl`

- [ ] **Step 1: Append the registered event through the locked ledger API**

Run `.venv/bin/python` with `app.research_validation.append_experiment_event` and this exact scientific contract:

```python
event = {
    "event_id": "auto-iter-022-causal-membership-quarantine:registered",
    "experiment_id": "auto-iter-022-causal-membership-quarantine",
    "event_type": "registered",
    "registration_timing": "prospective_before_implementation_or_new_collection",
    "hypothesis": (
        "A distinct immutable membership generation can represent a verified "
        "semantic-empty historical snapshot session using only a strictly past "
        "anchor and the target day's market generation, while fabricating no "
        "vendor receipt, reading no future snapshot, permitting no new BUY, and "
        "remaining reproducible and tamper-evident offline."
    ),
    "single_change": "add_causal_membership_generation_with_full_session_buy_quarantine",
    "expected_mechanism": (
        "Re-parse bound code-0/items-empty attempts; select MAX(nonempty snapshot "
        "date) strictly below the target; preserve carried and unknown observations "
        "in a separate generation; bind the target market generation; set session "
        "and every row ineligible; hash and publish the complete lineage."
    ),
    "primary_metric": (
        "zero future-snapshot inputs and zero signal-eligible rows or qualified BUY "
        "trades on derived sessions, with identical online/offline membership roots"
    ),
    "falsification_criterion": (
        "Any future/current-master metadata dependency, fabricated target receipt, "
        "unbound empty attempt, authority mismatch, recommendable derived row, hidden "
        "breadth quarantine, holding deletion, nondeterministic root, offline lineage "
        "gap, credential persistence, or failed full/static gate."
    ),
    "exit_criterion": (
        "Fixture poison tests and the bounded 2016-09-30..2016-10-10 development "
        "proof pass audit, artifact publish, offline load, tamper rejection, full "
        "pytest, Ruff, diff check, and independent review; no strategy/OOS run."
    ),
    "search_budget": {
        "live_window": ["2016-09-30", "2016-10-10"],
        "maximum_strategy_runs": 0,
        "maximum_live_collection_runs": 2,
        "variants": 1,
    },
    "temporal_role": "development",
    "temporal_contract_sha256": "cf70083e66f8706bf21e48b655c5b4342886e230c6a88dcfff04698d12b2e227",
    "final_oos_eligible": False,
}
```

- [ ] **Step 2: Verify ledger chain and event position**

Run:

```bash
.venv/bin/python - <<'PY'
from app.research_validation import read_experiment_ledger
rows = read_experiment_ledger("data/research_experiments/ledger.jsonl")
assert rows[-1]["event_id"] == "auto-iter-022-causal-membership-quarantine:registered"
assert rows[-1]["sequence"] == 75
print(rows[-1]["record_hash"])
PY
```

Expected: one 64-character hash; no raw ledger write and no secret output.

### Task 1: Pure semantic-empty and projection contract

**Files:**
- Create: `app/research_membership.py`
- Create: `tests/test_research_membership.py`

- [ ] **Step 1: Write RED classifier and derivation tests**

Tests must call the wished-for public pure API:

```python
classification = classify_membership_snapshot_body(raw, required_fields=BAK_BASIC_FIELDS)
assert classification.kind == "semantic_empty"
assert classification.response_code == 0
assert classification.items_count == 0

projection = derive_quarantined_membership(
    trade_date="2016-10-10",
    anchor_trade_date="2016-09-30",
    anchor_rows=[known_member],
    daily_codes={"600001.SH", "600002.SH"},
)
assert projection.rows[0]["signal_eligible"] is False
assert projection.rows[0]["metadata_stale_possible"] is True
assert projection.unknown_codes == ("600002.SH",)
assert projection.signal_session_eligible is False
```

Also assert malformed JSON, nonzero response code, missing canonical fields, non-empty items, target/evidence date mismatch, non-past anchor, duplicate codes, current-master metadata injection, and future-poison rows are rejected or ignored by construction.

- [ ] **Step 2: Run tests and observe the expected missing-module/API failure**

Run: `.venv/bin/pytest tests/test_research_membership.py -q`

Expected: FAIL because `app.research_membership` or its API does not exist.

- [ ] **Step 3: Implement the minimal pure contract**

Create immutable result dataclasses, the constants
`MEMBERSHIP_GENERATION_SCHEMA_VERSION = "membership-session-generations/v1"`
and
`MEMBERSHIP_POLICY_VERSION = "strictly-past-anchor-full-session-buy-quarantine/v1"`,
and these exact typed call surfaces:

- `classify_membership_snapshot_body(raw_bytes: bytes, *, required_fields: Sequence[str]) -> SnapshotBodyClassification`
- `derive_quarantined_membership(*, trade_date: str, anchor_trade_date: str | None, anchor_rows: Sequence[Mapping[str, Any]], daily_codes: AbstractSet[str]) -> MembershipProjection`
- `build_membership_manifest(*, trade_date: str, source_kind: str, anchor: Mapping[str, Any] | None, empty_evidence: Sequence[Mapping[str, Any]], market_generation: Mapping[str, Any], temporal_authority: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]`

The parser must reject duplicate JSON keys, require the exact requested fields to be present, and never accept an arbitrary `invalid_json` event as semantic-empty evidence. The derivation must read only arguments supplied by the caller, sort all rows/codes canonically, and mark all output rows and the session ineligible.

- [ ] **Step 4: Run pure tests and static checks**

Run:

```bash
.venv/bin/pytest tests/test_research_membership.py -q
.venv/bin/ruff check app/research_membership.py tests/test_research_membership.py
```

Expected: all tests pass; Ruff exits 0.

### Task 2: Immutable membership generation persistence

**Files:**
- Modify: `app/research_pit_store.py`
- Create: `tests/test_research_membership_generations.py`

- [ ] **Step 1: Write RED lifecycle and adversarial tests**

Build a two-session fixture with one non-empty past anchor, a target semantic-empty attempt plus terminal event, and a published target market generation. Tests must prove:

```python
generation = store.begin_or_resume_membership_generation(now, "2016-10-10")
staged = store.stage_membership_generation(generation["generation_id"])
published = store.publish_membership_generation(generation["generation_id"])
verified = store.verify_membership_generation("2016-10-10")
assert published["manifest_sha256"] == verified["manifest_sha256"]
assert verified["source_kind"] == "causal_carry_forward"
assert verified["signal_session_eligible"] is False
assert store.active_membership_generation("2016-10-10")["generation_id"] == generation["generation_id"]
```

Add cases for pre-anchor quarantine, exact-date receipt rejection, latest strictly past anchor selection, raw semantic-empty reparse, same-source/temporal authority, idempotent resume, concurrent begin uniqueness, immutable conflict, future snapshot poison invariance, current-master poison invariance, market-head staleness, row/attempt/event tampering, and secret absence.

- [ ] **Step 2: Run tests and observe missing schema/API failures**

Run: `.venv/bin/pytest tests/test_research_membership_generations.py -q`

Expected: FAIL because membership generation tables and methods do not exist.

- [ ] **Step 3: Add schema and lifecycle with atomic publication**

Add `_MEMBERSHIP_GENERATION_SCHEMA_SQL` defining:

```text
membership_session_generations
membership_generation_rows
membership_generation_evidence
membership_session_head
```

Persist exact attempt IDs and their raw/request-semantics/terminal evidence. Bind the target market generation ID, manifest, and lineage. Recompute projection and manifest during publish and verify; use one collecting generation per target date, a monotonic sequence, a content-addressed head, and `BEGIN IMMEDIATE`. Do not change or insert `daily_universe` rows and do not create an empty `bak_basic` receipt.

- [ ] **Step 4: Run lifecycle, existing store, and concurrency suites**

Run:

```bash
.venv/bin/pytest tests/test_research_membership_generations.py tests/test_research_pit_store.py tests/test_research_pit_generations.py tests/test_research_market_session_generations.py -q
.venv/bin/ruff check app/research_pit_store.py tests/test_research_membership_generations.py
```

Expected: all selected tests pass; no schema regression.

### Task 3: Controlled collector continuation for semantic-empty sessions

**Files:**
- Modify: `app/research_pit_collector.py`
- Modify: `app/jobs.py` only if report serialization requires it
- Modify: `tests/test_research_pit_collector.py`
- Modify: `tests/test_research_market_session_collector.py`
- Create: `tests/test_research_membership_collector.py`

- [ ] **Step 1: Write RED orchestration tests**

Use a deterministic fake transport to prove three identical code-0 empty bodies produce a controlled `semantic_empty` membership result, then collect/publish the target four-shard market generation and membership generation. Assert:

```python
assert report["semantic_empty_session_count"] == 1
assert report["membership_authority_count"] == len(report["open_sessions"])
assert report["derived_membership_generation_count"] == 1
assert report["controlled_request_lineage_complete"] is True
with sqlite3.connect(store.database_path) as connection:
    assert connection.execute(
        "SELECT 1 FROM receipts WHERE dataset='bak_basic' AND partition_key=?",
        ("2016-10-10",),
    ).fetchone() is None
assert store.active_membership_generation("2016-10-10")["signal_session_eligible"] is False
```

Mixed empty/non-empty retries, malformed bodies, API errors, incomplete bodies, authority changes, failed market generation, and cancellation must still fail with exact terminal evidence and no published membership head.

- [ ] **Step 2: Run tests and observe the existing collector abort**

Run: `.venv/bin/pytest tests/test_research_membership_collector.py -q`

Expected: FAIL because the current planned response empty path raises and prevents market collection.

- [ ] **Step 3: Implement a dedicated membership collection result**

Keep `fetch_partition()` default semantics unchanged. Add a membership-only wrapper that may return `stored`, `reused`, or raw-verified `semantic_empty`; it must consume all bounded retries, preserve every attempt/event, and never convert other failures. Change `_collect_once()` phases to:

```text
calendar + stock generation
membership snapshot outcomes per session
market generation per session
exact membership authority or derived generation per session
controlled sparse-receipt + membership-lineage completeness check
```

Reports must disclose exact versus semantic-empty versus pre-anchor counts and membership generation roots, remain deterministic across workers, and contain no token or request body.

- [ ] **Step 4: Run collector regression suites**

Run:

```bash
.venv/bin/pytest tests/test_research_membership_collector.py tests/test_research_pit_collector.py tests/test_research_pit_collector_adversarial.py tests/test_research_pit_collector_mid_failure_recovery.py tests/test_research_market_session_collector.py tests/test_research_pit_collector_throughput.py -q
.venv/bin/ruff check app/research_pit_collector.py app/jobs.py tests/test_research_membership_collector.py
```

Expected: all tests pass; workers=1 remains the default.

### Task 4: Coverage audit, artifact, and offline lineage

**Files:**
- Modify: `app/research_pit_store.py`
- Modify: `app/research_artifact_replay.py` if evidence authority fields are centralized there
- Modify: `tests/test_research_pit_store.py`
- Modify: `tests/test_research_artifact_replay.py`
- Create: `tests/test_research_membership_artifact.py`

- [ ] **Step 1: Write RED online/offline and tamper tests**

Create a two-session audited fixture and assert the coverage report includes:

```python
assert audit["exact_membership_session_count"] == 1
assert audit["quarantined_membership_session_count"] == 1
assert audit["pre_anchor_session_count"] == 0
assert audit["maximum_consecutive_quarantined_sessions"] == 1
assert len(audit["membership_generation_root_sha256"]) == 64
```

Publish and reopen the artifact, compare the complete coverage audit/root, and independently tamper with anchor date, row flags, empty-attempt raw, terminal event, market generation ID, membership manifest/head, and table-root metadata. Every tamper must fail loading. Assert there is no target-day `bak_basic` receipt in the artifact and the bound semantic-empty raw/evidence is present.

- [ ] **Step 2: Run tests and observe current same-day receipt failure**

Run: `.venv/bin/pytest tests/test_research_membership_artifact.py -q`

Expected: FAIL at current `audit_coverage()` same-day non-empty receipt requirement.

- [ ] **Step 3: Upgrade audit/export/loader as one semantic unit**

Bump store, coverage-audit, and universe-artifact schema versions with explicit legacy handling. Coverage must resolve exact same-day membership first and otherwise require the active derived generation. Export only actual anchor receipts plus bound semantic-empty attempts and the target market generation. Add all membership tables to table roots, materialized ranges, artifact logical metadata, artifact root, evidence authority, structural verification, and offline re-audit.

Exact sessions retain existing suspension reconciliation. Derived sessions verify known/unknown/absent sets, factors/limits for every observed daily code through the market-generation contract, full-session quarantine, anchor strictness, authority identity, and deterministic lineage; they must not pretend absent carried members have a same-day suspension event.

- [ ] **Step 4: Run artifact and temporal suites**

Run:

```bash
.venv/bin/pytest tests/test_research_membership_artifact.py tests/test_research_pit_store.py tests/test_research_artifact_replay.py tests/test_research_temporal_validation_e2e.py tests/test_research_market_generation_coverage.py -q
.venv/bin/ruff check app/research_pit_store.py app/research_artifact_replay.py tests/test_research_membership_artifact.py
```

Expected: all selected tests pass and offline audit equals online audit.

### Task 5: Strict candidate, breadth, and validation gates

**Files:**
- Modify: `app/research_pit_store.py`
- Modify: `app/research_backtest.py`
- Modify: `app/research_context.py`
- Modify: `app/research_validation.py`
- Modify: `tests/test_research_stock_only_backtest.py`
- Modify: `tests/test_research_stock_market_context.py`
- Modify: `tests/test_research_validation.py`

- [ ] **Step 1: Write RED consumer tests**

Assert exact item projections expose explicit true/false metadata, derived projections expose all known and unknown observations, and strict consumers fail closed:

```python
assert exact["signal_session_eligible"] is True
assert exact["signal_eligible"] is True
assert derived_known["signal_session_eligible"] is False
assert derived_known["signal_eligible"] is False
assert derived_unknown["unknown_metadata"] is True
assert _pit_historical_member(universe, symbol, gap_date) is None
```

Breadth must report `membership_count`, `unknown_metadata_count`,
`ineligible_count`, and `metadata_coverage_pct`; a quarantined session must map
to `level="unknown"` and `allow_buy=False`. Validation must reject a qualified
trade on a quarantined signal day or a membership-lineage mismatch. A fixture
holding opened before a gap must remain valued and continue exit attempts.

- [ ] **Step 2: Run tests and observe missing eligibility enforcement**

Run:

```bash
.venv/bin/pytest tests/test_research_stock_only_backtest.py tests/test_research_stock_market_context.py tests/test_research_validation.py -q
```

Expected: new tests fail because consumers currently treat row presence as eligibility.

- [ ] **Step 3: Implement explicit fail-closed gates**

Add `membership_status_as_of()`, and return uniform exact/derived row fields from
`item_as_of()` and `items_as_of()`. Require explicit true session/row eligibility
and explicit false unknown metadata in candidate generation and validation.
Breadth must preserve full projection statistics while using only eligible rows
for price metrics; zero eligible or any session quarantine yields unknown and no
BUY. Do not modify execution fill gates or position removal behavior.

- [ ] **Step 4: Run consumer and execution regressions**

Run:

```bash
.venv/bin/pytest tests/test_research_stock_only_backtest.py tests/test_research_stock_market_context.py tests/test_research_validation.py tests/test_execution.py tests/test_research_pit_next_open_execution.py tests/test_research_pit_next_open_execution_evidence.py -q
.venv/bin/ruff check app/research_backtest.py app/research_context.py app/research_validation.py
```

Expected: all selected tests pass; existing holdings and exits remain unchanged.

### Task 6: Full gate, bounded live proof, and scientific decision

**Files:**
- Append through API: `data/research_experiments/ledger.jsonl`
- Runtime only: `tmp/jiaoch-membership-quarantine-20160930-20161010/`

- [ ] **Step 1: Run local full verification before network use**

Run:

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
git diff --check
```

Expected: full suite passes with only the already known Starlette warning; Ruff and diff check exit 0.

- [ ] **Step 2: Run the single bounded development collection**

Use the existing source configuration from the environment without printing it.
Collect only `2016-09-30..2016-10-10`, workers=1, frozen development temporal
contract, into the dedicated runtime store. Audit, publish to a dedicated
runtime artifact directory, reopen offline, and compare membership and artifact
roots. If `2016-09-30` is not a verified non-empty anchor, stop the live case as
falsified/aborted according to the registered protocol; do not substitute an
unregistered date after observing results.

- [ ] **Step 3: Independently review code and evidence**

The spec reviewer checks every design invariant and the code-quality reviewer
checks transaction boundaries, raw reparse, source/temporal authority, artifact
selection, loader roots, consumer gates, secrets, and test validity. Resolve all
P0/P1/P2 findings and re-run affected tests.

- [ ] **Step 4: Append lifecycle and decision events through the API**

For an executed protocol, append `completed` with
`hypothesis_result=supported|falsified`, exact roots/counts/timing and zero
strategy/OOS metrics; then append `reviewed` and `decision`. Passing permits only
the next preregistered full-development quarantine-topology measurement with the
already frozen `<=2%` session rate and `<=5` consecutive-session limits. It does
not promote a strategy, historical source, or final-OOS result.
