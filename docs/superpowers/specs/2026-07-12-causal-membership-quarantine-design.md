# Causal Membership Quarantine Design

## Scope

Iteration 022 tests one data-contract hypothesis only: an open session whose
historical `bak_basic` response is a verified semantic empty can be represented
causally by an immutable derived membership generation without inventing a
vendor snapshot or reading a later snapshot. Strategy parameters, trade
metrics, contaminated diagnostics, and final OOS remain untouched.

The derived representation is deliberately conservative. Every derived session
is quarantined from new BUY signals because a past snapshot plus same-day price
observations cannot prove that dynamic metadata such as ST status, name, or
delisting-stage classification did not change on the missing day. Existing
positions continue to use market evidence for valuation and exit; membership
quarantine must never delete a holding.

## Considered approaches

1. Insert carried rows into `daily_universe`. Rejected: that table is the
   normalized vendor fact bound to a same-partition raw receipt. Derived rows
   would corrupt raw-to-normalized hashes and falsely claim an exact snapshot.
2. Add an immutable causal-membership generation layer beside the vendor table.
   Selected: original attempts, receipts, generations, and rows remain
   unchanged while derived semantics receive their own hashes and lineage.
3. Search for the nearest past snapshot dynamically during replay. Rejected:
   results would depend on mutable store contents, could silently rebind after
   validation, and would not survive content-addressed offline audit.

## Frozen causal policy

For each covered open session, exact same-day vendor membership remains the
preferred authority. A derived generation may be published only when no
successful same-day `bak_basic` receipt exists and at least one same-day fetch
attempt proves all of the following from its raw body:

- HTTP success and a complete body;
- native response code `0` with the canonical requested field set;
- `items=[]`;
- terminal evidence recorded as the existing semantic-empty rejection;
- the frozen development temporal contract and source route match all other
  inputs.

The anchor is the greatest date strictly earlier than the target session that
has a verified, non-empty `bak_basic` receipt under the same source and temporal
authority. A later snapshot is never considered. If no past anchor exists, the
generation mode is `pre_anchor_quarantine` and contains no known membership.

For `causal_carry_forward`, every in-scope anchor row is retained as a known
membership observation even if it has no same-day price row; absence must not
be interpreted as delisting because it may be suspension. Codes observed in
the target day's market generation but absent from the anchor are recorded as
unknown-metadata observations. The current security master and future snapshots
must not fill their name, industry, listing, ST, or lifecycle fields.

Both derived modes have `signal_session_eligible=false`. Every derived row has
`signal_eligible=false`; unknown observations additionally have
`unknown_metadata=true`, and carried rows declare
`metadata_stale_possible=true`. This iteration contains no mechanism for
relaxing that quarantine.

The existing collector keeps its default fail-closed behavior. A dedicated
membership-planning path may classify a response as `semantic_empty` only after
all bounded retries produce raw-verifiable code-0 empty bodies with terminal
evidence. That controlled result continues to the target day's market
generation and membership materialization. Malformed JSON, API errors,
transport errors, incomplete bodies, authority mismatches, or mixed retry
outcomes still abort the collection. No empty response becomes a receipt.

## Storage contract

Add a focused membership schema rather than changing `daily_universe`:

- `membership_session_generations` records immutable generation identity,
  target date, mode, optional past anchor, frozen temporal/source authority,
  target market-generation identity, session eligibility, row/statistic roots,
  manifest root, status, and timestamps.
- `membership_generation_rows` records the complete projection for one
  generation: known carried members and same-day unknown observations, with
  `membership_present`, `observed_in_daily`, `unknown_metadata`,
  `metadata_stale_possible`, `signal_eligible`, and metadata anchor date.
- `membership_generation_evidence` binds the exact semantic-empty attempt IDs,
  request-semantics hashes, raw hashes, and terminal-event statuses used to
  justify the generation.
- `membership_session_head` publishes at most one immutable generation per
  target date.

Generation begin/resume, stage, verify, and publish operations use the existing
SQLite `BEGIN IMMEDIATE` pattern. A published head is content-addressed. Reuse
is allowed only when every contract and manifest hash is identical; otherwise
the operation fails as an immutable conflict. Exact vendor sessions do not need
a duplicated generation and remain an implicit `exact_snapshot` projection.

