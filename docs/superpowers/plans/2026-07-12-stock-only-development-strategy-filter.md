# Stock Only Development Strategy Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or an equivalent local execution loop to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run one pre-registered stock-only development strategy iteration using existing qualified trades, without live data, final-OOS access, or broker automation.

**Architecture:** Treat the existing qualified-trade cache as a development-only strategy substrate. Use the repo's current sweep/validation functions to compare a baseline against one fixed filter shape; record the result in the append-only experiment ledger. If the cache lacks audited artifact anchors, classify the output as strategy-structure evidence only, not as live-ready proof.

**Tech Stack:** Python 3.12, existing `app.research_sweep`, existing `app.research_validation`, JSON qualified-trade cache, append-only experiment ledger, pytest/Ruff.

---

## Files

- Read: `data/research_cache/vps_research/sens_baseline_slip10_cost25.qt.json`
- Append through API: `data/research_experiments/ledger.jsonl`
- Optionally create runtime report under: `tmp/auto-iter-024-stock-only-development-strategy-filter/`
- Do not modify provider, artifact, execution, or broker code.

## Task 1: Register Fixed Strategy Iteration

- [x] **Step 1: Append registered event**

Registered `auto-iter-024-stock-only-development-strategy-filter` at ledger sequence 79.

## Task 2: Reproduce Baseline Metrics

- [ ] **Step 1: Load qualified trades**

Run a read-only local script that loads `qualified_trades` and reports:

```text
trade_count
signal_date range
available signal tags
available market levels
summary artifact/root fields if any
```

- [ ] **Step 2: Run baseline fixed sweep**

Use:

```python
sweep_qualified_trades(
    qualified_trades,
    hold_days=5,
    top_n=3,
    symbol_cooldown_days=0,
    max_active_positions=3,
    min_trades=200,
    max_filter_size=0,
    target_win_rate_pct=70.0,
    target_drawdown_pct=5.0,
    target_one_year_return_pct=200.0,
    exposure_multipliers=[1.0],
    annual_financing_rate_pct=8.0,
    roundtrip_cost_bps=25.0,
    slippage_bps=10.0,
    capital_model="slot-daily",
    fixed_spec=True,
)
```

Expected: one baseline row or a failure record with exact reason.

## Task 3: Run One Fixed Filter

- [ ] **Step 1: Choose the filter before seeing results**

Filter contract after read-only tag/market availability profiling, before any
filtered performance run:

```text
top_n=3
max_active_positions=3
capital_model=slot-daily
required_signal_tags=[
  "amount_gte_1b",
  "candidate_rank_lte_40",
  "prior_adverse_lte_4",
  "balanced_rsi"
]
excluded_signal_tags=[]
market_levels=["favorable", "neutral"]
cost/slippage unchanged
exposure=1.0
```

These fields were selected for the pre-registered mechanism: liquidity,
candidate quality, prior adverse excursion, non-extreme RSI, and avoiding
defensive/cautious/unknown market regimes. Do not add or remove filters after
seeing the filtered result.

- [ ] **Step 2: Run fixed sweep**

Run `sweep_qualified_trades(... fixed_spec=True ...)` with the chosen filter.

- [ ] **Step 3: Compare to baseline**

Report:

```text
selected_trade_count
trade_win_rate_pct
Wilson lower bound if available
portfolio_max_drawdown_pct
rolling_1y_latest_return_pct
target gates
sample adequacy
```

## Task 4: Record Decision

- [ ] **Step 1: Append completed or failed event**

Use `append_experiment_event` only. Do not hand-edit JSONL.

- [ ] **Step 2: Append decision event if completed**

Decision values:

```text
promote: false unless sample, win confidence, drawdown, and return gates all improve
next_action: data/artifact or strategy follow-up depending on failure
```

## Task 5: Verify

- [ ] **Step 1: Run targeted tests**

```bash
./.venv/bin/pytest tests/test_research_sweep.py tests/test_research_validation.py -q
```

- [ ] **Step 2: Run static check**

```bash
./.venv/bin/ruff check app/research_sweep.py app/research_validation.py
```

No production code change is expected in this iteration.
