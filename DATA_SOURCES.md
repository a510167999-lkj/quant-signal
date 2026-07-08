# A-share Data Source Options

## Current Bottleneck

The current AKShare path is usable for research, but it is too slow for a synchronous intraday wide scan when every candidate fetches daily bars from the network. On the VPS, one 360-day A-share history call has measured around 3 seconds. A 500-symbol scan therefore needs local caching and pre-warming first, regardless of which upstream is used. Fast intraday slots use `INTRADAY_SCAN_MAX_DEEP`; the 15:02 post-close slot can afford the wider `SCAN_MAX_DEEP` review.

## Implemented First

- Daily K-line SQLite cache: `MARKET_DATA_CACHE_PATH=data/market_data_cache.sqlite`.
- Cache-first `AkshareDataProvider`: fresh cache hits skip external data calls; stale cache can be used as a fallback if external data fails.
- Warm command: `python -m app.jobs warm-market-cache --max-deep 500 --workers 4 --lookback-days 620`.
- Systemd timer: `quant-signal-cache-warm.timer` runs on weekdays at 08:35 and 15:45.
- Optional MOOTDX L1 quote layer: `ENABLE_MOOTDX_L1_CONTEXT=1` attaches current quote, open gap, bid/ask spread, amount and near-limit tags to live recommendations and alert checks.
- Unified AKShare industry-board adapter: hot-sector classification, board constituents and 1/3/5/10-day board history all go through AKShare's `stock_board_industry_*` interfaces and reuse the same `BK` board code.
- Shared AKShare resilience layer: wrapped calls use bounded retries with backoff/jitter, bypass local proxies by default, and record recovered/failure status in `data/akshare_status.json`.

## Candidate Upstream Channels

### Keep AKShare, But Never As The Hot Path

AKShare remains useful because the codebase already supports it and it covers many Chinese-market endpoints. It is not a low-latency production source; upstream pages and anonymous interfaces can change, and AKShare itself publishes data caveats for stock endpoints.

Best use here: fallback and breadth data adapter, behind local SQLite.

### Tushare Pro

Tushare provides Python SDK and HTTP REST API access. It is more standardized than scraping-style calls and can return selected fields, which is helpful for batch ETL. It requires an account token and API permissions.

Implemented scope: optional primary daily-bar provider behind `MARKET_DATA_PROVIDER=tushare`. The adapter uses `pro_bar`, stores normalized OHLCV in the existing SQLite cache, and can fallback to AKShare when Tushare is unavailable.

Best use here: VPS-friendly daily ETL for OHLCV, trade calendar, fundamentals and some reference datasets.

### BaoStock

BaoStock is free and exposes historical A-share K-line APIs such as `query_history_k_data_plus`. It is a good low-cost backup for daily bars, but not a complete real-time solution.

Best use here: secondary historical daily-bar fallback.

### JQData / JoinQuant

JQData offers local Python SDK and HTTP access, with A-share prices, trading days, industry constituents, money flow, margin data and tick/current interfaces listed in its SDK docs. It requires an account.

Best use here: more complete research and point-in-time datasets; good candidate if we want cleaner industry/constituent history.

### RQData / Ricequant

RQData documents A-share historical daily/minute/tick and real-time data, plus funds, convertibles, futures, options and alternative datasets. It is a professional data product rather than a free crawler.

Best use here: paid production-grade data layer if the platform becomes more serious.

### QMT / miniQMT

QMT is closest to an execution-side data source because it comes from the broker terminal ecosystem and exposes Python APIs. It usually implies a Windows/QMT environment and account setup, so it is not a fit for this project's VPS-only recommendation path.

Best use here: not a primary source for this deployment. Reconsider only if a separate Windows broker-side bridge is introduced later.

### mootdx / TDX

mootdx can read TongDaXin online quotes and local TDX files, supports Linux, and is open-source. It can be fast for quote-style data, but it is still unofficial infrastructure and should be treated as a supplemental source.

Implemented scope: optional L1 quote adapter behind `ENABLE_MOOTDX_L1_CONTEXT=1`. It can be probed with:

```bash
python -m app.jobs mootdx-l1-check \
  --symbols 600519,000001,301308,002607 \
  --servers "58.63.254.217:7709,116.205.171.132:7709" \
  --timeout-seconds 3
```

Best use here: fast 09:32/open, 14:55/pre-close, 15:02/post-close context and trading-session alert checks. It should not replace the local daily K-line cache, official exchange lists, CNINFO announcements, or research backtest data.

## Recommendation

1. Keep the new local SQLite cache as the primary read path.
2. Use `MARKET_DATA_PROVIDER=tushare` when a Tushare Pro token is available; keep AKShare as fallback.
3. Keep MOOTDX enabled only as a live L1 overlay. Refresh the VPS quote-server list with `mootdx bestip -l 5 -v` when quote latency rises.
4. Keep industry taxonomy on AKShare industry-board `BK` codes unless a future paid source can provide point-in-time industry membership; mixing taxonomies makes hot-sector selection and attribution harder to audit.
5. Run a timed bake-off on the VPS: 100 symbols x 620 trading days, measuring success rate, median latency, p95 latency, and data-field completeness.
6. Promote Tushare or JQData/RQData only after token/account setup and latency measurements. Do not replace the cache with another network source; use network sources only to refresh the cache.
