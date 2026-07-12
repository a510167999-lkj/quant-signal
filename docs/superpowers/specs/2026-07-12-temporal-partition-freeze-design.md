# Temporal Partition Freeze Design

## Objective

Freeze a contamination-aware research timeline before collecting multi-year
data or running a real strategy baseline. Previously observed strategy dates
must never be relabeled as final OOS, and the forward final OOS must remain
unreadable by development workflows until a separately reviewed strategy
freeze authorizes a one-time evaluation.

## Frozen roles

| Role | Start | End | Permitted use |
|---|---|---|---|
| `development` | 2016-01-01 | 2023-12-31 | PIT collection, purged walk-forward training and validation |
| `contaminated_diagnostic` | 2024-01-01 | 2026-07-03 | Data/execution diagnostics only; never promotion metrics |
| `embargo` | 2026-07-04 | 2026-07-12 | No strategy evaluation or tuning |
| `final_oos` | 2026-07-13 | open-ended | Sealed until a future strategy-freeze receipt authorizes evaluation |

The final-OOS boundary is forward-only because real strategy outputs already
cover dates through 2026-07-03. Historical periods can still support
walk-forward validation, but none can honestly satisfy the untouched final-OOS
completion gate.

## Canonical contract

Create one canonical JSON contract under `data/research_partitions/`. It binds:

- schema and policy versions;
- exact role boundaries and permitted operations;
- the sealed final-OOS state;
- hashes of the real result artifacts that establish contamination;
- the real PIT infrastructure window already inspected;
- the canonical contract SHA-256.

The contract hash is recorded in the append-only experiment ledger. Loaders
recompute it after removing only the self-hash field. Unknown fields, missing
fields, invalid dates, role overlap, role gaps, or a non-forward open-ended role
fail closed.

## Enforcement API

A focused module exposes:

- `load_temporal_partition_contract(path)` for canonical verification;
- `classify_date(contract, value)` for one exact role;
- `assert_range_allowed(contract, role, start_date, end_date, operation)`;
- `assert_final_oos_sealed(contract)`.

Development collection, artifact publication, validation, and backtest entry
points must provide a contract and role. For this iteration, only
`development` collection/publication and read-only contaminated diagnostics are
admissible. All final-OOS operations fail regardless of caller flags because no
strategy-freeze receipt exists yet.

## Data flow

The next real collection is bounded to 2016-01-01 through 2023-12-31. Its
request semantics and artifact manifest bind the temporal-contract hash and
role. Resume may reuse a partition only when source, network route, request
semantics, temporal role, and contract hash all match.

The 2024-01-02 through 2024-01-05 source-qualification artifact remains valid
as `contaminated_diagnostic`; it cannot be combined with the development
artifact or contribute promotion metrics.

## Failure and recovery

- Contract tampering fails before any network request or data read.
- A range crossing a role boundary is rejected rather than split implicitly.
- A final-OOS request is rejected before artifact resolution or provider use.
- A contract change creates a new identity; old receipts remain immutable and
  cannot be silently resumed under the new contract.
- Failed attempts and rejected boundary requests remain ledger-visible.

## Acceptance

Tests must prove exact boundary classification, leap/calendar handling,
overlap/gap/tamper rejection, final-OOS sealing, pre-I/O rejection, request and
artifact hash binding, cross-contract resume isolation, and contaminated metric
exclusion. Full pytest, ruff, CLI failure probes, and independent review must
pass. No strategy, sweep, walk-forward result, or final OOS is executed in this
iteration.
