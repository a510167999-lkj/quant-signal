# 50pct Return 15pct Drawdown Stock Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-anchor A-share stock-only research around a 50% net rolling-one-year return target with maximum drawdown no worse than 15%.

**Architecture:** Keep the recommendation surface capped at at most 3 stocks per day and keep broker execution out of scope. Treat current research caches as development hypothesis-screening inputs only; any promotion still requires PIT-safe data, final OOS sealing, cost/slippage sensitivity, regime checks, and shadow/live evidence.

**Tech Stack:** Python, pytest, ruff, `app.research_sweep`, `app.research_validation`, hash-chained `data/research_experiments/ledger.jsonl`.

---

### Task 1: Lock The Active Target Profile

**Files:**
- Modify: `app/research_sweep.py`
- Modify: `app/research_validation.py`
- Modify: `app/jobs.py`
- Test: `tests/test_research_sweep.py`
- Test: `tests/test_research_validation.py`

- [ ] **Step 1: Write the failing target-default tests**

Add a sweep test that calls `sweep_qualified_trades(...)` without `target_one_year_return_pct` and asserts:

```python
assert result["target_one_year_return_pct"] == 50.0
```

Update the frozen-validation target test so the strategy omits `target_one_year_return_pct` and asserts:

```python
assert qualification["target_profile"] == "primary_50_return_15_drawdown"
```

- [ ] **Step 2: Verify the tests fail before implementation**

Run:

```bash
PYTHONPATH=. ./.venv/bin/pytest \
  tests/test_research_sweep.py::test_sweep_qualified_trades_defaults_to_current_50pct_return_target \
  tests/test_research_validation.py::test_development_gates_use_realistic_return_drawdown_and_quality_targets \
  -q
```

Expected before implementation: both tests fail because the code still reports `30.0` and `primary_30_return_15_drawdown`.

- [ ] **Step 3: Set implementation defaults to 50/15**

Change only the default return target and profile naming:

```python
target_one_year_return_pct: float = 50.0
target_return = float(strategy.get("target_one_year_return_pct", 50.0))
"target_profile": "primary_50_return_15_drawdown"
```

Set every CLI `--target-one-year-return-pct` default in `app/jobs.py` to `50.0`.

- [ ] **Step 4: Verify the target-default tests pass**

Run the same pytest command from Step 2.

Expected after implementation: `2 passed`.

### Task 2: Keep Current Docs Aligned Without Rewriting History

**Files:**
- Modify: `CLAUDE.md`
- Modify: `PLAN.md`
- Modify: `README.md`
- Optional review: `docs/superpowers/plans/*.md`

- [ ] **Step 1: Update only current-target language**

Current target language must say:

```text
成本/滑点后最近滚动 12 个月净收益约 50%，最大回撤不超过 15%
```

Keep old `70%/5%/200%` and old `30%` numbers only when they are clearly historical experiment records.

- [ ] **Step 2: Audit for stale active-target references**

Run:

```bash
rg -n "primary_30|年化.*30|收益 30|30% net|return30|40%-50%.*advanced" \
  CLAUDE.md PLAN.md README.md app tests docs/superpowers/plans
```

Expected: no active target/default references remain; historical records may remain if their surrounding text labels them as old results.

### Task 3: Run Auto-Iter-029 As One Pre-Registered Hypothesis

**Files:**
- Read: `data/research_cache/vps_research/sens_baseline_slip10_cost25.qt.json`
- Append: `data/research_experiments/ledger.jsonl`

- [ ] **Step 1: Preflight sample size without reading returns**

Use the cache only after stripping `return_pct`, `max_adverse_pct`, and `mark_to_market_path` from the preflight view. Test exactly one hypothesis:

```text
Exclude the highest signal-day cross-sectional turnover percentile to reduce crowding.
```

Minimum preflight acceptance before performance review:

```text
selected_trade_count >= 200
signal_days >= 120
```

- [ ] **Step 2: Register the hypothesis before performance**

Append a `registered` ledger event with:

```json
{
  "experiment_id": "auto-iter-029-cross-sectional-turnover-crowding",
  "event_type": "registered",
  "target_profile": "primary_50_return_15_drawdown",
  "target_one_year_return_pct": 50.0,
  "target_drawdown_pct": 15.0,
  "hypothesis": "Excluding only the highest signal-day turnover percentile reduces crowding and improves drawdown without giving back the 50% return target.",
  "falsification_criterion": "Reject if preflight sample is below 200 selected trades, if drawdown remains worse than 15%, if latest rolling-one-year return is below 50%, or if Profit Factor < 1.3 / Calmar < 1.5.",
  "live_readiness": false
}
```

