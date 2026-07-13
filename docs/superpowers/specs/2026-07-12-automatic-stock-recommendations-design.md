# Automatic A-Share Stock Operation Advice Design

## Objective

Automatically generate and display no more than three A-share stock operation
advice items per trading day. The system never places orders or calls a broker.
The strategy target is approximately 50% net annualized return after realistic
costs and slippage with maximum drawdown no worse than 15%. A candidate is not
promoted on win rate alone: the evidence must also cover a 52%-60% observed
win-rate range, payoff ratio at least 1.3, Profit Factor at least 1.3, Calmar
at least 1.5, and stable rolling 12-month behavior.

## Product boundary

The website is an advice surface, not an execution surface. Every displayed
item is an auditable snapshot with:

- target trade date and signal date;
- symbol, name, market, and operation (`buy`, `watch`, or `avoid`);
- entry range, suggested position size, stop-loss, take-profit, and expected
  holding period;
- invalidation condition and concise rationale;
- data-as-of timestamp, run slot, strategy profile, and evidence scope;
- explicit `auto_order: false`.

The daily result may be an empty list. An empty list is the correct fail-closed
answer when market conditions, data freshness, PIT evidence, or the strategy
profile gate is not satisfied.

## Recommended architecture

Use the existing systemd timers and recommendation service rather than adding a
second scheduler. The flow is:

1. The cache-warm job refreshes market data and records provider/freshness
   evidence.
2. The recommendation job resolves the A-share target session and run slot
   (`pre_open`, `open_confirm`, `pre_close`, or `post_close`) in
   `Asia/Shanghai`.
3. A fixed strategy-profile manifest is loaded. The manifest binds the target
   metrics, signal tags, cost/slippage assumptions, PIT contract identity, and
   profile version. Per-stock defaults cannot silently override it.
4. Preflight gates check calendar, market level, data-as-of, SQLite latest bar,
   provider health, PIT/temporal evidence, sample-size evidence, and profile
   compatibility.
5. Only passing candidates are converted to operation advice and capped at
   three items. The service writes the latest snapshot, append-only history,
   and audit events.
6. The API and website render the snapshot and its evidence boundary. They must
   show `research_development_candidate`/`development_only` when live proof is
   absent and must never imply guaranteed returns.

## Evidence levels and gates

The system has two explicit levels:

### Research-development candidate

Allowed for local hypothesis screening and website display as a labelled
candidate. It requires a complete metric payload but does not prove live
readiness. Current `auto-031` results remain provisional because they have 107
signal days, below the planned 120-day minimum, and use a cache whose temporal
contract is not a clean final-OOS proof.

### Live-use proof

Not granted by a backtest result. It requires an audited PIT artifact,
time-partition contract, frozen final-OOS/shadow evidence, cost and slippage
sensitivity, regime and rolling-window checks, and production health evidence.
Until all receipts exist, `live_proof` remains false and the website labels the
result development-only.

The promotion metrics are evaluated on net returns after configured costs and
slippage:

| Metric | Primary requirement |
|---|---:|
| Annualized net return | about 50% |
| Maximum drawdown | <= 15% |
| Observed win rate | 52%-60% observation band |
| Payoff ratio | >= 1.3 |
| Profit Factor | >= 1.3 |
| Calmar | >= 1.5 (>= 2 preferred) |
| Signal days | >= 120 for a non-provisional candidate |
| Rolling 12-month windows | every required window passes stability checks |

Wilson lower bounds, not only point estimates, are recorded for win rate. Any
missing metric, invalid temporal partition, or failed evidence receipt fails
closed. A failed gate produces no `buy` advice; it may still produce a labelled
`watch`/empty snapshot for diagnostics.

## Scheduling and target-date semantics

The timers must declare `Timezone=Asia/Shanghai`. A post-close run defaults to
the next A-share trading session; intraday runs target the current session.
Each snapshot stores `target_trade_date`, `signal_date`, `data_as_of`, and
`run_slot`. The API accepts an explicit `target_trade_date` or `next` and
rejects past or non-trading dates.

The intended schedule is 08:35 cache warm, 09:00 pre-open, 09:32 open-confirm,
14:55 pre-close, and 15:02 post-close. Schedule timing is operational evidence;
it is not a substitute for a fresh market bar or provider health.

## Failure handling and safety

- Provider failures, stale or internally inconsistent caches, invalid industry
  cache timestamps, missing PIT receipts, and profile drift are visible in
  production status and block `buy` advice.
- The latest snapshot remains readable when a new run fails, but its age and
  target date are shown so stale advice cannot appear current.
- Basic-auth failures are surfaced as an actionable website state rather than a
  blank recommendation panel.
- No code path may submit an order, mutate a broker account, or treat a website
  click as execution.
- Remote deployment is out of scope for this change; local verification is
  required before any later VPS rollout.

## Verification

TDD changes start with failing tests for profile binding, fail-closed gates,
target-date/slot semantics, the three-item cap, operation-advice fields,
staleness/health blocking, and explicit `auto_order: false`. Run focused pytest
and ruff checks first, then the recommendation/API/health/research suites,
shell and JavaScript syntax checks, and a local CLI replay for each run slot.
The final report must distinguish development evidence from live-use proof and
must include the exact snapshot data-as-of and provider/health status.

## Design self-review

- No unresolved placeholders or optional execution behavior remain.
- The design preserves the user's capped stock-only advice scope.
- The 50%/15% target is a measurable acceptance target, not a promise of
  future returns.
- Empty recommendations are a valid safety outcome.
- Current caches and `auto-031` are explicitly prevented from being presented
  as live proof.
