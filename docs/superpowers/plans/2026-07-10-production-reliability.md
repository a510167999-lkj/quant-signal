# Production Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a locally verified production-health command, authenticated API, deduplicated alert path, and VPS systemd checker before touching the VPS.

**Architecture:** Add a pure `app.production_status` module that evaluates existing artifacts without fetching or mutating market data. Expose it through `app.jobs` and `app.main`, then wrap it with a read-only shell checker for systemd state. Persist only a small redacted health-state file for alert transitions.

**Tech Stack:** Python 3.12, FastAPI, pytest, Ruff, Bash, systemd.

---

### Task 1: Production status evaluator

**Files:**
- Create: `app/production_status.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Test: `tests/test_production_status.py`

- [ ] **Step 1: Write failing evaluator tests**

Create fixtures with fixed `Asia/Shanghai` datetimes and temporary recommendation, lock, calendar, SQLite, industry, and AKShare status files. Cover a fresh empty recommendation as healthy, stale recommendation as unhealthy, more than three items as unhealthy, incomplete operation advice as unhealthy, stale lock as unhealthy, stale industry data as degraded, and fallback-aware provider failures.

```python
def test_empty_fresh_recommendation_is_healthy(health_settings, artifact_writer):
    artifact_writer.recommendations(items=[], running=False)
    result = build_production_status(health_settings, now=FIXED_NOW)
    assert result["status"] == "healthy"


def test_recommendation_over_limit_is_unhealthy(health_settings, artifact_writer):
    artifact_writer.recommendations(items=[complete_item()] * 4, running=False)
    result = build_production_status(health_settings, now=FIXED_NOW)
    assert check(result, "recommendations")["status"] == "unhealthy"
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_production_status.py -q`

Expected: import failure for `app.production_status`.

- [ ] **Step 3: Add bounded health settings**

Add environment-backed fields for recommendation age, running/lock age, calendar age, market-cache age, industry age, provider failure window, health state path, reminder interval, and alert enablement. Use conservative defaults and bounded parsers already present in `get_settings()`.

```python
production_recommendation_max_age_hours: int = 36
production_running_max_minutes: int = 90
production_calendar_max_age_hours: int = 48
production_market_cache_max_age_hours: int = 36
production_industry_max_age_hours: int = 72
production_provider_failure_window_hours: int = 24
production_health_state_path: str = "data/production_health_state.json"
production_health_reminder_hours: int = 6
production_health_alerts_enabled: bool = True
```

- [ ] **Step 4: Implement the pure evaluator**

Implement `build_production_status(settings, now=None)` with isolated check functions. Every check catches its own file/JSON/SQLite error and returns a structured result instead of raising.

```python
STATUS_RANK = {"healthy": 0, "degraded": 1, "unhealthy": 2}


def build_production_status(settings: Settings, now: datetime | None = None) -> dict[str, Any]:
    observed_at = now or now_cn()
    checks = [
        check_recommendations(settings, observed_at),
        check_recommendation_lock(settings, observed_at),
        check_trade_calendar(settings, observed_at),
        check_market_cache(settings, observed_at),
        check_industry_cache(settings, observed_at),
        check_provider_status(settings, observed_at),
    ]
    status = max((item["status"] for item in checks), key=STATUS_RANK.__getitem__)
    return {"status": status, "observed_at": observed_at.isoformat(), "checks": checks}
```

Recommendation completeness requires `entry_zone.low/high`, `levels.support/resistance/stop_loss/take_profit`, non-empty `risks`, and `trade_plans.short_term.horizon` or equivalent existing holding-period field. Zero items is valid. Use the cached trading calendar to avoid treating weekends/holidays as stale recommendation failures.

- [ ] **Step 5: Run focused tests**

Run: `.venv/bin/pytest tests/test_production_status.py -q`

Expected: all evaluator tests pass.

- [ ] **Step 6: Commit evaluator**

```bash
git add app/production_status.py app/config.py .env.example tests/test_production_status.py
git commit -m "feat: add production health evaluator"
```

### Task 2: CLI, API, and alert transitions

**Files:**
- Modify: `app/production_status.py`
- Modify: `app/jobs.py`
- Modify: `app/main.py`
- Test: `tests/test_production_status.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing CLI/API/alert tests**

Test the authenticated endpoint shape, CLI status mapping, redacted state persistence, first failure notification, unchanged failure suppression, reminder after the configured interval, and one recovery notification.

```python
@pytest.mark.parametrize((status, expected), [("healthy", 0), ("degraded", 1), ("unhealthy", 2)])
def test_production_check_exit_code(monkeypatch, status, expected):
    monkeypatch.setattr(jobs, "build_production_status", lambda settings: {"status": status, "checks": []})
    assert jobs.main(["production-check", "--no-alert"]) == expected


def test_production_status_endpoint(monkeypatch):
    monkeypatch.setattr(main, "build_production_status", lambda settings: {"status": "healthy", "checks": []})
    response = TestClient(main.create_app()).get("/api/production/status")
    assert response.status_code == 200
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest tests/test_production_status.py tests/test_api.py -q`

Expected: missing command/endpoint and transition helper failures.

- [ ] **Step 3: Implement alert transition state**

Compute a stable fingerprint from status and failing check names/messages. Send through a small webhook function only on a healthy-to-problem transition, fingerprint change, expired reminder interval, or recovery. Persist only status, fingerprint, and notification timestamps.

```python
def should_notify(previous: dict[str, Any], current: dict[str, Any], now: datetime, reminder_hours: int) -> bool:
    if previous.get("status") != current["status"]:
        return True
    if previous.get("fingerprint") != current["fingerprint"]:
        return True
    return current["status"] != "healthy" and reminder_expired(previous, now, reminder_hours)
```