- [ ] **Step 3: Run one baseline and one filtered performance check**

Keep costs and portfolio controls fixed:

```text
top_n=3
max_active_positions=5
capital_model=slot-daily
annual_financing_rate_pct=8
roundtrip_cost_bps=25
slippage_bps=10
target_one_year_return_pct=50
target_drawdown_pct=15
target_profit_factor=1.3
target_calmar=1.5
```

`top_n=3` preserves the daily recommendation cap; `max_active_positions=5` only allows overlapping 5-day holds and is required by the return-blind preflight sample-size gate.

Compare only:

```text
baseline
exclude signal-day highest-turnover percentile
```

- [ ] **Step 4: Complete, review, and decide in the ledger**

Append `completed`, `reviewed`, and `decision` events containing:

```text
selected_trade_count
signal_days
trade_win_rate_pct
win_rate_wilson_95_lower_pct
trade_payoff_ratio
trade_profit_factor
portfolio_compounded_return_pct
rolling_1y_latest_return_pct
portfolio_max_drawdown_pct
portfolio_calmar_latest_1y
target_all_pass
```

Decision rule:

```text
Promote only if the filtered variant has >=50% latest rolling-one-year return, drawdown no worse than -15%, Profit Factor >=1.3, Calmar >=1.5, sample >=200, and no clear deterioration versus baseline stability.
```

### Task 4: Verify Before Any Completion Claim

**Files:**
- Verify: `app/research_sweep.py`
- Verify: `app/research_validation.py`
- Verify: `app/jobs.py`
- Verify: `tests/test_research_sweep.py`
- Verify: `tests/test_research_validation.py`

- [ ] **Step 1: Run focused tests and lint**

Run:

```bash
PYTHONPATH=. ./.venv/bin/pytest tests/test_research_sweep.py tests/test_research_validation.py -q
./.venv/bin/ruff check app/research_sweep.py app/research_validation.py app/jobs.py tests/test_research_sweep.py tests/test_research_validation.py
```

Expected: all tests pass and ruff reports no issues.

- [ ] **Step 2: Verify CLI help still loads**

Run:

```bash
PYTHONPATH=. ./.venv/bin/python -m app.jobs research-sweep-file --help >/dev/null
PYTHONPATH=. ./.venv/bin/python -m app.jobs research-validate-file --help >/dev/null
```

Expected: both commands exit 0.

- [ ] **Step 3: Replay the ledger hash chain after appending auto-iter-029**

Run the existing local ledger replay check used by the project and confirm the latest sequence is contiguous and hash-valid.

Expected: the replay reports a valid chain; if not, stop and repair the ledger before interpreting experiment results.

### Status Addendum: Auto-Iter-031

`auto-iter-031` found the first current-profile fixed-spec development candidate in this continuation:

```text
required_signal_tags = breadth_advancing_gte_50,breakout_20d
market_levels = favorable,neutral
hold_days = 5
top_n = 3
max_active_positions = 10
capital_model = slot-daily
exposure = 1.0
costs = 8% annual financing, 25 bps roundtrip, 10 bps one-way slippage
```

Baseline fixed-spec sweep:

```text
selected_trade_count = 247
signal_days = 107
trade_win_rate_pct = 56.68
trade_profit_factor = 2.07
portfolio_calmar_latest_1y = 5.58
rolling_1y_latest_return_pct = 53.58
portfolio_max_drawdown_pct = -9.61
target_all_pass = true
```

这组结果的收益/回撤/PF/Calmar 指标通过，但 `signal_days=107` 低于本计划的
`signal_days >= 120` 样本门槛；因此 `target_all_pass=true` 只代表当前 sweep
指标子门槛，不能代表完整开发验收通过，候选状态降为 provisional。

The tested treatment, `pre_exit_calendar_gap_days=7`, is rejected because it reduced latest rolling-one-year return to `47.93` and did not improve drawdown. The baseline candidate remains development-only evidence: frozen validation is not yet available for this 2024-2026 cache because the current validation contract still enforces the older 2016-2023 frozen development range.