The generation manifest hashes the policy version, target date, mode, anchor
receipt identity, ordered semantic-empty evidence, market-generation manifest
and lineage, ordered row projection, all eligibility flags, and summary counts.
Created/published timestamps are evidence but do not alter the deterministic
content root.

## Read and consumer behavior

`AuditedPointInTimeUniverse` exposes a uniform projection:

- `membership_status_as_of(date)` reports exact versus derived mode, anchor
  date, gap age, session eligibility, known/unknown/absent counts, generation
  identity, and lineage root.
- `items_as_of(date)` returns all exact members or all derived known and unknown
  observations. It never hides quarantined rows by returning a deceptively
  complete eligible subset.
- `item_as_of(symbol, date)` returns the same eligibility and lineage fields.
- `seed_items(start, end)` includes only codes that have at least one exact,
  signal-eligible, known-metadata session in the range.

Strict historical candidate selection requires explicit
`signal_session_eligible is True`, `signal_eligible is True`, and
`unknown_metadata is False`. Missing eligibility fields fail closed. Historical
market breadth uses only signal-eligible rows as its market denominator but also
reports total projection count, unknown count, ineligible count, and metadata
coverage. A quarantined session produces an `unknown` market state and cannot
emit BUY. Trade validation rejects any qualified trade whose signal-day
membership or session is ineligible or whose recorded membership lineage does
not match the artifact.

Execution evidence remains separate from recommendation eligibility. Buy/sell
fill gates keep using same-day price, limit, and suspension evidence. Once a
position exists, a later missing membership row cannot remove it; valuation and
exit attempts continue and fail closed when market evidence does not permit an
execution.

## Coverage audit and artifact

Coverage resolves every open session in this order:

1. use and fully reconcile a verified non-empty same-day vendor snapshot; or
2. verify a published derived membership generation whose target date is the
   session and whose anchor is strictly earlier; otherwise fail.

For derived sessions, the audit verifies raw semantic-empty evidence again,
recomputes the generation manifest from immutable inputs, requires the bound
same-day market generation, and reconciles known, absent, and unknown code sets.
It does not require an absent carried code to have a same-day suspend event,
because ongoing suspensions are not equivalent to same-day suspension events.
It does require all observed daily codes to retain their bound factor and limit
evidence through the existing market-generation contract.

The audit reports exact-session count, quarantined-session count/rate, maximum
consecutive quarantined sessions, pre-anchor count, unknown-code counts, anchor
ages, and a membership-generation root. These fields and all new tables enter
the artifact table roots, logical manifest, artifact root, evidence authority,
and offline structural verification. Artifact export copies the selected past
anchor receipt and exact bound semantic-empty attempts in addition to the
target market generation. Any changed row, flag, anchor, attempt, terminal
event, or generation hash makes offline loading fail.

## Iteration 022 experiment boundary

The live proof window is `2016-09-30` through `2016-10-10`, development only.
The expected open-session shape is one normal past anchor followed by the known
semantic-empty target after the National Day closure. The run may collect only
calendar, stock-master, membership, and market evidence needed for this bounded
window. It must not run a strategy, validation metrics, 2024+ diagnostics, or
final OOS.

Primary success evidence:

- the target generation selects only the greatest strictly past anchor;
- exact and derived projections reproduce the intended known/unknown sets;
- all derived eligibility gates block BUY and market-state promotion;
- repeated materialization is idempotent with the same manifest/root;
- future snapshot insertion or mutation cannot change the earlier root;
- audit, artifact publish, offline load, and tamper rejection all pass;
- attempts and terminal events remain one-to-one and no credential is persisted.

The hypothesis is falsified by any future-snapshot dependency, authority mix,
unbound or unparsable semantic-empty attempt, fabricated vendor receipt,
recommendable derived/unknown row, hidden quarantine in breadth statistics,
holding deletion, non-deterministic root, offline lineage gap, or failing full
test/static gate.

Passing this bounded proof does not promote the source or authorize a strategy
run. The next experiment must measure the full 2016-2023 quarantine topology
before the remaining backfill. Its thresholds are frozen now: no more than 2%
of open sessions quarantined and no run longer than five consecutive open
sessions. Exceeding either threshold rejects this source as a sufficiently
complete sole historical membership authority; the thresholds are not relaxed
after seeing the result.
