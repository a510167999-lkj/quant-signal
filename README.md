# A-share Quant Signal Platform

个人使用的 A 股和 A 股 ETF 买卖点研究台。当前版本是 FastAPI + 静态 Web 前端，适合直接部署到个人 VPS；微信小程序后续可复用同一套 API。

> 仅供个人量化研究和交易辅助，不构成投资建议。

## Features

- A 股、A 股 ETF 日线分析
- 全 A 股机会发现：先从全市场快照预筛，再对候选股做深度日线信号扫描
- 大盘环境过滤：用沪深 300 ETF、创业板 ETF 判断市场风险，弱市时提高入选门槛
- 行业强弱加分：缓存强势行业成分股，强势行业内的候选股优先排序
- 历史信号质量过滤：统计历史 `BUY/WATCH` 信号后 10 个交易日的胜率、平均收益和回撤
- 回测按信号日后一交易日开盘入场，避免同 K 线成交偏差
- 外部消息风险叠加：近期负面标题会降低排名，高风险消息会阻断推荐
- 公告事件叠加：先读 Jiaoch/Tushare 兼容 `anns_d`，不可用时回退巨潮信息披露；立案、处罚、退市、风险警示等高风险公告直接阻断推荐，正向公告先展示和记录，暂不作为排名加分
- 资金流确认层：近期主力净流入加分，持续主力流出降权或阻断推荐
- 推荐后验证：跟踪已推荐标的 1/3/5/10 个交易日收益、10 日胜率和不利波动
- 可执行性过滤：次日开盘入场只使用前收与开盘时已知字段，剔除高开过大、低开破坏信号和接近涨停的难成交样本；入场日完整振幅仅可作收盘后诊断，不能反向决定开盘是否买入
- 交易日北京时间 09:00、09:32、14:55、15:02 自动生成开盘/收盘策略快照
- 推荐扫描带跨进程锁；运行中再次触发会返回当前任务状态，不重复开扫
- 独立持仓跟踪：默认持续追踪 `159567` 和 `520700`，展示 L1 现价、涨跌、支撑/止损/止盈距离和策略状态
- 交易时段自动监控最近推荐标的，优先用 MOOTDX L1 快照取最新价，触发止损、止盈、破支撑、策略转弱时记录提醒
- 可解释信号：趋势、MACD、RSI、突破、风控
- 输出买入观察、持有、减仓、卖出等操作状态
- 关键价位：入场区间、支撑、压力、止损、止盈
- 简易回测：策略收益、买入持有收益、最大回撤、胜率
- 观察池与批量扫描

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## API examples

```bash
curl -X POST http://127.0.0.1:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"510300","market":"etf","lookback_days":360,"adjust":"qfq"}'
```

Market values:

- `a`: A 股，例如 `600519`
- `etf`: A 股 ETF，例如 `510300`

## VPS deployment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `BASIC_AUTH_USER` and `BASIC_AUTH_PASSWORD` in `.env` if the service is reachable from the public internet.

This deployment currently uses `/home/ubuntu/quant-signal`, systemd service `quant-signal`, backend port `127.0.0.1:8010`, and Nginx on `https://quant.agentslee.online/`.

Then use the examples in `deploy/quant-signal.service`, `deploy/nginx.conf`, and the timer units under `deploy/`.

Daily jobs:

```bash
python -m app.jobs generate-recommendations --force --run-slot pre_open
python -m app.jobs generate-recommendations --force --run-slot open_confirm
python -m app.jobs generate-recommendations --force --run-slot pre_close
python -m app.jobs generate-recommendations --force --run-slot post_close
python -m app.jobs warm-market-cache --max-deep 500 --workers 4 --lookback-days 620
python -m app.jobs mootdx-l1-check --symbols 600519,000001,301308,002607 --timeout-seconds 3
python -m app.jobs monitor-recommendations --force
python -m app.jobs monitor-planned-exits --force   # post-close: profit-lock / calendar-gap planned exits
```

周末或收盘后准备下一交易日清单时，可显式使用
`python -m app.jobs generate-recommendations --force --run-slot pre_open --target-trade-date next`。
快照最多返回 3 只 A 股，并在网页中标注目标交易日、数据截至日、研究开发候选状态和“不自动下单”边界；50% 年化 / 15% 最大回撤是策略验收目标，不是单次推荐或实盘证明。
生产推荐还绑定 `primary_50_return_15_drawdown` profile：它要求完整的成本/滑点后收益、回撤、胜率 Wilson 下界、盈亏比、Profit Factor、Calmar、滚动 12 个月、PIT/时间分区和健康证据。证据文件缺失或门槛未通过时，网页显示“今日不推荐”，不会用未经验证的候选填满 3 个名额。
研究验证成功时会生成 content-addressed profile evidence receipt，并在实验 ledger 中绑定报告、源数据和 ledger anchor；receipt 缺少任一 hash、PIT 权威、双成本/环境证据或全滚动窗口时保持 `incomplete`，不会被推荐服务当成可用 profile。

当前股票池审计采用“生成”和“激活”分离的两步流程。`research-current-pool-audit`
仍只写入内容寻址的不可变审计文件；确认该文件后，再将它原子激活到运行时路径：

```bash
python -m app.jobs research-current-pool-audit \
  --universe-path data/current_pool_inputs/universe.json \
  --history-summary-path data/current_pool_inputs/history-summary.json \
  --risk-path data/current_pool_inputs/risk.json \
  --output-dir data/current_pool_audits

python -m app.jobs research-current-pool-publish \
  --audit-path data/current_pool_audits/<canonical_sha256>.json
```

`research-current-pool-publish` 默认发布到 `CURRENT_POOL_AUDIT_PATH`，并按
`PRODUCTION_CURRENT_POOL_MAX_AGE_HOURS` 检查时效；一次性路径可用 `--target-path`
覆盖，一次性时效可用 `--max-age-hours` 覆盖。命令先在目标目录内建立原字节临时快照，
对该快照执行 schema、canonical hash、来源、风险完整性、结构化股票池和时效的严格校验，
然后执行 `fsync` 和原子替换。它不重新序列化 JSON，因此目标字节和源文件完全一致；
校验或替换失败时旧目标保持不变。重复发布或源路径已等于目标路径时安全幂等，输出
`published=false`。该步骤只激活已验证的开发边界审计，不会把
`production_recommendation_eligible=false` 提升为实盘证明。

Production timer units:

- `quant-signal-recommend.timer`: weekdays 09:00, 09:32, 14:55 and 15:02 China time (`Timezone=Asia/Shanghai`). The job skips non-trading days via the A-share trading calendar and records `run_slot` in each recommendation snapshot.
- `quant-signal-monitor.timer`: every 5 minutes from 09:00 to 15:55 on weekdays; the job only acts inside A-share trading windows.
- `quant-signal-planned-exits.timer`: weekdays 15:05 China time (after `post_close`); scans recent recommendations for profit-lock and pre-calendar-gap planned exits using completed daily bars.
- `quant-signal-cache-warm.timer`: weekdays 08:35 and 15:45 China time; it refreshes the local daily K-line cache before the morning scan and after the close.
- `quant-signal-health.timer`: every 5 minutes; checks its own timer, recommendation/monitor/cache/planned-exit oneshot failure states, and recommendation, lock, calendar, cache, industry and provider health.

Production health check:

```bash
python -m app.jobs production-check --no-alert
deploy/check-production-health.sh
```

The application command returns `0` for healthy, `1` for degraded, and `2` for unhealthy. An empty recommendation list is valid; stale output, a stale scan lock, more than three recommendations, incomplete operation advice, an incomplete/tampered profile receipt, an industry cache with a research-only source/schema, expired critical data, repeated provider failure, or an SQLite cache whose latest `daily_bars.date` falls behind the trade calendar are reported explicitly. Without `--no-alert`, state changes and periodic unresolved reminders reuse `ALERT_WEBHOOK_URL`; unchanged failures are suppressed and recovery is notified once.

Always complete the local gate before installing or changing VPS units:

```bash
pytest -q
ruff check .
pytest tests/test_production_status.py tests/test_production_health_script.py -q
```

On the VPS, audit first with read-only `systemctl is-enabled`, `systemctl is-active`, `systemctl is-failed`, and `production-check --no-alert`. Install only `quant-signal-health.service` and `quant-signal-health.timer` after the audit matches the local assumptions.

Run slots:

- `pre_open`: 09:00 盘前候选。Uses previous-close/cached context and skips L1 confirmation because call-auction/open data is not stable yet.
- `open_confirm`: 09:32 开盘确认。Uses MOOTDX L1 quote context to confirm open gap, current liquidity, spread and near-limit risk.
- `pre_close`: 14:55 尾盘策略。Uses L1 context for late-day opportunity/risk checks before the close.
- `post_close`: 15:02 收盘复盘。Runs the full scan profile for end-of-day review and next-day candidates.

## Data notes

The current data layer uses a local cache plus practical no-account adapters. Most A-share historical bars and industry-board data come through AKShare-compatible paths:

- `stock_zh_a_hist` for A 股
- `fund_etf_hist_em` for A 股 ETF
- `stock_zh_a_spot` for all-market A-share prefiltering
- `stock_zh_a_disclosure_report_cninfo` for CNINFO A-share disclosure announcements
- `stock_board_industry_name_em` for industry-board list and `BK` board codes
- `stock_board_industry_cons_em` with the same `BK` board code for hot-sector constituents and candidate pools
- `stock_board_industry_hist_em` with the same `BK` board code for 1/3/5/10-day hot-sector windows and industry-rotation research
- `stock_lhb_detail_em` for Eastmoney dragon-tiger/public-trading detail research
- Fallbacks: `stock_zh_a_daily`, `fund_etf_hist_sina`

Official margin eligibility is fetched separately from exchange sources:

