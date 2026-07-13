# Automatic A-Share Stock Operation Advice Implementation Plan

> **For agentic workers:** implement task-by-task with TDD. Keep broker
> execution out of scope and preserve unrelated dirty-worktree changes.

**Goal:** Automatically produce and display at most three A-share stock
operation-advice items per day, with a 50% net annualized-return / 15% maximum-
drawdown target and explicit research-versus-live evidence boundaries.

**Architecture:** Keep the existing recommendation service, API, static web
surface, append-only history, and Asia/Shanghai systemd timers. Add one
versioned strategy-profile contract and one fail-closed evidence gate at the
recommendation boundary. The profile is the single source of truth for target
metrics, required tags, costs/slippage, and minimum evidence; per-stock config
defaults cannot weaken it.

**Tech stack:** Python, FastAPI, pytest, ruff, vanilla static JavaScript,
systemd timer units, JSON snapshots, and the existing research ledger/PIT
modules.

## Current execution status (2026-07-13)

- **Target:** real-cost/slippage net annualized return about 50%, maximum
  drawdown no worse than 15%; observed win rate 52%-60%, payoff ratio and
  Profit Factor at least 1.3, Calmar at least 1.5, and stable rolling
  12-month evidence.
- **Action boundary:** generate and display at most three A-share operation
  suggestions per day (entry zone, sizing guidance, stop, take-profit,
  holding period, and invalidation); `auto_order` is permanently `false` and
  no broker order API is in scope.
- **Verified locally:** profile/gate/evidence, recommendation generation,
  production-health semantics, website contract, JavaScript syntax, and shell
  syntax. The latest replay writes a valid empty snapshot for the next session
  with `recommendation_status=blocked_profile_gate`, `items=0`,
  `live_proof=false`, and `auto_order=false`.
- **Model binding:** the current signal evaluator is `app.signals.evaluate_signal`
  with source hash `959bb25ef4c39a1f1dc0d1298775926756f9caec70da7da6ecefc03325e41a6d`;
  the registered profile hash is
  `ffc6b2ddc65e3e64bea193c8e11a94c73054281168bb071e7711fe1c1bab02c1`. Any
  future model edit must produce a new replay/evidence receipt rather than
  reusing the old one.
- **Still blocked:** one audited 2023 development PIT artifact now exists, but
  it does not cover the complete strategy-validation range and cannot produce
  the qualifying profile receipt. Strategy-signal and artifact-outcome replay
  therefore remain unbound to a complete qualifying artifact; provider and
  industry-cache health are also degraded. The website must continue to show
  “今日不推荐” rather than inventing three picks.
- **PIT result:** all 242 Jiaoch market generations for 2023-01-03 through
  2023-12-29 now pass `pit-coverage-audit/v4`. Ten missing `300114.SZ` daily
  rows are explained only by an independently hashed CNINFO start/resume PDF
  interval `[2023-01-12, 2023-02-02)`; provider rows remain unchanged. The
  published development bundle is
  `tmp/jiaoch-published-2023/4cb70198653ae43488118f58f7fd0d8bc59aa888c8b8316581003c13aec68b7c`
  with coverage audit
  `3b9c790c41b4092b219e9e42f1b0744e3b68a07302b171a3eb151c672a900347`.
- **Source policy:** controlled PIT collection now defaults to Jiaoch. Live
  announcement context attempts the pinned Jiaoch `anns_d` endpoint first,
  then records a stable fallback reason and uses official CNINFO disclosure
  lookup. The current credential is explicitly denied `anns_d` access
  (`40203`), so the process circuit-breaks repeated attempts and uses CNINFO.
  The website surfaces actual market/announcement sources and fallback state.
  Live daily bars remain AKShare/SQLite until a separately tested Jiaoch
  `daily + adj_factor` adapter proves raw execution prices, causal qfq signals,
  units, freshness, cache isolation, and source lineage.
