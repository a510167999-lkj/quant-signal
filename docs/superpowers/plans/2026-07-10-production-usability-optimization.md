# Production Usability Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe MOOTDX emergency daily-bar fallback, auditable recommendation selection funnels, and core-versus-enhancement production health classification.

**Architecture:** A dedicated MOOTDX daily adapter returns raw validated bars and XDXR events. `AkshareDataProvider` remains the owner of cache semantics and permits qfq incremental merge only over trusted cached history with no intervening corporate action. Recommendation generation records first-decision rejection reasons into a bounded summary plus JSONL audit, while production status classifies checks by operational criticality.

**Tech Stack:** Python 3.12, pandas, mootdx/tdxpy, SQLite, JSONL, FastAPI, pytest, Ruff.

---

### Task 1: MOOTDX raw daily adapter

**Files:**
- Create: `app/mootdx_daily.py`
- Modify: `app/config.py`
- Modify: `.env.example`
- Test: `tests/test_mootdx_daily.py`

- [ ] **Step 1: Write failing routing, paging, normalization, validation, and failover tests**

Use fake clients and a client factory. Assert `600519/510300/588000 -> 1`, `000001/159915 -> 0`, `430xxx/830xxx/920xxx -> 2`; every bars call uses integer frequency 9; pages use starts 0/800/1600; output is ascending and deduplicated; invalid OHLC or negative volume fails; connection False, exception, and empty response move to the next server; all clients close.

```python
def test_pages_daily_bars_with_frequency_nine():
    provider = MootdxDailyProvider(["good:7709"], client_factory=fake_factory)
    frame = provider.history("600519", start_date="2018-01-01", end_date="2026-07-10")
    assert [call["start"] for call in fake_factory.client.bar_calls] == [0, 800, 1600]
    assert {call["frequency"] for call in fake_factory.client.bar_calls} == {9}
    assert frame["date"].is_monotonic_increasing
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_mootdx_daily.py -q`

Expected: import failure for `app.mootdx_daily`.

- [ ] **Step 3: Implement the adapter and settings**

Implement `MootdxDailyProvider.history`, `corporate_actions`, explicit market routing, strict frame validation, per-server business probes, bounded pages and total elapsed budget. Add `ENABLE_MOOTDX_DAILY_FALLBACK`, `MOOTDX_DAILY_MAX_PAGES`, and `MOOTDX_DAILY_MAX_ELAPSED_SECONDS`.

```python
class MootdxDailyProvider:
    def history(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame: ...
    def corporate_actions(self, symbol: str, after_date: str) -> list[dict[str, Any]]: ...
```

- [ ] **Step 4: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/test_mootdx_daily.py -q && .venv/bin/ruff check app/mootdx_daily.py tests/test_mootdx_daily.py`

Commit: `feat: add validated mootdx daily adapter`

### Task 2: Safe cache-aware fallback integration

**Files:**
- Modify: `app/market_data.py`
- Modify: `app/main.py`
- Test: `tests/test_market_data.py`

- [ ] **Step 1: Write failing fallback safety tests**

Cover raw `adjust=""` fallback; qfq with no cache rejection; qfq with trusted cache and no XDXR appending only newer rows; dividend/rights/split/ETF adjustment blocking; hfq rejection; quality failure leaves SQLite unchanged; source labels identify raw versus incremental qfq.

```python
def test_qfq_fallback_appends_only_after_trusted_cache(monkeypatch, provider):
    seed_qfq_cache(provider, through="2026-07-09")
    monkeypatch.setattr(provider, "_fetch", fail_akshare)
    provider.mootdx_daily = FakeMootdx(actions=[], bars=[bar("2026-07-10")])
    frame, source = provider.history("600519", "a", adjust="qfq")
    assert frame.iloc[-1]["date"] == "2026-07-10"
    assert source == "MOOTDX incremental qfq fallback"
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_market_data.py -q`

Expected: new fallback tests fail because the provider has no MOOTDX daily integration.

- [ ] **Step 3: Implement minimal integration**

Inject the adapter into `AkshareDataProvider`, retain stale cache before the primary fetch, and call a `_mootdx_fallback` helper only after both AKShare paths fail. Merge qfq only when cached rows exist and XDXR after the cached max date is empty. Validate the combined frame before `_write_disk_cache`; never support hfq fallback.

- [ ] **Step 4: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/test_market_data.py tests/test_mootdx_daily.py -q && .venv/bin/ruff check app tests/test_market_data.py tests/test_mootdx_daily.py`