- SSE current collateral, financing-buy, and short-sell lists from [SSE margin securities](https://www.sse.com.cn/services/tradingservice/margin/info/againstmargin/) static data files.
- SZSE underlying and collateral reports from [SZSE margin underlying securities](https://www.szse.cn/disclosure/margin/object/index.html) and [SZSE collateral securities](https://www.szse.cn/disclosure/margin/securites/index.html).
- Historical research can attach SZSE underlying reports as-of each signal date with `--margin-eligibility-context`. SSE historical eligibility is intentionally not backfilled from the current SSE list, because that would introduce lookahead.

AKShare and upstream public data endpoints may occasionally change or rate-limit requests. Treat the platform as a research assistant and verify important signals before trading.

The data adapter bypasses local `http_proxy` / `https_proxy` by default because Eastmoney endpoints are often less reliable through desktop proxies. Set `MARKET_DATA_USE_PROXY=1` only when your VPS must use a proxy.

AKShare calls are wrapped by a shared retry layer. `AKSHARE_MAX_RETRIES`, `AKSHARE_RETRY_BASE_DELAY_SECONDS`, `AKSHARE_RETRY_JITTER_SECONDS` and optional `AKSHARE_MAX_ELAPSED_SECONDS` control retry behavior. Endpoints that already retry internally, such as industry-board and announcement/news adapters, are capped at lower outer retry counts to avoid multiplying delays. Recovered calls and final failures are recorded at `AKSHARE_STATUS_PATH`; the latest status is available from `/api/data/akshare-status`.

Optional Tushare Pro daily-bar adapter:

- Set `MARKET_DATA_PROVIDER=tushare` and `TUSHARE_TOKEN=...` to use Tushare Pro as the primary daily K-line source on Linux VPS.
- Tushare data is still written into the same SQLite cache, so recommendation scans keep reading from local cache whenever it is fresh.
- Keep `TUSHARE_FALLBACK_TO_AKSHARE=1` unless you want Tushare failures to fail requests immediately.
- QMT/MiniQMT is not used for the VPS deployment path because it depends on a local logged-in broker client environment.

Optional MOOTDX L1 quote layer:

- Set `ENABLE_MOOTDX_L1_CONTEXT=1` to attach TongDaXin L1 snapshots to morning recommendations and trading-session alert checks.
- Set `MOOTDX_SERVERS=host1:7709,host2:7709` to avoid relying on `~/.mootdx/config.json`. Run `mootdx bestip -l 5 -v` occasionally to refresh fast hosts for the VPS network.
- The L1 layer reads current price, previous close, open, high, low, amount, bid1/ask1, spread and server time. It adds tags such as `l1_open_gap_gt_5`, `l1_near_limit_up`, and `l1_spread_lte_0_2`.
- Treat MOOTDX as a fast supplemental quote source. It is not the source of truth for historical backtests, statutory disclosure, exchange eligibility, or final trade confirmation.

Optional MOOTDX emergency daily fallback:

- Set `ENABLE_MOOTDX_DAILY_FALLBACK=1` only after validating the configured TDX servers from the VPS.
- Unadjusted requests can use validated MOOTDX daily bars after both AKShare paths fail.
- `qfq` fallback requires an existing trusted SQLite qfq history. MOOTDX may append only dates after that cache when XDXR reports no intervening dividend, rights issue, stock split, consolidation, or ETF share adjustment.
- A material XDXR event, unavailable XDXR data, missing trusted cache, invalid OHLC, or an excessive price discontinuity blocks the merge. The provider then uses a still-acceptable stale cache or fails explicitly.
- `hfq` never falls back to MOOTDX. Raw prices are never stored under a qfq/hfq cache key.
- MOOTDX daily paging always uses integer frequency `9`; each page is limited to 800 bars and every configured server must pass a real non-empty bars request.

Recommendation selection audit:

- Every recommendation run includes `summary.selection_funnel`, including valid zero-result, skipped, and failed runs.
- The funnel reports snapshot, prefiltered, analyzed, qualified, returned, and first-decision rejection counts.
- A sanitized full audit is appended to `RECOMMENDATION_AUDIT_PATH`; it contains symbols, bounded error details, rejection reasons, and selected action/score only. It does not store credentials, holdings, or news article bodies.
- Production health exposes `core_status` for recommendation/calendar/cache/lock availability and `enhancement_status` for industry/provider overlays, while preserving the existing overall status and CLI exit codes.

The A-share trading calendar is cached at `TRADE_CALENDAR_CACHE_PATH`. If AKShare's calendar endpoint is temporarily unavailable, the scheduler uses the latest cache; without a cache it skips the run instead of guessing from weekdays.

Preferred source hierarchy for future hardening:

- Trading calendar and rules: Shanghai Stock Exchange / Shenzhen Stock Exchange official pages. Current reference pages: [SSE annual market-close schedule](https://www.sse.com.cn/disclosure/dealinstruc/closed/) and [SZSE trading calendar](https://www.szse.cn/aboutus/calendar/index.html).
- Company announcements and statutory disclosure: exchange announcement pages, [SSE latest announcements](https://www.sse.com.cn/disclosure/listedinfo/announcement/), and [CNINFO](https://www.cninfo.com.cn/). CNINFO is operated by Shenzhen Securities Information Co. and is a statutory disclosure platform; CSRC has also identified CNINFO as a designated disclosure site for ChiNext information disclosure.
- Financing/margin eligibility: official SSE/SZSE margin lists first; current live recommendations emit `margin_*` tags from this source.
- Market data adapter: [AKShare stock data](https://akshare.akfamily.xyz/data/stock/stock.html) for personal research, with source and cache metadata retained.
- Industry-board classification and history: AKShare's industry-board interfaces are the primary source. The stored `BK` board code is reused for board constituents and board K-line history, so hot-sector classification, sector constituents and 1/3/5/10-day sector strength use the same taxonomy. Current board constituents are not historical constituents; do not treat them as no-lookahead stock membership.
- Fund-flow confirmation: Eastmoney fund-flow interfaces through AKShare; use as confirmation/risk overlay, not as an unverified primary signal.
- Dragon-tiger / public trading information: exchange pages such as [SSE public trading information](https://www.sse.com.cn/disclosure/diclosure/public/) are the preferred official references; current research uses AKShare/Eastmoney as a practical historical adapter and drops `上榜后*日` future-return columns before tagging.
- News headlines: only as risk context; statutory announcements should take precedence over media headlines.

## Selection model

Daily recommendation is a staged filter:

1. Whole-market prefilter: remove ST/delist/new-stock markers, low liquidity, extreme limit-up/limit-down names, and unsuitable price ranges.
2. Hot-industry candidate pool: rank active industries first, then deep-scan only the top 3 industries and the top 3 liquid/popular constituents from each industry. The dashboard also splits hot industries by 1-day, 3-day, 5-day and 10-day cumulative strength. If industry data is unavailable, the job falls back to the whole-market liquidity pool.
3. Morning L1 context: after the open, optional MOOTDX quotes refresh current price,成交额, bid/ask spread, open gap and near-limit status for the candidate pool.
4. Market/industry context: defensive markets raise the score threshold; strong industry membership adds ranking bonus.
5. Stock-level quality: trend, MACD, RSI, breakout, ATR risk, and historical signal-outcome quality decide whether the stock can enter the final pool.
6. Announcement event overlay: recent CNINFO disclosure titles are scored first as statutory risk context. Hard negative events block a candidate; positive events are displayed and saved, but do not add rank score until larger backtests prove value.
7. News risk overlay: recent public headlines are treated as untrusted risk context. They can downgrade or block a candidate, but they are not a primary buy signal.
8. Fund-flow overlay: recent main-fund outflows can downgrade or block a candidate; positive fund flow only adds a small ranking bonus.

Each single-stock analysis now emits two plans:

- `short_term`: a 3-10 trading day signal plan. It uses the existing entry zone, stop loss, support/resistance and take-profit levels, and is intentionally strict about not chasing large gap-up moves.
- `long_term`: a 1-6 month rolling holding plan. It uses the 20/60-day trend, 60-day drawdown, 20-day momentum and volatility to decide whether a stock is suitable for core holding, pullback accumulation, observation, or long-term avoidance.

Important knobs:

- `SCAN_MAX_DEEP`: upper bound for how many prefiltered stocks receive full daily-line analysis. With the default hot-industry mode, the practical pool is usually `SCAN_INDUSTRY_TOP_N * SCAN_PER_INDUSTRY_TOP_N` unless industry data is unavailable.
- `INTRADAY_SCAN_MAX_DEEP`: max deep-scan count for the fast intraday slots: 09:00, 09:32 and 14:55. The default is `120`; 15:02 post-close review still uses `SCAN_MAX_DEEP`.
- `SCAN_INDUSTRY_TOP_N`: how many hottest industries are eligible for the short-term pool. The default is `3`.
- `SCAN_PER_INDUSTRY_TOP_N`: how many liquid/popular constituents each eligible industry can contribute. The default is `3`.
- `INDUSTRY_TOP_N`: how many active industries are fetched into the industry-strength cache. The default is `50`; scanning can still use only the hottest subset through `SCAN_INDUSTRY_TOP_N`.
- `MARKET_DATA_CACHE_PATH`: persistent SQLite cache for daily K-lines. Recommendation scans read this before calling external data sources; `warm-market-cache` refreshes it ahead of time.
- `MARKET_DATA_PROVIDER`: `akshare` by default, or `tushare` for VPS-friendly Tushare Pro daily bars with optional AKShare fallback.
- `AKSHARE_MAX_RETRIES`: max attempts for wrapped AKShare calls. The default is `3`.
- `AKSHARE_MAX_ELAPSED_SECONDS`: optional total outer retry budget for each wrapped AKShare call. `0` disables the extra budget.
- `AKSHARE_STATUS_PATH`: JSON status file for AKShare recovered/failure events. The default is `data/akshare_status.json`.
- `TUSHARE_TOKEN`: Tushare Pro token used when `MARKET_DATA_PROVIDER=tushare`.
- `TUSHARE_FALLBACK_TO_AKSHARE`: when using Tushare, fallback to AKShare if Tushare is unavailable or returns unusable data.
- `ENABLE_MOOTDX_L1_CONTEXT`: enable MOOTDX L1 snapshots for live recommendations and monitor alerts.
- `MOOTDX_SERVERS`: comma-separated TongDaXin quote servers. Leave empty only if the runtime has a valid MOOTDX bestip config.
- `MOOTDX_TIMEOUT_SECONDS`: per-connection quote timeout. The default is `3`.
- `HOLDINGS_PATH`: personal holdings file. The default seeds `159567` and `520700`; optional `cost_price` and `shares` fields enable P/L display.
- `MIN_BACKTEST_TRADES`: minimum historical signal count.
- `MIN_BACKTEST_WIN_RATE`: minimum historical 10-day signal win rate.
- `MAX_BACKTEST_DRAWDOWN`: maximum historical strategy drawdown allowed.
- `MIN_BACKTEST_AVG_RETURN`: minimum historical average 10-day signal return.
- `MAX_BACKTEST_AVG_ADVERSE`: maximum historical average adverse movement.
- `MAX_ENTRY_GAP_UP_PCT`: maximum next-open gap allowed in research validation.
- `MAX_ENTRY_GAP_DOWN_PCT`: maximum next-open down gap allowed in research validation.
- `LOCKED_LIMIT_GAP_PCT`: gap threshold for detecting likely locked limit-up entries.
- `MAX_ENTRY_INTRADAY_RANGE_PCT`: maximum entry-day range allowed in research validation.
- `RECOMMENDATION_MIN_SIGNAL_SCORE`: production recommendation score floor. The current default is `2`; the market-regime layer still raises the effective floor outside favorable markets.

See `DATA_SOURCES.md` for the current external data-source comparison and migration plan.
- `RECOMMENDATION_PROFILE_ID`: canonical production advice profile. The default is `primary_50_return_15_drawdown`; an empty value is only useful for isolated unit-test fixtures.
- `RECOMMENDATION_PROFILE_EVIDENCE_PATH`: JSON receipt containing `metrics`, `evidence`, and optional `evidence_receipt_id`. The default is `data/recommendation_profile_evidence.json`; missing or invalid content fails closed.
- `RECOMMENDATION_REQUIRED_SIGNAL_TAGS`: comma-separated fallback signal tags. The canonical production profile binds `breadth_advancing_gte_50,breakout_20d` and cannot be weakened by this setting.
- `RECOMMENDATION_ALLOWED_MARKET_LEVELS`: comma-separated market regimes allowed for production recommendations. The current default is `favorable,neutral`.
- `RECOMMENDATION_SYMBOL_COOLDOWN_DAYS`: skip stocks recommended in the recent holding window. The default is `5`, matching the 5-trading-day hard-stop research profile.
- `RECOMMENDATION_LOCK_PATH`: lock file used to prevent overlapping recommendation scans.
- `ENABLE_NEWS_CONTEXT`: enable the headline risk overlay.
- `ENABLE_ANNOUNCEMENT_CONTEXT`: enable the CNINFO announcement event overlay.
- `ENABLE_FUND_FLOW_CONTEXT`: enable the fund-flow overlay.
- `ENABLE_MARGIN_ELIGIBILITY_CONTEXT`: enable official SSE/SZSE margin eligibility tags. Default is enabled.
- `MARGIN_ELIGIBILITY_CACHE_PATH`: cache file for official margin eligibility data.
- `NEWS_LOOKBACK_DAYS`: headline lookback window for risk scoring.
- `ANNOUNCEMENT_LOOKBACK_DAYS`: CNINFO announcement lookback window for event scoring.
- `MARKET_DATA_TIMEOUT_SECONDS`: AKShare A-share daily-history request timeout. The default is `8` seconds; lower it for broad research sweeps so one slow upstream request does not stall the run.

The dashboard's recommendation-performance panel uses saved recommendation history and evaluates from the next trading bar's open. Each recommendation also carries a `strategy_exit` (profit-lock + pre-calendar-gap + hold-days fallback) that reuses the research exit pipeline, so its single-trade outcome is comparable to the backtest target's exit semantics (the portfolio capital-model layer — slot-daily / exposure / correlation — is out of scope for per-trade tracking). Early after deployment, this sample may be empty or too small; treat it as live validation rather than a promised future win rate.

## Tests

```bash
pytest
```

## Research backtest

Use the repeatable research command before changing production thresholds:

```bash
python -m app.jobs research-backtest \
  --start-date 2024-07-05 \
  --max-deep 80 \
  --top-n 10 \
  --hold-days 10 \
  --lookback-days 620
```

More selective experiments can use:

```bash
MIN_BACKTEST_WIN_RATE=60 MIN_BACKTEST_AVG_RETURN=1 MAX_BACKTEST_AVG_ADVERSE=4 \
python -m app.jobs research-backtest --max-deep 80 --top-n 5 --buy-only --min-score 4.5
```

The research cache is stored under `data/research_cache/` and is ignored by git.

Signal-tag experiments can be run without changing production settings:

```bash
python -m app.jobs research-backtest \
  --start-date 2024-07-05 \
  --max-deep 80 \
  --top-n 10 \
  --hold-days 10 \
  --lookback-days 620 \
  --require-signal-tag score_gte_5,balanced_rsi \
  --require-all-signal-tags \
  --require-market-level favorable,neutral,cautious \
  --symbol-cooldown-days 10 \
  --max-active-positions 10
```

Use `--include-qualified-trades` to export the qualified trade set for faster offline factor sweeps.

Strict historical-universe research rebuilds the prefilter candidate list for each historical signal date, instead of replaying today's Top 80 list through the past:

```bash
python -m app.jobs research-historical-universe \
  --start-date 2024-07-05 \
  --max-universe-symbols 120 \
  --max-deep 80 \
  --top-n 10 \
  --hold-days 10 \
  --lookback-days 620 \
  --require-market-level favorable \
  --require-signal-tag candidate_change_0_to_3,moderate_20d_momentum,volume_confirmed \
  --require-all-signal-tags \
  --symbol-cooldown-days 10 \
  --max-active-positions 10
```

This mode still seeds symbols from the current A-share snapshot for practicality, so it can retain survivorship/current-liquidity bias. Its main improvement is that liquidity, price,涨跌幅, and candidate rank are reconstructed from each signal date's historical bar.

The legacy JSON builder can still produce a content-hashed development universe from dated security
master, exchange-calendar, daily-universe, and source-manifest files. It is intentionally accepted only
through `--pit-universe-path`; changing its filename or suffix never upgrades it to an audited source:

```bash
python -m app.jobs research-build-pit-universe \
  --security-master-path data/pit_inputs/stock_basic.json \
  --trade-calendar-path data/pit_inputs/trade_cal.json \
  --daily-universe-path data/pit_inputs/bak_basic_by_session.json \
  --source-manifest-path data/pit_inputs/source_manifest.json \
  --start-date 2016-01-01 \
  --end-date 2025-12-31

python -m app.jobs research-historical-universe \
  --start-date 2016-01-01 \
  --pit-universe-path data/research_artifacts/pit_universe/<universe_sha256>.json \
  --max-universe-symbols 0 \
  --max-deep 80 \
  --top-n 10 \
  --hold-days 10 \
  --lookback-days 3200
```

Legacy artifact mode forbids live/current-snapshot fallback and current-liquidity pre-truncation. Candidate
names, ST/name exclusions, and market breadth all use the same supplied signal-date membership. This
is an integrity-preserving development input, not yet trusted PIT: the current builder does not prove
that every daily source response was complete or that normalized rows were derived from the archived
raw responses. It remains `final_oos_eligible=false` until those lineage checks, frozen raw execution
bars, causal corporate-action handling, historical ST/suspension intervals, and the full producer
bundle are implemented.

Native Tushare responses can now be ingested one bounded request at a time into an append-only,
resumable SQLite receipt store. This avoids a multi-gigabyte all-years JSON and preserves the exact
pre-DataFrame response bytes:

```bash
python -m app.jobs research-pit-ingest-response \
  --store-dir data/research_receipts/pit-universe \
  --dataset bak_basic \
  --partition-key 2024-01-02 \
  --endpoint bak_basic \
  --params-json '{"trade_date":"20240102"}' \
  --raw-response-path data/raw_responses/bak_basic-20240102.json \
  --http-status 200 \
  --retrieved-at 2024-01-02T16:00:00+08:00 \
  --row-cap 7000

python -m app.jobs research-pit-audit-store \
  --store-dir data/research_receipts/pit-universe \
  --start-date 2024-01-02 \
  --end-date 2024-01-31 \
  --calendar-exchanges SSE,SZSE
```

For controlled collection, keep the token out of command-line arguments and files. The collector
first freezes the two exchange calendars, then stages the exact eight L/D/P/G master requests into
one immutable stock-master generation, atomically publishes its verified head, and only then derives
daily requests from the common SSE/SZSE open sessions. It records every transport failure, retryable
HTTP body, API error, invalid response, successful candidate, reuse, and immutable conflict before
receipt promotion or generation staging:

```bash
export TUSHARE_TOKEN='<token-in-process-environment-only>'

python -m app.jobs research-pit-fetch-tushare \
  --store-dir data/research_receipts/pit-universe \
  --start-date 2016-01-01 \
  --end-date 2025-12-31 \
  --api-url http://api.tushare.pro \
  --allow-insecure-official-http \
  --max-attempts 3 \
  --timeout-seconds 30
```

The same audited historical PIT collection path can use the pinned Jiaoch
Tushare-compatible source. Its credential is separate from the official provider token,
and this profile fixes both HTTPS and the allowed host instead of accepting a URL override:

```bash
export JIAOCH_TOKEN='<token-in-process-environment-only>'

python -m app.jobs research-pit-fetch-tushare \
  --source-profile jiaoch \
  --store-dir data/research_receipts/pit-universe \
  --start-date 2016-01-01 \
  --end-date 2025-12-31 \
  --max-attempts 3 \
  --timeout-seconds 30
```

For PIT collection this profile only changes the historical evidence source. It does not replace
the daily recommendation market-data provider, alter strategy parameters, or qualify results as
OOS evidence. Announcement context separately attempts Jiaoch `anns_d` first and falls back to the
official CNINFO disclosure lookup on a stable, token-free failure code. The current credential's
`anns_d` permission probe is denied, so that fallback is expected until the source grants access.
The recommendation card exposes the actual market-data source, announcement source, and fallback
state; it never labels AKShare/SQLite daily bars as Jiaoch.
The compatible service uses a pinned `/{api_name}` POST path and may return the exact
success message `"success"` with business code zero; other non-empty success messages remain
fail-closed. A collection is not publishable until its stock-master identifiers and row-cap
semantics also pass the existing coverage audit.

The official REST documentation currently names the plain-HTTP endpoint, so this project refuses it
unless `--allow-insecure-official-http` is explicit. HTTPS may be supplied through `--api-url` when a
verified endpoint is available. The built-in clock gate fails closed unless the host reports network
time synchronization with measured evidence; on macOS, merely enabling Network Time is insufficient
without an SNTP offset within one second. The attestation is refreshed after a five-minute TTL. A
successful report deep-verifies every calendar/daily receipt plus the active stock generation,
including token-free request semantics, exact response entity bytes, all eight staged events, the
generation manifest, normalized-row hash, exact attempt/event lineage hash, and atomic head.
`--no-resume` forces a fresh generation and receipt refetch; a completed normal rerun can skip the
network only after re-verifying the existing receipt and generation lineage.

The controlled boundary also rejects narrowed or otherwise non-canonical wire parameters before
network access, treats a short `Content-Length` entity as incomplete, and discards any response body
that reflects the in-memory token before raw CAS publication. If a crash occurs after a matching
receipt commits but before its promotion event commits, resume and artifact publication repair that
event in the same local SQLite transaction without refetching the network.

The eight `stock_basic` shards are not promoted into legacy receipt keys. They are bound to one
generation ID; fresh partial generations resume, stale partial generations are abandoned without
deleting evidence, and a head becomes visible only after all eight shards pass the one-hour batch,
raw, normalization, request, and event checks. Coverage audit rejects stores that contain only the
old stock receipts and no verified active generation.

The store rejects non-2xx/API-error responses, malformed native fields, duplicate keys, cap hits,
partition rewrites, missing calendar dates, missing daily receipts, and daily symbol sets that do not
match the frozen listing/delisting lifecycles. It re-parses every raw shard and re-hashes the SQLite
rows during audit. After a successful audit, publish the exact externally anchored coverage snapshot:

```bash
AUDIT_SHA=<coverage_audit_sha256-from-the-previous-command>

python -m app.jobs research-pit-publish-universe \
  --store-dir data/research_receipts/pit-universe \
  --start-date 2024-01-02 \
  --end-date 2024-01-31 \
  --expected-coverage-audit-sha256 "$AUDIT_SHA" \
  --output-dir data/research_artifacts/audited_pit_universe

python -m app.jobs research-historical-universe \
  --start-date 2024-01-02 \
  --audited-pit-universe-path \
    data/research_artifacts/audited_pit_universe/<bundle_sha256>/metadata.sqlite3 \
  --expected-coverage-audit-sha256 "$AUDIT_SHA" \
  --max-universe-symbols 0 \
  --max-deep 80 \
  --top-n 10 \
  --hold-days 10 \
  --lookback-days 3200
```

The audited bundle is a pruned, read-only receipt-store snapshot. It copies the selected calendar and
daily receipts plus the exact active generation, its head, eight shards, referenced attempts/events,
raw CAS objects, normalized generation rows, and a foreign-key-bound consumer master projection.
Calendar rows outside the requested coverage and out-of-scope daily rows remain when needed to replay
a selected raw response. The loader requires the audit hash supplied out of band, replays every raw
and normalized proof, recomputes the generation lineage, and proves that the consumer master is an
exact projection before applying the frozen SSE/SZSE and 主板/创业板/科创板 policy. Publication uses
staging, fsync, semantic and physical hashes, and an atomic directory rename; verification remains
self-contained after the source store is removed.

This still remains `final_oos_eligible=false`: no live-token collection has yet proved the controlled
transport against real multi-year source responses or a real delisting boundary, no independent
exchange security master is bound, and frozen raw execution bars, corporate actions, historical
pause/suspension and price-limit intervals, and qualified-trade lineage are still required.

If the three frozen market-component JSON files and the legacy PIT JSON artifact are already present,
the following command can assemble a content-addressed v2 evidence bundle after re-opening the
audited SQLite artifact. Its output is deliberately `development_integrity_only`; it does not write
or claim a strict `research_data_contract`, because the current component schema still reports
unresolved lineage/semantic reasons:

```bash
python -m app.jobs research-build-evidence-bundle \
  --pit-universe-path data/research_artifacts/pit_universe/<universe_sha256>.json \
  --market-data-manifest-path data/research_artifacts/market/market-<sha256>.json \
  --audited-pit-universe-path data/research_artifacts/audited_pit_universe/<bundle_sha256>/metadata.sqlite3 \
  --expected-coverage-audit-sha256 "$AUDIT_SHA" \
  --expected-artifact-root-sha256 "$ARTIFACT_ROOT_SHA" \
  --expected-temporal-contract-sha256 "$TEMPORAL_CONTRACT_SHA" \
  --expected-temporal-role development \
  --output-dir data/research_artifacts
```

`research-validate-file` rejects this integrity-only bundle until qualified-trade lineage,
producer-code binding, and all market semantics are independently verified.

For an audited artifact-native historical run, the execution lineage can be materialized separately:

```bash
python -m app.jobs research-build-artifact-native-evidence \
  --qualified-trades-path data/research_artifacts/qualified_trades/auto031.json \
  --audited-pit-universe-path data/research_artifacts/audited_pit_universe/<bundle_sha256>/metadata.sqlite3 \
  --expected-coverage-audit-sha256 "$AUDIT_SHA" \
  --expected-artifact-root-sha256 "$ARTIFACT_ROOT_SHA" \
  --expected-temporal-contract-sha256 "$TEMPORAL_CONTRACT_SHA" \
  --expected-temporal-role development \
  --output-dir data/research_artifacts/native_evidence
```

This re-queries the audited artifact for each trade's membership, causal signal frame, next-open
entry/exit, raw price, and generation proof. Newly generated qualified trades also carry a
`strategy_signal` snapshot; the command replays that snapshot against the exact
`evaluate_signal` source hash. It also replays the first-fillable sell search, return, MAE/MFE,
and mark-to-market path for trades that carry the complete outcome fields. Legacy qualified files
without those claims remain explicitly blocked. Producer-code lineage and corporate-action
receipts are still separate blockers, so this command never turns development evidence into a
production recommendation receipt.

Automated factor-combination sweeps can be run as a first pass before promoting any signal tag into production:

```bash
python -m app.jobs research-sweep \
  --start-date 2024-07-05 \
  --max-deep 80 \
  --top-n 10 \
  --hold-days 10 \
  --lookback-days 620 \
  --symbol-cooldown-days 10 \
  --max-active-positions 10 \
  --min-trades 20 \
  --max-filter-size 3 \
  --compact \
  --output-limit 12
```

The sweep now uses a stricter development target: 50% net latest rolling-one-year return after configured costs/slippage, no more than 15% portfolio drawdown, and an observed win-rate floor of 52%. Promotion review must also inspect payoff ratio, Profit Factor (target at least 1.3), Calmar (at least 1.5), and rolling-window stability. A development pass is not live-readiness proof.

Strict historical-universe sweeps combine the two steps: rebuild historical candidates, collect qualified trades, then sweep signal tags, candidate-rank tags, amount tags, and market regimes:

```bash
python -m app.jobs research-historical-sweep \
  --start-date 2024-07-05 \
  --max-universe-symbols 300 \
  --max-deep 80 \
  --top-n 10 \
  --hold-days 10 \
  --lookback-days 620 \
  --symbol-cooldown-days 10 \
  --max-active-positions 10 \
  --min-trades 20 \
  --max-filter-size 3 \
  --compact \
  --output-limit 12
```

Use `--qualified-trades-output` to save the expensive historical qualified-trade set, then re-run tag/capital sweeps from the saved file without fetching or rebuilding historical signals again:

```bash
python -m app.jobs research-historical-sweep \
  --start-date 2024-07-06 \
  --max-universe-symbols 300 \
  --max-deep 80 \
  --top-n 3 \
  --hold-days 5 \
  --stop-loss-pct 5 \
  --symbol-cooldown-days 5 \
  --max-active-positions 3 \
  --qualified-trades-output data/research_cache/qualified_hold5_stop5.json \
  --compact

python -m app.jobs research-sweep-file \
  --qualified-trades-path data/research_cache/qualified_hold5_stop5_ohlc.json \
  --hold-days 5 \
  --top-n 3 \
  --symbol-cooldown-days 5 \
  --max-active-positions 3 \
  --capital-model slot-daily \
  --exposure-multiplier 2.08 \
  --annual-financing-rate-pct 8 \
  --roundtrip-cost-bps 25 \
  --slippage-bps 10 \
  --required-signal-tags breadth_advancing_gte_50,breakout_20d,price_gap_up_2_to_5 \
  --excluded-signal-tags entry_gap_lt_neg1 \
  --market-levels favorable,neutral \
  --pre-exit-calendar-gap-days 7 \
  --prior-high-trailing-stop-pct 10 \
  --prior-high-trailing-activation-pct 0 \
  --partial-profit-activation-pct 18 \
  --partial-profit-fraction 1 \
  --correlation-threshold 0.35 \
  --correlation-lookback-days 30 \
  --correlation-min-periods 15 \
  --compact
```

`research-sweep*` remains an exploratory interface. Promotion evidence must use the frozen-spec
development-validation gate, which writes a locked and hash-verified
`registered -> completed|failed|aborted` experiment ledger, purges outcomes crossing fold
boundaries, applies an embargo, reports the Wilson 95% win-rate interval, and rejects any input file
that already contains rows from the declared final OOS period:

```bash
python -m app.jobs research-validate-file \
  --qualified-trades-path data/research_cache/qualified_point_in_time.json \
  --experiment-id exp-YYYYMMDD-001 \
  --hypothesis "A single frozen hypothesis" \
  --expected-mechanism "Why the signal should persist" \
  --falsification-criterion "Wilson lower bound is below 70%" \
  --exit-criterion "Reject when any primary gate fails" \
  --final-oos-start YYYY-MM-DD \
  --required-signal-tags tag_a,tag_b \
  --market-levels favorable,neutral
```

The command intentionally rejects legacy qualified-trade artifacts unless their summary carries a
versioned point-in-time contract, an empty known-bias list, explicit final-validation eligibility,
`entry_decision_cutoff=next_open`, and (for an audited development artifact) a
`qualified_trade_lineage_sha256` that was recomputed from fresh artifact entry/exit proofs. Hash-looking
strings are not evidence: the contract must point to a relative, content-addressed evidence bundle,
and validation recursively rehashes the exact PIT universe, raw vendor universe inputs, frozen trading
calendar, raw execution bars, corporate actions, causal signal bars, and market source manifest. For
audited artifact-native trades it also re-queries the read-only artifact for membership, causal signal
rows, next-open buy/sell fillability, raw prices, and generation proofs; editing the trade JSON and
recomputing its outer hash cannot pass. Current normal and PIT historical-universe runs still declare
`eligible_for_final_validation=false` until all of those real components exist for the requested
period. Existing 2024-2026 artifacts have already been used for parameter exploration, so they remain
development diagnostics rather than a new final OOS set.

Holding-period comparisons can be run in one command:

```bash
python -m app.jobs research-historical-hold-sweep \
  --start-date 2024-07-05 \
  --max-universe-symbols 300 \
  --max-deep 80 \
  --top-n 10 \
  --hold-days-list 4,5,6,7,8 \
  --lookback-days 620 \
  --stop-loss-pct 5 \
  --max-active-positions 10 \
  --min-trades 20 \
  --max-filter-size 3 \
  --exposure-sweep \
  --max-exposure-multiplier 2 \
  --annual-financing-rate-pct 8 \
  --roundtrip-cost-bps 25 \
  --slippage-bps 10 \
  --compact \
  --output-limit 5
```

The sweep supports three capital models:

- `signal-day`: legacy conservative basket model. It spreads each signal-day basket return over `hold_days` and compounds on signal dates.
- `slot-exit`: active-slot model. It allocates exposure across `max_active_positions`, books each selected trade on its exit date, and charges financing plus execution costs per slot. This is closer to the current 3-slot research profile, but still does not mark positions to market intraday.
- `slot-daily`: stricter active-slot model. It allocates exposure across active slots like `slot-exit`, but marks each open trade to market every trading day and uses each day's low price for drawdown checks.

Historical qualified trades now store per-day `open_return_pct`, `high_return_pct`, `close_return_pct`, and `low_return_pct` in `mark_to_market_path`. This makes later research able to model next-open de-risking and partial profit-taking without assuming same-bar execution.

Use `--required-signal-tags`, `--excluded-signal-tags`, and `--market-levels` on `research-sweep-file` to re-check a specific production slice without enumerating the full tag grid. Use `--exposure-multiplier` when the goal is to audit an exact leverage assumption instead of running the gated exposure sweep. Use `--pre-exit-calendar-gap-days` to model exiting on the last trading day before a long exchange closure instead of carrying the position across the closure. Use `--prior-high-trailing-stop-pct` and `--prior-high-trailing-activation-pct` to model a protective sell level based only on the prior completed bar's high watermark. Use `--partial-profit-activation-pct` and `--partial-profit-fraction` to model selling a fraction, or the full position at `1.0`, at the next open after the prior completed bar has reached a profit threshold. Use `--correlation-threshold`, `--correlation-lookback-days`, and `--correlation-min-periods` to cap new entries whose pre-signal close-to-close return correlation with active positions is too high.

The historical sweep path computes only the latest 365-day rolling window during large tag scans. Full rolling best/worst windows remain in the standalone backtest summaries, but large sweeps avoid recomputing unused windows for every candidate filter.

The sweep currently derives extra tags from the historical prefilter context: `candidate_rank_lte_20`, `candidate_rank_lte_40`, `amount_gte_1b`, `amount_gte_300m`, `amount_gte_100m`, `candidate_change_0_to_3`, `candidate_change_3_to_6`, `candidate_change_negative`, `prefilter_score_gte_12`, and prior-quality tags such as `prior_win_gte_60`.

The research backtest also attaches relative-strength tags against the two market proxy ETFs, HS300 ETF `510300` and ChiNext ETF `159915`. Examples include `rs20_nonnegative`, `rs20_strong`, `rs60_nonnegative`, and `rs60_strong`. These tags compare a stock's 20/60-day return with the average 20/60-day return of the proxy ETFs as of the signal date.

The same proxy context now emits broad-market `proxy_*` tags, such as `proxy20_avg_gte_10`, `proxy60_avg_lt_0`, `proxy_market_bullish`, and `proxy_market_fragile`. These are as-of market-state filters derived from cached proxy ETF bars, not future benchmark outcomes. They are anchored to official index-provider references for broad A-share and growth-market benchmarks: SSE links to China Securities Index equity-index methodology, and CNI Index publishes Shenzhen/ChiNext index information and methodology/news pages.

Strict historical-universe research now also attaches signal-day market breadth tags computed only from same-day cross-sectional history in the fetched universe. Examples include `breadth_ma20_gte_60`, `breadth_ret20_pos_gte_70`, `breadth_advancing_gte_50`, `breadth_median_ret20_gte_5`, and `breadth_liquid_300m_gte_50`. The live recommendation path now computes current deep-scan `breadth_*` tags and proxy ETF `rs*` relative-strength tags before strict filtering. Keep the scope caveat visible: live breadth is calculated from the current recommendation deep-scan candidates, while the strict research breadth below used the 300-symbol fetched seed.

It also attaches signal-day price-action and liquidity-position tags: `amount_rank_pct_top_10`, `candidate_rank_pct_top_25`, `price_close_near_high`, `price_gap_up_0_to_2`, `price_gap_up_2_to_5`, `price_range_4_to_8`, and `recent_large_up_20d`. These features only use signal-day or earlier bars. The live recommendation path now computes the same latest-bar price-action tag family before strict filtering. The limit-up approximation uses 20% for ChiNext/STAR-style codes and 10% for other non-ST A shares; before production promotion, replace this approximation with official exchange limit-price data.

When announcement context is enabled, CNINFO disclosure context is now converted into `announcement_*` tags for both live strict filtering and cached research sweeps. Examples include `announcement_level_watch_risk`, `announcement_blocked`, `announcement_negative_gte_2`, `announcement_score_lte_neg14`, and event tags such as `announcement_event_regulatory_penalty`, `announcement_event_litigation_freeze`, `announcement_event_pledge_or_reduction`, and `announcement_event_buyback_or_increase`. These tags are derived from announcements published on or before the signal date, so they can be used as no-lookahead filters when the qualified-trades cache was generated with `--announcement-context`.

The sweep also derives holding-path calendar-gap tags from `mark_to_market_path`: `holding_calendar_gap_lte_4`, `holding_calendar_gap_lte_6`, `holding_calendar_gap_gte_5`, `holding_calendar_gap_gte_7`, and `holding_calendar_gap_gte_10`. These tags help isolate long exchange-closure risk around holiday windows. Official exchange schedules confirm, for example, that the 2024 National Day closure ran from 2024-10-01 through 2024-10-07 and reopened on 2024-10-08; 2025 National Day/Mid-Autumn closed from 2025-10-01 through 2025-10-08 and reopened on 2025-10-09; 2026 National Day closes from 2026-10-01 through 2026-10-07 and reopens on 2026-10-08.

The optional correlation budget reads cached historical adjusted daily bars from `data/research_cache/a_<symbol>_<lookback>_qfq.json` and uses only returns dated on or before the signal date. This is intentionally a price-behavior concentration control, not a current-industry-membership proxy, so it avoids lookahead from today-only sector labels. Large sweeps now reuse both per-symbol return histories and per-date pair-correlation values across filter specs, so correlation-enabled research remains practical without changing the selected-trade semantics.

The sweep also attaches entry-executability tags from the next entry bar, such as `entry_gap_lt_neg1`, `entry_gap_gte_neg1`, `entry_gap_neg1_to_2`, `entry_range_lt_6`, and `entry_range_gte_6`. Treat these as execution-time conditional-entry guards, not live pre-entry signals: they are only known after the entry open print.

The prior-high trailing stop and profit-lock exit use only high prices from completed prior bars. If the next bar opens through the trailing stop, the model exits at that open return; otherwise it exits at the precomputed stop level when the day's low crosses it. For profit lock, once the prior completed high reaches the configured threshold, the modeled exit is the next open. These are research and alerting models, not guaranteed exchange-native stop orders. Exchange rule pages document auction trading, limit/market order handling, order size/tick rules, and price-limit mechanics; broker-side conditional orders still need separate execution validation.

Optional industry-rotation research can be enabled with `--industry-rotation-context`. It aggregates AKShare industry-board history by signal date into `industry_*` tags without assigning stocks to current board constituents, avoiding historical membership lookahead. The data layer uses AKShare's `stock_board_industry_name_em`, `stock_board_industry_cons_em` and `stock_board_industry_hist_em`, passing the same `BK` board code through the whole chain.

Optional margin-eligibility research can be enabled with `--margin-eligibility-context` on `research-backtest`, `research-sweep`, `research-historical-universe`, `research-historical-sweep`, and `research-historical-hold-sweep`. In quick `research-backtest` / `research-sweep`, `margin_*` tags use the current official SSE/SZSE lists and are useful for exploratory slicing only. In strict historical-universe research, `margin_*` tags currently use SZSE underlying reports fetched as-of the signal date and skip non-SZSE symbols; the summary includes a caveat because SSE historical eligibility still needs a reliable as-of source before it can be used as a no-lookahead filter.

Optional dragon-tiger research can be enabled with `--dragon-tiger-context` on strict historical-universe commands. It attaches `lhb_*` tags from same-day list fields such as net buy amount, net-buy ratio, turnover ratio, institution buy/sell text, and public-list reasons. It intentionally excludes AKShare's `上榜后1日` / `上榜后2日` / `上榜后5日` / `上榜后10日` fields because they are future outcomes from the signal date perspective.

Current reliable-source stance:

- AKShare remains the practical no-account adapter for A-share行情, exchange summaries, and CNINFO disclosure lookups.
- Official SSE/SZSE lists are now the primary source for current margin eligibility in live recommendations. Historical no-lookahead margin tags are partially integrated for SZSE underlying securities only; SSE historical margin status remains a source-hardening item.
- Exchange and CNINFO/深证信 sources are preferred for announcements, market summaries, financing/margin, trading calendars, and official event data. CNINFO's own home page exposes announcements across 深市/沪市/北交所 and states that it is a statutory information disclosure platform operated by Shenzhen Securities Information Co.; CSRC's disclosure-site notice identifies CNINFO as a designated ChiNext disclosure site.
- SSE/SZSE trading-rule pages are the anchor for execution assumptions such as auction trading, limit/market orders, lot size, tick size, price limits, and valid order prices. Protective stops in this project are alerting or broker-side conditional execution models; their actual fill behavior must be verified separately.
- SSE/CSI and CNI Index pages are the reference anchors for broad-market proxy regime research. The implementation still uses practical ETF bars from AKShare for historical no-account backtests, so `proxy_*` tags should be treated as benchmark-state approximations.
- Price-limit and trading-rule checks should be anchored to exchange/CSRC rules, such as SSE trading rules for 10% main-board limits, SSE STAR rules for 20%, SZSE ChiNext special rules for 20%, and CSRC/BSE disclosures for 30% BSE limits.
- Portfolio-exposure research must not be confused with ordinary A-share cash trading. SSE's current margin notice says financing margin ratio must not be below 100%, and CSRC rules let brokers dynamically control margin ratio, eligible securities, collateral haircuts, maintenance guarantees, and concentration limits. The 5x/6x exposure experiments below are mathematical stress tests, not a deployable retail margin plan.
- Tushare Pro is a candidate paid backup for daily bars, daily basic, margin/pledge, dragon-tiger, and news/event datasets, but it requires token and permission management before production use.
- Media/news feeds should remain risk filters or manual review inputs until their latency, duplication, and false-positive rate are measured in backtests.
- Dragon-tiger data is useful for audit/explanation, but the latest strict sweep did not promote `lhb_*` into the best return slice.

Industry-board history source checks can be run separately before promoting an industry factor:

```bash
python -m app.jobs industry-history-check \
  --start-date 2025-01-01 \
  --end-date 2025-04-01 \
  --max-boards 3
```

This command validates the AKShare industry-board list and historical board行情 interface, normalizes sample histories, and reports an as-of trend score. It intentionally records the membership caveat: current board constituents are not a historical constituent map.

Official margin eligibility source checks can be run separately:

```bash
python -m app.jobs margin-eligibility-check --as-of 2026-07-05
```

The live recommendation path now attaches `margin_financing_underlying`, `margin_financing_eligible`, `margin_short_underlying`, `margin_short_eligible`, `margin_collateral_eligible`, `margin_exchange_sse`, `margin_exchange_szse`, and SZSE price-limit tags where available. `margin_financing_eligible` is intentionally stricter than `margin_financing_underlying`: when SZSE reports `当日可融资 = N`, the candidate will not get the eligible tag even if it remains in the broader underlying pool.

Use `--stop-loss-pct 5` to model a hard 5% single-trade stop, `--take-profit-pct 12` to model a fixed take-profit exit, and `--trailing-stop-pct 6` to model a conservative trailing stop from already-observed highs. These options are available on both backtest and sweep commands. Same-day stop-loss and take-profit hits are handled conservatively as stop-loss first because daily bars do not reveal intraday ordering. Early exits now compute adverse movement only through the actual exit date, not through the original planned holding window.

Portfolio-control experiments can use:

```bash
python -m app.jobs research-backtest \
  --max-deep 80 \
  --top-n 10 \
  --stop-loss-pct 5 \
  --symbol-cooldown-days 10 \
  --max-active-positions 10
```

The research output includes `portfolio_max_drawdown_pct`, `trade_payoff_ratio`, `trade_profit_factor`, and `portfolio_calmar_latest_1y`. Current acceptance aims for roughly 50% net rolling-one-year return with drawdown no worse than 15%; cached development experiments remain hypothesis-screening evidence only.

Announcement-aware research can be enabled without leaking future events:

```bash
python -m app.jobs research-backtest \
  --start-date 2024-07-05 \
  --max-deep 80 \
  --top-n 10 \
  --hold-days 10 \
  --lookback-days 620 \
  --announcement-context \
  --announcement-lookback-days 30
```

The announcement context is evaluated as of each signal date. Recent experiments showed that CNINFO disclosure risk filtering is useful for auditability, but did not improve the 80/10 two-year baseline by itself, so positive announcements remain non-alpha context for now.

Event-filter research can require or exclude classified CNINFO disclosure events:

```bash
python -m app.jobs research-backtest \
  --start-date 2024-07-05 \
  --max-deep 80 \
  --top-n 10 \
  --hold-days 10 \
  --lookback-days 620 \
  --announcement-context \
  --announcement-lookback-days 30 \
  --require-announcement-event buyback_or_increase,financing_or_guarantee \
  --require-all-announcement-events
```

Latest two-year research snapshot, measured from next trading day's open and held for 10 trading days:

| Experiment | Trades | Win rate | Avg return | Portfolio return | Max drawdown |
| --- | ---: | ---: | ---: | ---: | ---: |
| Baseline 80 x 10 | 398 | 53.52% | 2.23% | 68.30% | -4.91% |
| CNINFO 30-day risk overlay | 358 | 51.96% | 1.67% | 49.71% | -5.96% |
| Require buyback/increase event | 88 | 62.50% | 2.62% | 18.61% | -3.12% |
| Buyback/increase, excluding incentive and dividend events | 55 | 67.27% | 2.77% | 16.07% | -2.63% |
| Require buyback/increase + financing/guarantee | 22 | 72.73% | 3.05% | 6.40% | -1.31% |
| Require buyback/increase + pledge/reduction | 11 | 72.73% | 6.39% | 7.19% | -0.43% |
| Strong signal gate: score >= 5, balanced RSI, non-defensive market | 34 | 70.59% | 7.82% | 26.47% | -0.70% |
| Breakout near high, favorable market, prior quality filter | 30 | 80.00% | 4.68% | 11.08% | -0.54% |

This older snapshot used today's Top 80 prefiltered names and is retained only as a benchmark. After adding daily historical prefilter reconstruction, the production gate was tightened to the stricter historical-universe slice below.

The research summary also reports `rolling_1y`, a 365-day rolling portfolio window. For the strict signal gate above, the latest one-year window is 2025-06-06 to 2026-06-02 with 22 trades, 22.06% return, and -0.46% max drawdown. Under the old 200% one-year return target, this was the clearest gap.

Deep-scan breadth test:

| Deep scan | Trades | Win rate | Avg return | Portfolio return | Max drawdown |
| --- | ---: | ---: | ---: | ---: | ---: |
| Top 80 prefiltered stocks | 34 | 70.59% | 7.82% | 26.47% | -0.70% |
| Top 150 prefiltered stocks | 67 | 55.22% | 3.98% | 25.11% | -3.39% |
| Top 300 prefiltered stocks | 129 | 48.84% | 2.05% | 21.44% | -6.59% |

The old global-rank-only Top 300 split shows why blind expansion is risky: rank 1-80 kept a 72.73% win rate, while rank 81-150 fell to 37.50% and rank 151-300 to 42.19%. Production now uses an industry-stratified pool instead of a pure global rank, with `SCAN_MAX_DEEP=500` as the VPS execution cap until caching, timeout, and staged generation are hardened.

Exit-rule test on the same strong-signal gate:

| Exit rule | Trades | Win rate | Avg return | Portfolio return | Max drawdown |
| --- | ---: | ---: | ---: | ---: | ---: |
| Hold 5 trading days | 68 | 63.24% | 2.87% | 41.75% | -4.78% |
| Hold 10 trading days | 34 | 70.59% | 7.82% | 26.47% | -0.70% |
| Hold 10 + 8% take-profit | 33 | 69.70% | 3.13% | 9.78% | -0.70% |
| Hold 10 + 12% take-profit | 33 | 69.70% | 4.16% | 13.06% | -0.70% |
| Hold 10 + 15% take-profit | 33 | 69.70% | 4.85% | 15.28% | -0.70% |

Fixed take-profit exits are available for research and live alerts, but they are not a production default because they reduced the two-year strong-signal return in this sample.

Relative-strength filter test on the strong-signal gate:

| Filter | Trades | Win rate | Avg return | Portfolio return | Latest 1Y return |
| --- | ---: | ---: | ---: | ---: | ---: |
| Base strong signal | 34 | 70.59% | 7.82% | 26.47% | 22.06% |
| `rs20_nonnegative` | 24 | 70.83% | 8.27% | 21.35% | 17.42% |
| `rs20_strong` | 8 | 62.50% | 9.33% | 7.65% | 4.63% |
| `rs60_nonnegative` | 22 | 77.27% | 8.97% | 18.46% | 13.64% |
| `rs60_strong` | 10 | 80.00% | 13.64% | 11.50% | 10.35% |
| `rs20_nonnegative + rs60_nonnegative` | 18 | 72.22% | 8.66% | 16.58% | 11.84% |

Relative-strength filters improved trade precision in some slices, but did not improve the one-year return target because they reduced trade count further.

Latest automated sweep snapshot, using 1,566 qualified trades from the Top 80 prefiltered universe and 1,880 tag/regime combinations:

| Sweep slice | Trades | Win rate | Avg return | Portfolio return | Max drawdown | Latest 1Y return | Full target pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `breakout_20d + high_volatility + rs60_strong`, favorable/neutral market | 34 | 70.59% | 8.19% | 25.05% | -2.00% | 26.67% | no |
| `balanced_rsi + score_gte_5`, non-defensive market | 34 | 70.59% | 7.82% | 26.47% | -0.70% | 22.06% | no |
| `breakout_20d + high_volatility + rs60_market_leader`, favorable/neutral market | 27 | 74.07% | 8.30% | 22.56% | -1.49% | 21.66% | no |
| `balanced_rsi + moderate_20d_momentum + score_gte_5`, non-defensive market | 26 | 73.08% | 9.04% | 26.02% | -0.58% | 21.45% | no |
| `moderate_20d_momentum + rs60_nonnegative + volume_confirmed`, favorable market | 50 | 70.00% | 4.84% | 19.69% | -1.02% | 17.40% | no |

This confirms that the current factor set can produce high-precision, low-drawdown slices, but none of the tested combinations approaches the 200% one-year return target. The best tested rolling-one-year return under the 70%/5% constraints is 26.67%, leaving a 173.33 percentage-point gap.

Legacy research caveat: `research-backtest` still starts from today's prefiltered Top 80 universe, then replays historical daily bars. That makes it useful for quick signal slicing but not a strict historical all-market simulation. Use `research-historical-universe` when candidate rank and daily liquidity need to be reconstructed as of each signal date.

Strict historical-universe snapshot, using the current-snapshot top 120 symbols as a fetch seed, rebuilding the daily Top 80 prefilter by historical price/amount/change, then holding signals for 10 trading days:

| Slice | Trades | Win rate | Avg return | Portfolio return | Max drawdown | Latest 1Y return | Full target pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| Old production gate: `score >= 5 + balanced_rsi`, non-defensive market | 25 | 60.00% | 5.05% | 13.35% | -1.62% | 13.31% | no |
| New production gate: `balanced_rsi + moderate_20d_momentum + volume_confirmed`, favorable market | 73 | 73.97% | 4.35% | 22.22% | -1.58% | 20.70% | no |
| Same as new gate, plus `score >= 5` | 2 | 50.00% | 1.43% | 0.29% | -0.13% | 0.29% | no |

The strict historical-universe result invalidates the earlier `score >= 5` production assumption for this sample. Production now favors the broader favorable-market volume-confirmed slice because it restores the 70% win-rate and 5% drawdown constraints under a less biased candidate-rank model. It still falls far short of the 200% one-year return target.

Expanded strict historical-universe sweep, using the current-snapshot top 300 symbols as the fetch seed:

| Check | Result |
| --- | --- |
| Fetched symbols | 296 / 300 |
| Historical candidate days | 472 |
| Qualified trades before sweep | 1,605 |
| Sweep combinations | 4,640 |
| 70% win-rate and 5% drawdown pass count, min 20 trades | 0 |
| 70% win-rate and 5% drawdown pass count, min 10 trades | 0 |
| Best win-rate slice | `moderate_20d_momentum + near_60d_high + volume_confirmed`, favorable market: 103 trades, 68.93% win rate, -2.49% max drawdown, 17.49% latest 1Y return |
| Best latest-1Y-return slice | `amount_gte_300m + candidate_rank_lte_40 + rs20_nonnegative`: 242 trades, 50.83% win rate, -10.85% max drawdown, 73.01% latest 1Y return |
| Previous production gate on this 300-symbol sample | 111 trades, 67.57% win rate, 25.15% portfolio return, -1.82% max drawdown, 23.45% latest 1Y return |

This larger sample did not validate the original tag set against the 70%/5%/200% target. It showed a useful split: broader rank/relative-strength slices could lift one-year return toward 70% but lost too often and drew down too much, while precision slices stayed below 70% win rate and far below the 200% return target.

After adding signal-day change and prior-quality tags, the same 300-symbol strict historical sweep covered 18,104 combinations. It found 28 combinations that pass 70% win rate and 5% drawdown, but still no combination reaches the 200% latest-one-year return target:

| Slice | Trades | Win rate | Avg return | Portfolio return | Max drawdown | Latest 1Y return | Full target pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `candidate_change_0_to_3 + moderate_20d_momentum + volume_confirmed`, favorable market | 101 | 70.30% | 4.52% | 30.10% | -1.72% | 27.37% | no |
| `candidate_change_3_to_6 + controlled_volatility + moderate_20d_momentum`, favorable market | 42 | 71.43% | 5.76% | 16.93% | -1.39% | 16.92% | no |
| `candidate_change_negative + moderate_20d_momentum + near_60d_high`, favorable market | 20 | 80.00% | 4.87% | 8.26% | -1.01% | 8.68% | no |

The production gate now uses the first row because it is the broadest 70%/5% passing slice in the 300-symbol strict historical sample. This is a precision improvement, not completion of the 200% return objective.

After adding signal-day market breadth tags and precomputing per-trade sweep tags for performance, the same strict 300-symbol sample covered 83,504 combinations. It found 670 combinations that pass 70% win rate and 5% drawdown, but still no combination reaches the 200% latest-one-year return target:

| Slice | Trades | Win rate | Avg return | Portfolio return | Max drawdown | Latest 1Y return | Full target pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `breadth_ma20_gte_60 + rs60_nonnegative + volume_confirmed`, favorable market | 74 | 71.62% | 6.13% | 37.08% | -0.68% | 33.73% | no |
| `breadth_ma20_gte_60 + candidate_rank_lte_40 + rs20_nonnegative`, favorable market | 87 | 71.26% | 6.00% | 38.84% | -0.75% | 31.49% | no |
| `breadth_ma20_gte_60 + candidate_change_3_to_6 + rs60_nonnegative`, favorable market | 44 | 75.00% | 7.05% | 27.88% | -1.34% | 27.39% | no |
| Historical production-gate candidate: `candidate_change_0_to_3 + moderate_20d_momentum + volume_confirmed`, favorable market | 101 | 70.30% | 4.52% | 30.10% | -1.72% | 27.37% | no |

Market breadth improved the best 70%/5% passing slice from 27.37% to 33.73% latest-one-year return. This is useful risk/quality filtration, but it does not close the return target gap; the next research direction needs return-expansion factors such as industry rotation breadth, limit-up continuation state, gap continuation, intraday volume percentile, and position sizing rather than only more precision filters.

After adding signal-day liquidity percentile, candidate-rank percentile, gap/range/close-position, and recent limit-up/large-up tags, the strict sample covered 281,504 combinations. It found 1,930 combinations that pass 70% win rate and 5% drawdown, but still no combination reaches the 200% latest-one-year return target:

| Slice | Trades | Win rate | Avg return | Portfolio return | Max drawdown | Latest 1Y return | Full target pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `breadth_ma20_gte_60 + rs60_nonnegative + volume_confirmed`, favorable market | 74 | 71.62% | 6.13% | 37.08% | -0.68% | 33.73% | no |
| `breadth_ma20_gte_60 + candidate_rank_lte_40 + rs20_nonnegative`, favorable market | 87 | 71.26% | 6.00% | 38.84% | -0.75% | 31.49% | no |
| `amount_gte_1b + breadth_ret20_pos_gte_70 + recent_large_up_20d`, favorable market | 38 | 71.05% | 9.06% | 33.53% | -1.36% | 30.05% | no |
| `breadth_ret20_pos_gte_70 + recent_large_up_20d`, favorable market | 43 | 72.09% | 8.88% | 33.10% | -1.36% | 29.17% | no |

The `recent_large_up_20d` factor increases average trade return, but not enough to beat the existing best 70%/5% slice. It remains a useful feature for the next industry-rotation and position-sizing experiments rather than a production gate by itself.

After adding exposure-multiplier research to the sweep, the strict 300-symbol sample can mathematically pass the three target thresholds, but only at 5x/6x exposure:

| Slice | Exposure | Trades | Win rate | Avg return | Portfolio return | Max drawdown | Latest 1Y return | Full target pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `breadth_ma20_gte_60 + rs60_nonnegative + volume_confirmed`, favorable market | 6.0x | 74 | 71.62% | 6.13% | 489.04% | -4.04% | 410.22% | yes |
| `breadth_ma20_gte_60 + rs60_nonnegative + volume_confirmed`, favorable market | 5.0x | 74 | 71.62% | 6.13% | 346.33% | -3.37% | 295.68% | yes |
| `breadth_ret20_pos_gte_70 + near_60d_high + recent_large_up_20d`, favorable market | 6.0x | 32 | 71.88% | 8.88% | 314.50% | -2.74% | 267.82% | yes |

This is not production-complete. It proves the selected low-drawdown signal family has enough return density under high exposure, but ordinary A-share spot/margin execution, financing availability, interest, forced liquidation, concentration limits, transaction costs, and slippage are not yet modeled. Keep production recommendations at 1x until a realistic execution model is added.

After adding a simple execution-cost model, the realistic-margin pass still does not meet the 200% one-year target. The model below assumes max 2.0x exposure, 8% annual financing cost, 25 bps round-trip transaction cost, and 10 bps one-way slippage:

| Slice | Exposure | Financing | Costs | Trades | Win rate | Portfolio return | Max drawdown | Latest 1Y return | Full target pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `breadth_ma20_gte_60 + rs60_nonnegative + volume_confirmed`, favorable market | 2.0x | 8.0% | 25 bps + 10 bps/side | 74 | 71.62% | 77.33% | -1.95% | 70.69% | no |
| `breadth_ma20_gte_60 + candidate_rank_lte_40 + rs20_nonnegative`, favorable market | 2.0x | 8.0% | 25 bps + 10 bps/side | 87 | 71.26% | 81.36% | -1.65% | 65.33% | no |
| `amount_gte_1b + breadth_ret20_pos_gte_70 + recent_large_up_20d`, favorable market | 2.0x | 8.0% | 25 bps + 10 bps/side | 38 | 71.05% | 70.72% | -3.07% | 62.77% | no |

The realistic pass keeps win rate and drawdown inside target, but the best latest-one-year return falls to 70.69%. The next productive direction is therefore not higher generic leverage; it is a real execution model plus alpha that increases trade frequency or per-trade return under ordinary financing limits.

Shorter holding-period plus corrected hard-stop accounting improved the realistic-margin pass, but still did not reach the 200% one-year target. A 5-trading-day hold increased raw signal count, and a 5% hard stop materially improved the best compliant slice under the same 2.0x, 8% financing, 25 bps round-trip cost, and 10 bps one-way slippage assumptions:

| Exit / hold test | Exposure | Trades | Win rate | Portfolio return | Max drawdown | Latest 1Y return | Full target pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 5-day hold, no hard stop: `breadth_ma20_gte_60 + candidate_change_negative + recent_large_up_20d` | 2.0x | 42 | 73.81% | 72.97% | -3.87% | 70.67% | no |
| 5-day hold + 5% hard stop: `breadth_advancing_gte_50 + breakout_20d + price_gap_up_2_to_5`, favorable/neutral | 2.0x | 54 | 77.78% | 118.98% | -2.21% | 96.82% | no |
| 4-day hold + 5% hard stop: `price_gap_down + price_intraday_loss + recent_large_up_20d` | 2.0x | 20 | 75.00% | 69.08% | -2.88% | 52.71% | no |
| 6-day hold + 5% hard stop: `breadth_advancing_gte_50 + breakout_20d + price_gap_up_2_to_5`, favorable/neutral | 2.0x | 51 | 72.55% | 70.62% | -1.85% | 59.18% | no |
| 7-day hold + 5% hard stop: `candidate_change_negative + price_upper_shadow_gte_3 + rs60_nonnegative`, favorable/neutral | 2.0x | 25 | 76.00% | 55.24% | -3.15% | 52.34% | no |
| 8-day hold + 5% hard stop: `breadth_ret20_pos_gte_70 + price_range_4_to_8 + score_gte_5`, favorable/neutral | 2.0x | 20 | 75.00% | 58.55% | -3.05% | 60.80% | no |
| 3-day hold + 5% hard stop: `moderate_20d_momentum + price_gap_down + price_intraday_loss`, favorable | 2.0x | 24 | 70.83% | 35.61% | -1.57% | 33.49% | no |
| 10-day hold + 6% trailing stop: `breadth_ma20_gte_60 + controlled_volatility + rs60_market_leader`, favorable | 2.0x | 31 | 74.19% | 38.04% | -1.22% | 31.41% | no |

The 5-day hard-stop slice was the best realistic-cost improvement in this historical scan: it raises the latest-one-year result from 70.69% to 96.82% while keeping win rate and drawdown inside that scan's target. Adjacent 4/6/7/8-day scans did not beat it, and trailing stops were not helpful in this sample.

Capacity and breadth expansion did not improve that best slice:

| Expansion test | Exposure | Trades | Win rate | Portfolio return | Max drawdown | Latest 1Y return | Full target pass |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 5-day hard stop, Top80 candidates, `top_n=20`, `max_active_positions=20` | 2.0x | 54 | 77.78% | 118.98% | -2.21% | 96.82% | no |
| 5-day hard stop, 500-symbol seed, Top150 daily candidates: `breakout_20d + price_gap_up_2_to_5 + price_range_lt_4`, favorable | 2.0x | 30 | 70.00% | 44.77% | -2.55% | 38.17% | no |

The wider universe increased raw signals but diluted the best compliant return. This keeps the validated production/research default at Top80 until a stronger factor can separate deeper candidates.

After adding the slot-exit capital model and reducing the active slot budget to 3, the same 5-day hard-stop family produced the first strict sample that passes all three research targets under a 2.0x realistic-cost assumption:

| Capital model | Slice | Active slots | Exposure | Trades | Win rate | Portfolio return | Max drawdown | Latest 1Y return | Full target pass |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `slot-exit` | `breadth_advancing_gte_50 + breakout_20d + price_gap_up_2_to_5`, favorable/neutral | 3 | 2.0x | 41 | 82.93% | 361.84% | -3.99% | 251.93% | yes |
| `slot-exit` | same slice | 4 | 2.0x | 47 | 78.72% | 235.20% | -5.74% | 171.16% | no |
| `slot-exit` | same slice | 5 | 2.0x | 50 | 80.00% | 222.51% | -4.60% | 141.91% | no |
| `slot-daily` | same 3-slot slice | 3 | 2.0x | 41 | 82.93% | 355.79% | -14.32% | 248.41% | no |
| `slot-daily` | same 3-slot slice + `holding_calendar_gap_lte_6` | 3 | 2.0x | 37 | 81.08% | 250.56% | -9.37% | 215.15% | no |
| `slot-daily` | same 3-slot slice + pre-exit before calendar gaps >= 7 days | 3 | 2.0x | 41 | 82.93% | 359.93% | -9.37% | 234.00% | no |
| `slot-daily` | same 3-slot slice + pre-exit before calendar gaps >= 7 days + 30d correlation cap 0.35 | 3 | 2.0x | 31 | 80.65% | 321.26% | -8.49% | 208.03% | no |
| `slot-daily` | same 3-slot slice + pre-exit + 30d correlation cap 0.35 + exclude `entry_gap_lt_neg1` | 3 | 2.0x | 27 | 85.19% | 347.99% | -8.46% | 227.58% | no |
| `slot-daily` | same 3-slot slice + pre-exit + exclude `entry_gap_lt_neg1` + 30d correlation cap 0.35 + 10% prior-high trailing stop | 3 | 2.0x | 27 | 81.48% | 314.46% | -7.26% | 206.77% | no |
| `slot-daily` | same 3-slot slice + pre-exit + exclude `entry_gap_lt_neg1` + 30d correlation cap 0.35 + 7% prior-high trailing stop | 3 | 2.3x | 27 | 85.19% | 331.19% | -6.46% | 201.66% | no |
| `slot-daily` | same 3-slot slice + pre-exit + exclude `entry_gap_lt_neg1`, `proxy20_avg_gte_15`, `proxy60_avg_lt_0` + 30d correlation cap 0.35 + 7% prior-high trailing stop | 3 | 2.3x | 20 | 85.00% | 198.41% | -5.53% | 201.66% | no |
| `slot-daily` | same 20-trade strict slice + 18% prior-high next-open profit-lock exit | 3 | 2.08x | 20 | 85.00% | 199.09% | -5.00% | 201.99% | yes |

This is now a strict `slot-daily` research pass, not a live performance promise. The pass depends on a narrow 20-trade slice, 2.08x exposure, realistic financing/cost/slippage assumptions, a 7% prior-high trailing stop, proxy-market risk exclusions, and an 18% prior-high next-open profit-lock exit. The stricter `slot-daily` model still marks open trades daily and uses daily lows for drawdown checks. Production defaults use the same signal tags, favorable/neutral market regime, 5-day cooldown, 3 returned recommendations, live profit-lock alerting (intraday plus post-close planned-exit alerts for profit-lock and pre-calendar-gap exits), and strategy-exit performance tracking, so forward validation can accumulate against this profile.

A follow-up local search over 18,288 variants of the same core slice, adding common confirmation tags, risk-exclusion tags, 7%-10% prior-high protection, 2.0x-2.5x exposure, and 30-day correlation caps, still found no strict `slot-daily` row that passes all three targets. The best new boundary row used 7% prior-high protection and 2.3x exposure: it kept 85.19% trade win rate and 201.66% latest-one-year return, but strict max drawdown was still -6.46%. After adding proxy-market tags, excluding short-term proxy overheating (`proxy20_avg_gte_15`) and medium-term proxy weakness (`proxy60_avg_lt_0`) improved the strict drawdown boundary to -5.53% while preserving 85.00% win rate and 201.66% latest-one-year return. It is still not a 5% pass, but it is the closest strict result so far. Active-slot scans showed the same tradeoff: 3 slots are the best balance; 2 slots raise one-year return above 200% at lower exposure but drawdown remains worse than -7%, while 4 slots reduce return density. The next useful work is therefore new alpha or risk information, especially official announcements, industry/index regime features, and partial de-risking rules, not another fixed stop tweak.

A targeted announcement probe fetched CNINFO disclosure histories for the 31 candidates in the current strict boundary set and simulated ranking adjustments plus common event exclusions. This did not produce a passing row: hard blocking high-risk announcement contexts reduced selected trades below the 20-trade research floor, while simple announcement score ranking introduced weaker replacements and worsened strict drawdown. The useful outcome is engineering rather than immediate alpha: announcement context is now available as `announcement_*` tags for future full-cache rebuilds and live filtering, but it is not yet a production default gate.

A small exit-mechanism grid did not solve the strict drawdown gap: 4% hard stop cut latest-one-year return to 167.44% while drawdown stayed -12.74%; 3% hard stop dropped win rate to 62.50%; 6% trailing stop dropped win rate to 65.85%; 3-day hold dropped win rate to 65.96%. The next research direction is volatility/risk-budgeted position sizing or market-state exposure control, not tighter fixed stops.

Fixed take-profit also failed under the strict daily-low drawdown model. 6%/8%/10%/12%/15% take-profit kept or barely kept 70% win rate in some cases, but latest-one-year return stayed between 72.27% and 128.63%, and drawdown remained worse than -11%. The useful variant is not a fixed take-profit; it is a no-lookahead profit-lock exit that waits for a prior completed bar to reach 18% open-position profit and exits at the next open. On the current strict 20-trade slice, that rule plus 2.08x exposure gives 85.00% win rate, -5.00% drawdown, and 201.99% latest-one-year return.

Historical SZSE margin-eligibility smoke check, using an 80-symbol seed and a short 2026-06-15 window, confirmed that official as-of reports can tag real strict-universe trades without fetch errors. In that small window, requiring `margin_financing_eligible` reduced raw qualified trades from 64 to 29, selected trades from 10 to 8, raised win rate from 40.00% to 50.00%, and raised average trade return from 3.15% to 8.66%. This is not enough evidence for a production gate, but it makes `margin_*` a valid execution and sweep factor.

Dragon-tiger context was tested on the same 5-day hard-stop, 2.0x realistic-cost sweep. The adapter fetched 482 listing days with 0 fetch errors, expanded available tags from 98 to 115 and specs from 292,908 to 536,552, but did not improve the best compliant slice. The best 70% win-rate / 5% drawdown row remained `breadth_advancing_gte_50 + breakout_20d + price_gap_up_2_to_5`, favorable/neutral market, 50 trades, 80.00% win rate, -2.21% drawdown, and 96.83% latest-one-year return. Treat `lhb_*` as audit/context until a later source or transform proves alpha value.