- **Fresh verification:** the source/recommendation/web focused suite passes
  152 tests; the full suite passes 1262 tests with one existing
  Starlette/httpx deprecation warning. Full Ruff, JavaScript syntax, and diff
  whitespace checks pass. A fresh 2026-07-14 pre-open replay remains safely
  blocked with zero items and no order authority.
  It remains `final_oos_eligible=false` and proves data integrity, not 50/15
  strategy performance or live readiness.

---

## Task 1: Add the canonical recommendation strategy profile

**Files:**

- Add: `app/recommendation_profile.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Test: `tests/test_recommendation_profile.py`

- [ ] **Step 1: Write failing tests**

  Test that the default profile is named `primary_50_return_15_drawdown` and
  contains return 50.0, drawdown 15.0, payoff 1.3, Profit Factor 1.3, Calmar
  1.5, signal days 120, and a maximum of three displayed items. Test that a
  profile cannot be loaded with weaker thresholds or a signal-tag set that
  differs from the registered profile.

- [ ] **Step 2: Run the focused tests and confirm red**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest tests/test_recommendation_profile.py -q
  ```

- [ ] **Step 3: Implement the immutable profile contract**

  Provide typed loading/validation and a stable profile version/hash. Bind
  costs, slippage, required tags, market scope (`a` only), evidence level, and
  the three-item cap. Keep secrets out of profile errors and logs.

- [ ] **Step 4: Re-run focused tests and lint**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest tests/test_recommendation_profile.py -q
  ./.venv/bin/ruff check app/recommendation_profile.py app/config.py tests/test_recommendation_profile.py
  ```

## Task 2: Implement the fail-closed evidence and health gate

**Files:**

- Add: `app/recommendation_gate.py`
- Modify: `app/production_status.py`
- Modify: `app/recommendations.py`
- Test: `tests/test_recommendation_gate.py`
- Test: `tests/test_production_status.py`

- [ ] **Step 1: Write failing gate tests**

  Cover stale SQLite latest-bar date, provider failure, invalid industry cache
  timestamp, missing PIT/temporal receipt, profile drift, failed metrics,
  insufficient signal days, and a valid development-only candidate. Assert
  that a failed gate returns no `buy` advice and records each reason.

- [ ] **Step 2: Run focused tests and confirm red**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest tests/test_recommendation_gate.py tests/test_production_status.py -q
  ```

- [ ] **Step 3: Implement the gate**

  Add a pure, deterministic gate that consumes profile, metric payload,
  temporal/PIT receipts, and production status. Return structured reasons,
  `evidence_scope`, `live_proof`, and `auto_order: false`. Make missing or
  malformed evidence fail closed.

- [ ] **Step 4: Verify gate behavior and lint**

  Run the focused tests and ruff. Confirm existing market-cache freshness
  checks remain intact and no provider token is printed.

## Task 3: Bind recommendation generation to the profile and advice contract

**Files:**

- Modify: `app/recommendations.py`
- Modify: `app/jobs.py`
- Modify: `app/main.py`
- Test: `tests/test_recommendations.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing generation/API tests**

  Assert that generation uses the profile rather than loose per-stock defaults,
  only scans A-share stocks, caps output at three, stores target date/signal
  date/data-as-of/run slot/profile id, includes operation advice fields, and
  always emits `auto_order: false`. Test that a blocked gate produces a valid
  empty snapshot with reasons.

- [ ] **Step 2: Run tests and confirm red**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest tests/test_recommendations.py tests/test_api.py -q
  ```

- [ ] **Step 3: Implement the smallest integration**

  Inject the profile and gate at the final selection boundary. Preserve
  existing lock/idempotency/history behavior. Map existing fields to advice
  fields without inventing a price or guarantee when source data is missing.
  Keep `target_trade_date=next` and post-close semantics unchanged.

- [ ] **Step 4: Verify focused API and recommendation tests**

  Run pytest and ruff on changed Python files. Replay `pre_open`,
  `open_confirm`, `pre_close`, and `post_close` locally with the current cache.

## Task 4: Make the website surface evidence and operational advice clearly

**Files:**

- Modify: `app/static/index.html`
- Modify: `app/static/app.js`
- Test: `tests/test_web_contract.py`

