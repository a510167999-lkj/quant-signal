# A-share Data Source Options

## Current Bottleneck

The runtime recommendation path is now Jiaoch-only for daily/minute market data. AKShare/Tushare material below describes isolated legacy/research adapters and is not a permitted live fallback. A 500-symbol scan still needs local cache warming and bounded Jiaoch requests; source errors fail closed instead of switching providers.

## Runtime Source Decision

- `MARKET_DATA_PROVIDER=jiaoch` is the production default.
- `JIAOCH_TOKEN` is the points-primary slot; `JIAOCH_STK_MINS_TOKEN` is the independent historical-minute/daily slot.
- The live adapter rejects credential echoes, non-canonical responses, non-Jiaoch cache rows, and unsupported adjustment modes.
- The recommendation publication gate requires every observed market source to be Jiaoch. `auto_order` remains permanently false and an empty result is valid.

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

## Research-Only Recommendation

1. Keep the Jiaoch-backed local SQLite cache as the primary runtime read path.
2. Keep AKShare/Tushare available only for explicitly isolated research fixtures; never use them as a live recommendation fallback.
3. Keep MOOTDX disabled for the Jiaoch-only publication path; any non-Jiaoch L1 observation blocks publication.
4. Keep industry taxonomy on the existing research adapter until a PIT-compatible Jiaoch industry contract is separately audited; that context cannot override the market-source gate.
