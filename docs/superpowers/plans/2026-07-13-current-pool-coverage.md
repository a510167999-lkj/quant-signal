# 2026 Current Pool Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an audited 2026 current-universe filter and available-history coverage report for Shanghai/Shenzhen main-board and ChiNext stocks, then run the frozen auto-iter-031 strategy as auto-iter-034 without individual-stock factor changes.

**Architecture:** Add a focused universe-policy module and a deterministic coverage-report module, then wire them into the existing research CLI. Jiaoch supplies structured bulk data; the existing sweep engine remains responsible for strategy metrics and the hash-chained ledger remains the experiment authority.

**Tech Stack:** Python 3, pandas, pytest, existing Jiaoch/Tushare-compatible collector, JSON experiment ledger.

---

### Task 1: Current-pool eligibility policy

**Files:**
- Create: `app/current_pool.py`
- Create: `tests/test_current_pool.py`

- [ ] **Step 1: Write failing eligibility tests**

```python
from app.current_pool import classify_current_pool_item

def test_accepts_main_board_and_chinext_only():
    assert classify_current_pool_item({"symbol": "600000", "name": "浦发银行", "list_status": "L"})["eligible"]
    assert classify_current_pool_item({"symbol": "300001", "name": "特锐德", "list_status": "L"})["eligible"]
    assert not classify_current_pool_item({"symbol": "688001", "name": "华兴源创", "list_status": "L"})["eligible"]
    assert not classify_current_pool_item({"symbol": "920001", "name": "纬达光电", "list_status": "L"})["eligible"]
```

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest tests/test_current_pool.py -q`
Expected: FAIL because `app.current_pool` does not exist.

- [ ] **Step 3: Implement the minimal pure classifier**

Implement deterministic code-prefix classification plus name/status exclusions. Return `eligible`, `board`, and stable `reason_codes`; do not add liquidity or fundamental filters.

- [ ] **Step 4: Run GREEN**

Run: `.venv/bin/pytest tests/test_current_pool.py -q`
Expected: PASS.

### Task 2: Available-history coverage audit

**Files:**
- Modify: `app/current_pool.py`
- Modify: `tests/test_current_pool.py`

- [ ] **Step 1: Write failing coverage tests**

Test cohorts `0-59`, `60-119`, `120-239`, and `240+` bars; verify a newly listed stock remains in the eligible mother pool but has `signal_ready=false` below the configured minimum.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest tests/test_current_pool.py -q`
Expected: FAIL because coverage reporting is missing.

- [ ] **Step 3: Implement deterministic report generation**

Add `build_current_pool_coverage(items, histories, min_signal_bars)` returning counts, percentages, cohorts, failures, source dates, policy identity, canonical SHA-256, and `evidence_scope=development_only`.

- [ ] **Step 4: Run GREEN**

Run: `.venv/bin/pytest tests/test_current_pool.py -q`
Expected: PASS.

### Task 3: Research CLI and Jiaoch bulk inputs

**Files:**
- Modify: `app/jobs.py`
- Modify: `tests/test_research_backtest.py`
- Modify: `README.md`

- [ ] **Step 1: Write a failing CLI contract test**

Verify the command requires structured stock master and history summaries, writes a content-addressed coverage JSON, and never reads credential values into output.

- [ ] **Step 2: Run RED**

Run: `.venv/bin/pytest tests/test_research_backtest.py -q`
Expected: FAIL because the command is absent.

- [ ] **Step 3: Add `research-current-pool-audit`**

Wire bulk Jiaoch-derived inputs into the pure audit component. Reject missing status fields or inconsistent dates; emit only stable provider identifiers and hashes.

- [ ] **Step 4: Run GREEN and lint**

Run: `.venv/bin/pytest tests/test_current_pool.py tests/test_research_backtest.py -q`
Run: `.venv/bin/ruff check app/current_pool.py app/jobs.py tests/test_current_pool.py tests/test_research_backtest.py`
Expected: PASS.

### Task 4: Auto-iter-034 expanded-pool replay

**Files:**
- Modify through application command: `data/research_experiments/ledger.jsonl`
- Create through application command: `data/research_experiments/auto-iter-034-current-pool-coverage-expansion.json`

- [ ] **Step 1: Complete coverage preflight before reading performance**

Record mother-pool size, eligible count, fetched count, failures, signal-ready count, history cohorts and coverage hash. Abort without performance claims if coverage is not auditable.

- [ ] **Step 2: Run the frozen specification**

Use required tags `breadth_advancing_gte_50` and `breakout_20d`, favorable/neutral markets, top 3, five-day holding, ten active slots, 1x exposure, 25 bps roundtrip cost, 10 bps slippage and 8% annual financing.

- [ ] **Step 3: Append completed/reviewed/decision events**

Record selected trades, signal days, win rate and Wilson interval, payoff ratio, Profit Factor, net return, maximum drawdown, Calmar and every rolling-12-month pass rate. Reject promotion if any preregistered criterion fails.

### Task 5: End-to-end verification

**Files:**
- Modify: `PLAN.md`

- [ ] **Step 1: Run focused verification**

Run: `.venv/bin/pytest tests/test_current_pool.py tests/test_research_sweep.py tests/test_research_validation.py tests/test_recommendations.py -q`

- [ ] **Step 2: Run static checks**

Run: `.venv/bin/ruff check app/current_pool.py app/jobs.py app/research_sweep.py app/research_validation.py app/recommendations.py`
Run: `git diff --check`

- [ ] **Step 3: Verify website safety contract**

Confirm the latest snapshot has no more than three items, preserves `auto_order=false`, and reports `blocked_profile_gate`/“今日不推荐” when expanded-pool evidence has not passed.