- [ ] **Step 1: Write failing web-contract tests**

  Require visible target date, data-as-of, run slot, profile, evidence scope,
  health/gate status, operation fields, empty-result explanation, and a clear
  no-auto-order label. Cover stale/latest and Basic Auth error states.

- [ ] **Step 2: Run tests and confirm red**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest tests/test_web_contract.py -q
  ```

- [ ] **Step 3: Implement the UI contract**

  Keep the existing simple static surface. Render research-development labels
  prominently and distinguish `watch`/empty from `buy`; never render a BUY when
  the gate is failed. Add no order button or execution affordance.

- [ ] **Step 4: Verify syntax and contract tests**

  ```bash
  node --check app/static/app.js
  PYTHONPATH=. ./.venv/bin/pytest tests/test_web_contract.py -q
  ```

## Task 5: Align schedules and production-health evidence

**Files:**

- Modify: `deploy/quant-signal-*.timer`
- Modify: `deploy/check-production-health.sh`
- Modify: `README.md`
- Test: `tests/test_production_health_script.py`

- [ ] **Step 1: Add failing schedule/health tests**

  Assert Asia/Shanghai timezone declarations, coverage of health and oneshot
  services, target-date/data-as-of checks, and explicit degraded status when
  provider or industry-cache evidence is invalid.

- [ ] **Step 2: Implement and verify local scripts**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest tests/test_production_health_script.py -q
  bash -n deploy/check-production-health.sh
  ```

  Do not deploy or restart a remote service in this task.

## Task 6: Close the strategy evidence chain

**Files:**

- Modify: `app/research_sweep.py`
- Modify: `app/research_validation.py`
- Modify: `app/research_backtest.py`
- Modify: `app/jobs.py`
- Test: `tests/test_research_sweep.py`
- Test: `tests/test_research_validation.py`
- Test: `tests/test_research_backtest.py`

- [ ] **Step 1: Write failing metric-gate tests**

  Require payoff ratio, Profit Factor, Calmar, Wilson lower bound, minimum
  signal days, every required rolling 12-month window, double-cost/slippage
  sensitivity, and temporal/PIT contract identity. Ensure current auto-031 is
  reported provisional because it has 107 signal days and contaminated
  diagnostic dates.

- [ ] **Step 2: Run focused research tests and confirm red**

  ```bash
  PYTHONPATH=. ./.venv/bin/pytest tests/test_research_sweep.py tests/test_research_validation.py tests/test_research_backtest.py -q
  ```

- [ ] **Step 3: Implement complete qualification payloads**

  Preserve historical experiment records, but make the active profile require
  the complete metric/evidence set. Append machine-readable registered,
  completed, reviewed, and decision events for any new auto-iteration. Do not
  use the current development cache as final-OOS proof. The profile receipt
  builder must bind strategy/validation hashes, the qualified-trades artifact,
  PIT/temporal authority hashes, a ledger anchor, and all rolling windows. The
  latest-window Calmar must use the same 365-day drawdown window; the old
  mixed-window field remains legacy-only.

- [ ] **Step 4: Verify research gates and ledger shape**

  Run the focused tests, ruff, and a read-only CLI validation probe that fails
  closed on the known incompatible cache/partition rather than relabeling it.

  The current `auto-031` cache must produce an `incomplete` receipt: its 107
  signal days, missing PIT contract, and unstable all-window evidence cannot be
  promoted merely because its latest rolling window looks good.

## Task 7: Full local acceptance and handoff

- [ ] Run the relevant combined pytest suites, ruff, `node --check`, shell
  syntax checks, `git diff --check`, and local recommendation CLI replays.
- [ ] Inspect the generated snapshot and website payload for target date,
  data-as-of, advice fields, profile id, evidence scope, gate reasons, and
  `auto_order: false`.
- [ ] Confirm no secret/token appears in logs, snapshots, or test output.
- [ ] Report separately: development candidate evidence, missing live-proof
  evidence, production health state, and the single next priority.
- [ ] Do not claim 50%/15% live readiness until the audited PIT/final-OOS/
  shadow evidence exists.
