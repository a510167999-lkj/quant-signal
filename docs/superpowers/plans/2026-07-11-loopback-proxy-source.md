# New Source Loopback Proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the controlled PIT collector reach the new HTTPS source through an explicitly configured local loopback HTTP proxy without weakening host pinning, secret handling, immutable receipts, or fail-closed coverage.

**Architecture:** Validate the proxy once in the source resolver and carry an immutable, non-secret route descriptor into both the urllib transport and request semantics. Direct mode retains an empty proxy handler; proxy mode installs an HTTPS-only handler for a validated loopback endpoint.

**Tech Stack:** Python 3.12, urllib, ipaddress, socket, pytest, ruff, SQLite receipt store.

---

### Task 1: Freeze loopback proxy validation

**Files:**
- Modify: `app/research_pit_sources.py`
- Test: `tests/test_research_pit_sources.py`

- [ ] **Step 1: Write failing source-resolution tests**

Set `JIAOCH_PROXY_URL=http://127.0.0.1:7897` and assert the source exposes `proxy_url` and `network_route="loopback_http_proxy"`. Parametrize invalid HTTPS, remote, credentialed, missing-port, path, query, and fragment values. Assert direct mode remains `proxy_url is None` and `network_route == "direct"`.

- [ ] **Step 2: Verify RED**

Run `.venv/bin/pytest -q tests/test_research_pit_sources.py`.

Expected: failures because proxy route fields and validation do not exist.

- [ ] **Step 3: Implement the minimal validator**

Add immutable source fields:

```python
proxy_url: str | None = None
network_route: str = "direct"
```

Implement `_validated_loopback_proxy_url` with `urlparse`, `ipaddress.ip_address`, and `socket.getaddrinfo`. Require scheme `http`, port `1..65535`, no credentials, path only empty or `/`, and no params/query/fragment. Accept only loopback IPs; for `localhost`, require every resolved address to be loopback. Read only `JIAOCH_PROXY_URL` for the new profile.

- [ ] **Step 4: Verify GREEN**

Run the Task 1 command. Expected: all source tests pass.

### Task 2: Wire the validated route into the transport

**Files:**
- Modify: `app/research_pit_collector.py`
- Test: `tests/test_research_pit_collector.py`

- [ ] **Step 1: Write failing transport tests**

Patch `build_opener`. Construct `UrllibTushareTransport(proxy_url=None)` and assert `ProxyHandler({})`; construct with `http://127.0.0.1:7897` and assert an HTTPS-only proxy mapping. Confirm `_NoRedirectHandler` remains installed.

- [ ] **Step 2: Verify RED**

Run `.venv/bin/pytest -q tests/test_research_pit_collector.py -k 'proxy or path_per_interface'`.

Expected: failure because the transport has no `proxy_url` argument.

- [ ] **Step 3: Implement the minimal transport change**

```python
def __init__(self, opener: Any = None, *, proxy_url: str | None = None) -> None:
    proxy_handler = ProxyHandler({"https": proxy_url} if proxy_url else {})
    self._opener = opener or build_opener(proxy_handler, _NoRedirectHandler())
```

Do not read environment proxy variables inside the transport or alter redirect, size, TLS, or response handling.

- [ ] **Step 4: Verify GREEN**

Run the Task 2 command. Expected: selected tests pass.

### Task 3: Bind route identity to immutable request semantics

**Files:**
- Modify: `app/research_pit_collector.py`
- Modify: `app/jobs.py`
- Test: `tests/test_research_pit_collector.py`
- Test: `tests/test_research_pit_attempts.py`

- [ ] **Step 1: Write failing semantics and job-wiring tests**

Construct a collector with `network_route="loopback_http_proxy"` and `proxy_endpoint="http://127.0.0.1:7897"`. Assert semantics contain both keys, contain no source token, and the job passes the source route into the transport and collector.

- [ ] **Step 2: Verify RED**

Run `.venv/bin/pytest -q tests/test_research_pit_collector.py tests/test_research_pit_attempts.py -k 'route or proxy or semantics'`.

Expected: failures because route metadata is not accepted or recorded.

- [ ] **Step 3: Implement minimal semantics and wiring**

Add `network_route: str = "direct"` and `proxy_endpoint: str | None = None` to `ControlledTushareCollector.__init__`. Accept only `direct` and `loopback_http_proxy`, enforce endpoint absence/presence, and add both keys to `_request_semantics`. In `app/jobs.py`, construct `UrllibTushareTransport(proxy_url=source.proxy_url)` and pass route fields to the collector.

- [ ] **Step 4: Verify GREEN and redaction**

Run the Task 3 command. Expected: selected tests pass and no token appears in stored semantics.

### Task 4: Local regression and adversarial verification

**Files:**
- Modify only if a failing test identifies a route-specific defect.

- [ ] **Step 1: Run focused gates**

```bash
.venv/bin/ruff check app/research_pit_sources.py app/research_pit_collector.py app/jobs.py tests/test_research_pit_sources.py tests/test_research_pit_collector.py tests/test_research_pit_attempts.py
.venv/bin/pytest -q tests/test_research_pit_sources.py tests/test_research_pit_collector.py tests/test_research_pit_attempts.py tests/test_research_pit_collector_adversarial.py tests/test_research_pit_collector_secret_toctou.py
```

Expected: all focused gates pass.

- [ ] **Step 2: Run full local gate**

```bash
git diff --check
.venv/bin/pytest -q
```

Expected: at least 807 tests pass; only the existing Starlette/httpx deprecation warning may remain.

### Task 5: Resume the preregistered real four-session collection

**Files:**
- Append through API: `data/research_experiments/ledger.jsonl`
- Runtime only: `tmp/jiaoch-event-window-20240102-20240105/`

- [ ] **Step 1: Resume with the explicit local route**

```bash
export JIAOCH_TOKEN="$(launchctl getenv JIAOCH_TOKEN)"
export JIAOCH_PROXY_URL="${https_proxy}"
.venv/bin/python -m app.jobs research-pit-fetch-tushare \
  --source-profile jiaoch \
  --store-dir tmp/jiaoch-event-window-20240102-20240105 \
  --start-date 2024-01-02 --end-date 2024-01-05 \
  --timeout-seconds 30
```

Expected: preserve failed attempts, fetch missing partitions, and never print the token.

- [ ] **Step 2: Audit and publish**

Run `research-pit-audit-store`, capture its exact coverage hash, and pass it to `research-pit-publish-universe`. Expect four open sessions, zero unresolved in-scope differences, `final_oos_eligible=false`, and a deterministic artifact root.

- [ ] **Step 3: Verify offline replay**

Open the artifact with `AuditedPointInTimeUniverse.from_file`; verify each session's `items_as_of`, causal evidence, artifact root, and final-OOS false without the source store or network.

- [ ] **Step 4: Review and close the experiment**

Perform independent read-only review of proxy security, receipt semantics, reconciliation, and tests. Append `completed`, `reviewed`, and `decision` events for iteration 018 through `append_experiment_event`. Do not run strategy, sweep, walk-forward, or final OOS.