Commit: `feat: add safe mootdx incremental fallback`

### Task 3: Recommendation selection funnel and audit JSONL

**Files:**
- Modify: `app/config.py`
- Modify: `app/recommendations.py`
- Modify: `.env.example`
- Test: `tests/test_recommendations.py`

- [ ] **Step 1: Write failing funnel and audit tests**

Create deterministic candidates that are rejected by cooldown, analysis error, action, score, strict signal, strategy quality, announcement, news, and fund flow. Assert each candidate has exactly one first-decision reason, stage counts balance, a zero-result run still has a complete funnel, and the audit JSONL excludes headlines, tokens, auth and holdings.

```python
def test_zero_result_run_explains_first_rejection_reason(tmp_path):
    payload = service.generate_daily_recommendations(force=True)
    funnel = payload["summary"]["selection_funnel"]
    assert funnel["returned"] == 0
    assert sum(funnel["rejection_reasons"].values()) == funnel["considered"]
    assert read_jsonl(settings.recommendation_audit_path)[-1]["rejections"]
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_recommendations.py -q`

Expected: missing `selection_funnel` and audit file assertions fail.

- [ ] **Step 3: Implement first-decision recording**

Add `RECOMMENDATION_AUDIT_PATH`. Use one helper to record a symbol once, build bounded reason counts and up to 20 examples, append the full sanitized audit snapshot after every completed/skipped/failed run, and expose only summary plus bounded examples in latest output.

- [ ] **Step 4: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/test_recommendations.py -q && .venv/bin/ruff check app/recommendations.py tests/test_recommendations.py`

Commit: `feat: record recommendation selection funnel`

### Task 4: Core and enhancement health classification

**Files:**
- Modify: `app/production_status.py`
- Test: `tests/test_production_status.py`

- [ ] **Step 1: Write failing classification tests**

Assert recommendation/calendar/cache/lock failures affect `core_status`; industry/provider partial failures affect `enhancement_status`; overall status is the maximum; alert payload includes both; an enhancement failure never changes `core_status` from healthy.

```python
def test_enhancement_failure_does_not_mark_core_unhealthy(health_settings):
    result = build_production_status(health_settings, FIXED_NOW)
    assert result["core_status"] == "healthy"
    assert result["enhancement_status"] == "degraded"
    assert result["status"] == "degraded"
```

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/pytest tests/test_production_status.py -q`

Expected: missing `core_status` and `enhancement_status`.

- [ ] **Step 3: Implement classification and alert context**

Tag every check with `domain="core"|"enhancement"`, calculate both domain statuses, retain existing overall exit-code contract, and add both statuses to the webhook payload and fingerprint.

- [ ] **Step 4: Verify GREEN and commit**

Run: `.venv/bin/pytest tests/test_production_status.py -q && .venv/bin/ruff check app/production_status.py tests/test_production_status.py`

Commit: `feat: classify production health domains`

### Task 5: Local gate, real samples, and VPS rollout

**Files:**
- Modify: `README.md`
- Test: all tests

- [ ] **Step 1: Complete local gate**

Run full pytest and Ruff. Run deterministic failure/no-action/action-block/recovery tests. Run real read-only samples for `600519`, `000001`, `510300`, `159915` and compare the latest 20 raw days with the existing trusted cache; require dates/OHLC within tick tolerance, volume <=0.1% difference and amount <=0.5% difference.

- [ ] **Step 2: Document operator-visible behavior**

Document fallback source labels, hard XDXR gate, funnel fields, audit location, health domains, and the default-off feature flag. Do not present this internal file to the user as the delivery surface.

- [ ] **Step 3: Commit and re-run local gate**

Commit: `docs: document production usability safeguards`

- [ ] **Step 4: VPS read-only preflight**

Confirm current units, health state, MOOTDX server business probe, cache freshness and current code baseline. Do not change `.env` or service state during preflight.

- [ ] **Step 5: Deploy disabled, verify, then enable**

Sync tracked project files without `.env` or data, restart only `quant-signal`, run the fallback smoke with the flag disabled, add `ENABLE_MOOTDX_DAILY_FALLBACK=1` only after smoke passes, restart, and simulate AKShare failure through a non-production temporary cache/test command rather than corrupting live cache.

- [ ] **Step 6: Final audit**

Verify authenticated API, recommendation funnel structure, audit JSONL creation after the next controlled recommendation run, production health domain output, all systemd timers, and no unrelated VPS project changes.
