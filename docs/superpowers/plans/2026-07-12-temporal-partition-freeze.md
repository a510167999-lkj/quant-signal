# Temporal Partition Freeze Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enforce one hash-bound temporal-role contract that keeps contaminated dates out of promotion and seals every final-OOS read before strategy freeze.

**Architecture:** A small pure validation module owns canonical contract parsing and range classification. Collection request semantics and audited artifact manifests bind the verified contract hash and role; CLI and research entry points reject forbidden ranges before token, network, artifact, or provider access.

**Tech Stack:** Python 3.12, JSON, SHA-256, SQLite artifact manifests, argparse, pytest, ruff.

---

### Task 1: Canonical temporal contract

**Files:**
- Create: `app/research_partitions.py`
- Create: `data/research_partitions/frozen-v1.json`
- Create: `tests/test_research_partitions.py`

- [ ] Write RED tests for exact boundaries, canonical self-hash, required fields, unknown fields, overlap, gaps, open-ended role placement, altered contamination hashes, and sealed final OOS.
- [ ] Run `.venv/bin/pytest -q tests/test_research_partitions.py` and confirm failures are caused by the missing module/contract.
- [ ] Implement strict parsing, `classify_date`, `assert_range_allowed`, `assert_final_oos_sealed`, and deterministic contract hashing. No I/O other than reading the named contract file.
- [ ] Run the target tests and ruff; expected all pass.

### Task 2: Pre-I/O collection enforcement and request identity

**Files:**
- Modify: `app/research_pit_collector.py`
- Modify: `app/jobs.py`
- Test: `tests/test_research_pit_collector.py`
- Test: `tests/test_research_pit_attempts.py`
- Test: `tests/test_research_partitions.py`

- [ ] Write RED tests proving development collection accepts only 2016-01-01..2023-12-31, diagnostic collection cannot claim promotion eligibility, final-OOS/embargo/cross-role requests fail before clock/token/network/store mutation, and contract/role enter request semantics.
- [ ] Run selected tests and confirm RED.
- [ ] Load the contract before resolving credentials or constructing transport; pass `temporal_role` and `temporal_contract_sha256` into the collector and every canonical request-semantics hash.
- [ ] Prove resume isolation across contract hash/role and same-contract idempotence; run selected recovery/concurrency tests and ruff.

### Task 3: Artifact role and contract binding

**Files:**
- Modify: `app/research_pit_store.py`
- Modify: `app/jobs.py`
- Test: `tests/test_research_pit_store.py`
- Test: `tests/test_research_artifact_replay.py`

- [ ] Write RED tests that publication requires a verified contract/role, coverage must lie wholly inside that role, diagnostic artifacts remain non-promotable, final-OOS publication is sealed, and manifest/SQLite/role/hash tampering fails offline.
- [ ] Run target tests and confirm RED.
- [ ] Bind temporal role, contract hash, exact range, and promotion eligibility into artifact semantic manifest and loader verification without invalidating legacy development-only artifacts.
- [ ] Add loader properties for role/hash and enforce no cross-role bundle combination; run target and artifact tamper suites.

### Task 4: Validation and backtest boundary gates

**Files:**
- Modify: `app/research_validation.py`
- Modify: `app/research_backtest.py`
- Modify: `app/jobs.py`
- Test: `tests/test_research_validation.py`
- Test: `tests/test_research_stock_only_backtest.py`

- [ ] Write RED tests proving contaminated trades are reported only as diagnostics, never promotion metrics; embargo and final-OOS dates reject before artifact/provider resolution; development walk-forward stays within 2016-2023.
- [ ] Run target tests and confirm RED.
- [ ] Add role-aware gates while preserving synthetic fixture support through explicit synthetic contracts. Do not add any final-OOS unlock path in this iteration.
- [ ] Run validation/backtest/CLI tests and ruff.

### Task 5: Verification and iteration decision

**Files:**
- Append through API: `data/research_experiments/ledger.jsonl`

- [ ] Run focused partition, collector, store, artifact, validation, and backtest suites.
- [ ] Run `ruff check app tests`, `git diff --check`, and full `.venv/bin/pytest -q`.
- [ ] Run CLI adversarial probes showing embargo/final-OOS rejection occurs before credential lookup or network calls.
- [ ] Obtain independent specification and quality reviews; resolve every P0/P1/P2.
- [ ] Append completed/reviewed/decision events for iteration 019. The next iteration may collect only the 2016-2023 development partition; no strategy or final OOS is run here.