- [ ] **Step 4: Add CLI and authenticated API**

Add `production-check` with `--no-alert`. Print JSON and return `0`, `1`, or `2`. Add `GET /api/production/status` behind `require_basic_auth`; the GET endpoint is read-only and never sends alerts.

- [ ] **Step 5: Run focused tests**

Run: `.venv/bin/pytest tests/test_production_status.py tests/test_api.py -q`

Expected: all pass.

- [ ] **Step 6: Commit CLI/API**

```bash
git add app/production_status.py app/jobs.py app/main.py tests/test_production_status.py tests/test_api.py
git commit -m "feat: expose production health checks"
```

### Task 3: Read-only VPS systemd checker

**Files:**
- Create: `deploy/check-production-health.sh`
- Create: `tests/test_production_health_script.py`

- [ ] **Step 1: Write failing shell-wrapper tests**

Create a temporary fake `systemctl` executable and fake Python health command. Verify all enabled/active units plus healthy CLI exits zero; disabled, inactive, failed, or nonzero application health produces a concise failure and nonzero exit. Verify output never contains environment values.

```python
def test_checker_fails_when_timer_disabled(tmp_path):
    result = run_checker(tmp_path, systemctl_state={"quant-signal-recommend.timer": "disabled"})
    assert result.returncode != 0
    assert "quant-signal-recommend.timer" in result.stdout
```

- [ ] **Step 2: Run test and verify failure**

Run: `.venv/bin/pytest tests/test_production_health_script.py -q`

Expected: checker script is missing.

- [ ] **Step 3: Implement the checker**

Use Bash arrays and separate `systemctl is-enabled`, `is-active`, and `is-failed` calls for the application service and four timers. Invoke `.venv/bin/python -m app.jobs production-check`; collect failures and return the most severe nonzero status without reading `.env`.

```bash
units=(
  quant-signal.service
  quant-signal-recommend.timer
  quant-signal-monitor.timer
  quant-signal-planned-exits.timer
  quant-signal-cache-warm.timer
)
```

- [ ] **Step 4: Run focused tests**

Run: `.venv/bin/pytest tests/test_production_health_script.py -q`

Expected: all pass.

- [ ] **Step 5: Commit checker**

```bash
git add deploy/check-production-health.sh tests/test_production_health_script.py
git commit -m "ops: add production systemd health check"
```

### Task 4: Local documentation and complete local gate

**Files:**
- Modify: `README.md`
- Create: `deploy/quant-signal-health.service`
- Create: `deploy/quant-signal-health.timer`
- Test: `tests/test_production_health_script.py`

- [ ] **Step 1: Add unit-contract tests**

Verify the timer runs every five minutes, the service uses `Type=oneshot`, and `ExecStart` points to the project checker.

- [ ] **Step 2: Add systemd units and operator instructions**

Document the one-command local check, exit-code contract, common findings, local-first deployment gate, and VPS read-only audit commands. Add a oneshot service and timer without installing them locally.

- [ ] **Step 3: Run the complete local gate**

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/python -m app.jobs production-check --no-alert
.venv/bin/pytest tests/test_production_health_script.py -q
```

Expected: tests and Ruff pass; healthy fixtures exit zero. Real local data may return degraded/unhealthy because it is intentionally stale, but JSON must be valid and the exit code must match its status.

- [ ] **Step 4: Exercise deterministic failure and recovery**

Run the focused tests that freeze time and mutate temporary artifacts from healthy to unhealthy and back. Confirm the failure is nonzero, duplicate alert is suppressed, and recovery is notified once.

- [ ] **Step 5: Commit local gate**

```bash
git add README.md deploy/quant-signal-health.service deploy/quant-signal-health.timer tests/test_production_health_script.py
git commit -m "docs: add production health operations runbook"
```

### Task 5: VPS read-only audit, deployment, and production verification

**Files:**
- Modify only if evidence requires: `deploy/check-production-health.sh`
- No changes outside `/home/ubuntu/quant-signal` and `quant-signal-*` units.

- [ ] **Step 1: Confirm the local gate evidence is still green**

Re-run full pytest, Ruff, CLI contract tests, and shell-wrapper tests immediately before connecting to the VPS. Stop if any gate fails.

- [ ] **Step 2: Perform a read-only VPS audit over Paramiko as user `ubuntu`**

Collect code revision, service/timer enabled/active/failed state, last trigger/result timestamps, health endpoint reachability, latest recommendation metadata, cache/calendar/industry timestamps, provider summary, and bounded recent logs. Do not print `.env`, credentials, tokens, holdings, or unrelated project state.

- [ ] **Step 3: Compare VPS evidence with local assumptions**

If unit names, paths, or artifact formats differ, update code/tests locally first and repeat the complete local gate. Do not patch production-only logic directly on the VPS.

- [ ] **Step 4: Deploy project-scoped changes**

Update `/home/ubuntu/quant-signal`, install only the two new `quant-signal-health` units, run `systemctl daemon-reload`, enable/start the health timer, and leave unrelated services untouched.

- [ ] **Step 5: Verify production behavior**

Run the real checker, capture JSON plus exit code, confirm all required timers, confirm recommendation date/count without exposing holdings, verify one controlled local-fixture failure/recovery test remains green, and observe at least one timer execution result.

- [ ] **Step 6: Final requirement audit**

Map every design requirement to test output, file evidence, or VPS command evidence. Keep the goal active if any requirement remains unverified.
