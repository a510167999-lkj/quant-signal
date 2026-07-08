# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

个人 A 股 / A 股 ETF 量化信号研究平台。FastAPI 后端 + 静态 Web 前端，部署在个人 VPS。**仅供个人研究，不构成投资建议。**

## Commands

虚拟环境与依赖：

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

运行开发服务器：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

测试（`pytest.ini` 已设 `pythonpath=.`、`testpaths=tests`，直接 `pytest` 即可）：

```bash
pytest                                   # 全量
pytest tests/test_signals.py             # 单文件
pytest tests/test_api.py::test_analyze_endpoint_with_fake_provider  # 单测
pytest -k dragon_tiger                    # 按名筛选
```

定时任务 / 研究 CLI 入口都在 `app/jobs.py`（`python -m app.jobs <command>`）。常用：

```bash
python -m app.jobs generate-recommendations --force --run-slot post_close
python -m app.jobs warm-market-cache --max-deep 500 --workers 4 --lookback-days 620
python -m app.jobs monitor-recommendations --force
python -m app.jobs research-backtest            # 严格历史回测
python -m app.jobs research-historical-sweep    # 标签/暴露/资本模型 sweep
python -m app.jobs research-sweep-file          # 从落盘的 qualified-trades 重跑 sweep
```

## Architecture

整个系统围绕一条**信号 → 推荐 → 验证**的研究管线组织，所有配置通过环境变量注入（`app/config.py` 的 `Settings` dataclass；测试默认不加载 `.env`）。

**数据层（无账号适配 + 本地缓存，避免重复打外部接口）**

- `market_data.py`：`MarketDataProvider` 统一封装历史 K 线。主路径 SQLite 磁盘缓存（`MARKET_DATA_CACHE_PATH`）是 09:32 扫描的速度关键；provider 可在 akshare / tushare 间切换，tushare 失败可回退 akshare。
- `akshare_client.py`：共享重试层，包住所有 AKShare 调用；`AKSHARE_*` 环境变量控重试参数，状态写 `AKSHARE_STATUS_PATH`，前端经 `/api/data/akshare-status` 暴露。**默认绕过 `http_proxy/https_proxy`**（东财端点走代理更不稳），仅 `MARKET_DATA_USE_PROXY=1` 时启用。
- `mootdx_l1.py`：通达信 L1 实时报价叠加层，供开盘确认和盘中提醒用，需 `ENABLE_MOOTDX_L1_CONTEXT=1`。
- 上下文数据各自独立模块：`news_context.py`（媒体风险）、`announcement_context.py`（巨潮公告，立案/处罚/退市等高风险直接阻断推荐）、`fund_flow.py`（主力资金流）、`margin_eligibility.py`（交易所融资融券标的）、`dragon_tiger.py`（龙虎榜）、`industry_history.py`、`trading_calendar.py`。

**信号与回测（核心研究逻辑）**

- `indicators.py` → `signals.py`：趋势结构、MACD、RSI、20 日突破、ATR 风控。
- `signal_tags.py`（最大模块之一）：把横截面特征转成 `breadth_*`、`proxy_*`、`price_*`、`rs*`、`margin_*`、`lhb_*`、`industry_*` 等标签，供生产推荐和历史 sweep 共用同一套门槛。
- `backtest.py`：**信号日次日开盘入场**，避免同 K 线回填偏差。
- `research_backtest.py`（约 2100 行）+ `research_sweep.py`：严格历史回测引擎，支持资本模型（`slot-exit` / `slot-daily` 逐日盯市）、暴露倍数、相关性预算、前日高点保护止损、分批止盈、长假前退出等。研究目标：70% 单笔胜率 / 5% 最大回撤 / 200% 一年组合收益——是研究标尺，不是实盘承诺。

**推荐服务（生产路径）**

- `recommendations.py`（约 1500 行）：`RecommendationService` 负责全市场扫描。流程是**先预筛全市场快照，再对行业分层候选池深扫**，叠加市场环境过滤（`market_regime.py`）、行业强弱（`industry_strength.py`）、历史信号质量、消息/公告/资金流风险叠加，最后用 `RECOMMENDATION_*` 环境变量定义的强信号门槛过滤。
- 跨进程互斥锁（`RECOMMENDATION_LOCK_PATH`）：扫描运行中再次触发返回当前状态，不重复开扫。
- 四个 run slot（`pre_open` 09:00 / `open_confirm` 09:32 / `pre_close` 14:55 / `post_close` 15:02）口径不同，盘前跳过 L1 确认，非交易日经交易日历跳过。
- `performance.py`：跟踪已推荐标的 1/3/5/10 日收益与胜率，落盘 `recommendations_history.jsonl`。
- `execution.py`：可执行性过滤（剔除高开过大、一字板、入场振幅过大等难成交样本）。

**API / 前端**

- `main.py`：`create_app()` 构造 FastAPI。所有 `/api/*` 端点走 `require_basic_auth`（`BASIC_AUTH_USER/PASSWORD` 都空时禁用）；`MarketDataError` 统一映射 502。SPA fallback 把非 api 路径都回 `static/index.html`。
- 关键端点：`POST /api/analyze`（单标的）、`POST /api/recommendations/run`（后台触发扫描）、`GET /api/recommendations/latest`、`GET /api/performance/recommendations`、`POST /api/alerts/monitor`。
- 前端是 `app/static/` 下的原生 HTML/CSS/JS（无构建步骤），直接调上述 API。

**运行时数据**：全部落 `data/` 目录（K 线 SQLite、推荐 JSON/jsonl、各上下文缓存、alerts）。已 gitignore，不要提交。

## Key conventions

- 所有可调参数走环境变量 + `Settings` dataclass（`frozen=True`），不要在代码里硬编码阈值。改门槛前先看 `.env.example` 里对应的 `SCAN_*` / `RECOMMENDATION_*` / `MIN_BACKTEST_*` 段落。
- 测试用 `monkeypatch` 替换 `main.DATA_PROVIDER` / `main.SETTINGS`，用 `tmp_path` 隔离落盘——**不要让测试触碰真实 `data/` 或外部网络**。
- 历史回测严格防 lookahead：当前成分不当历史成分、当前上交所名单不回填历史融资标的。新增历史标签时遵循同一原则（见 `PLAN.md` Phase 2 的 `--margin-eligibility-context` / `--industry-rotation-context` 设计）。
- `PLAN.md` 是研究决策日志（记录了每一轮 sweep 的样本数、胜率、回撤、收益），改策略门槛前先查它确认当前生产切片及其边界条件。
- `DATA_SOURCES.md` 列出每个外部数据源的官方锚点和已知局限，接新数据源前先查。
