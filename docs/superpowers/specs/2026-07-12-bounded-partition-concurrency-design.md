# Bounded Partition Concurrency Design

## Scope

Reduce immutable development backfill wall time without changing any request,
parser, temporal, receipt, generation, or artifact contract. Concurrency is
allowed only between independent canonical partitions. Strategy code and final
OOS remain untouched.

## Execution graph

Collection keeps explicit dependency phases:

1. collect the two calendar shards and eight stock-master shards concurrently;
2. derive the common open-session list only after both calendars verify;
3. collect independent daily membership receipts concurrently by session;
4. collect independent market-session generations concurrently by session;
5. inside one market-session generation, keep the four dataset shards ordered
   and publish only after all four verify;
6. run the existing coverage audit after every worker has terminated.

The CLI accepts `--workers` in `1..8`, defaulting to `1`. Worker count is
execution metadata, not request semantics: identical canonical requests may be
resumed across worker counts. Results are sorted by the original deterministic
plan order before reporting.

## Shared-state safety

- Clock attestation refresh and cached evidence are protected by one lock.
- Resume-index initialization and updates are protected by one lock.
- Each store operation continues to use its own SQLite connection and existing
  `BEGIN IMMEDIATE` generation/receipt invariants.
- Source and temporal authorities remain frozen private values.
- Tokens remain in process memory and are never included in task names,
  exceptions, logs, or executor state.

## Failure behavior

The first worker failure sets a cancellation flag, cancels pending tasks, waits
for running tasks to finish their current bounded request, and raises one
redacted collection error. Every started attempt retains its terminal event;
successful concurrent partitions remain resumable. No generation publishes
unless its own complete shard set passes. A resumed run fetches only missing
partitions regardless of worker count.

## Benchmark and promotion

Use the frozen development window 2023-12-28 through 2023-12-29. Compare
workers 1, 2, 4, and 8 with the same source, route, temporal contract, fields,
and row caps. Record wall time, request/attempt/event counts, normalized roots,
coverage/reconciliation hashes, failures, and source throttling.

Promote only a repeatable variant with at least 2.5x speedup over workers=1 and
identical correctness evidence. Otherwise retain workers=1 and record the
failed optimization. The live benchmark budget is 60 minutes.
