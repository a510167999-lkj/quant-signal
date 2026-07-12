# Bounded Partition Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add measured, bounded concurrency to independent PIT collection partitions while preserving byte-for-byte request identities and immutable correctness evidence.

**Architecture:** A deterministic phase runner uses a bounded thread pool for independent partitions. Collector shared caches are lock-protected; SQLite retains transaction ownership of receipt and generation correctness.

**Tech Stack:** Python 3.12, `concurrent.futures.ThreadPoolExecutor`, `threading`, SQLite WAL, pytest, ruff.

---

### Task 1: Deterministic concurrent phase runner

**Files:**
- Modify: `app/research_pit_collector.py`
- Test: `tests/test_research_pit_collector.py`
- Test: `tests/test_research_pit_collector_mid_failure_recovery.py`

- [ ] Write RED tests for worker validation, deterministic ordering, actual overlap, first-failure cancellation, terminal evidence, and worker-count-independent resume identity.
- [ ] Implement a private phase runner and lock clock/resume shared state.
- [ ] Keep `workers=1` behavior serial and unchanged; run collector/recovery/concurrency tests.

### Task 2: Phase integration and CLI

**Files:**
- Modify: `app/research_pit_collector.py`
- Modify: `app/jobs.py`
- Test: `tests/test_research_pit_collector.py`
- Test: `tests/test_research_market_session_collector.py`

- [ ] Add CLI `--workers` with range `1..8`, default `1`.
- [ ] Parallelize calendar+stock, daily membership sessions, and market-session generations only at the approved boundaries.
- [ ] Prove request semantics and generation pins are identical across worker counts and reports remain deterministically ordered.

### Task 3: Local correctness and benchmark harness

**Files:**
- Create: `tests/test_research_pit_collector_throughput.py`
- Modify only if tests expose a correctness defect.

- [ ] Add a latency-controlled transport benchmark proving overlap and measuring speedup without network variance.
- [ ] Assert exact normalized rows, attempts/events, receipts, generation roots, resume behavior, and bounded workers.
- [ ] Run focused suites, full pytest, ruff, and diff check.

### Task 4: Live bounded benchmark and decision

**Files:**
- Append through API: `data/research_experiments/ledger.jsonl`
- Runtime only: separate `tmp/` stores per worker variant.

- [ ] Run workers 1,2,4,8 on 2023-12-28..2023-12-29 within the 60-minute budget.
- [ ] Audit every completed store and compare normalized/reconciliation evidence.
- [ ] Repeat the leading safe variant once; reject throttled, inconsistent, or sub-2.5x variants.
- [ ] Complete independent review and append iteration 020 lifecycle events. No strategy or OOS is run.
