# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

个人 A 股量化信号研究平台。FastAPI 后端 + 静态 Web 前端，部署在个人 VPS。**仅供个人研究，不构成投资建议。**

## Research goal（硬约束，写死）

用户目标只有一条，禁止被工程任务改写：

> 做一条可复现的 A 股策略：真实成本后滚动 12 个月净年化 **≥ 26%**，最大回撤 **≤ 15%**；只做沪主板、深主板、创业板；**不考虑 ST、科创板、北交所**；**不自动交易**。

权威常量见 `app/research_goal_contract.py`。改市场范围、名称排除或绩效门槛必须先改该模块并补测试，禁止在业务代码或会话里另起一套口径。

| 维度 | 约束 |
|------|------|
| **交付物** | 一条规则清楚、可复现的策略证据。不是交易系统，也不是 Factor V3 / PIT 权威链本身。 |
| **数据** | **Jiaoch-only**（Tushare 镜像站）。正式训练/回测/证据链不得混 AKShare 或其他未授权源。 |
| **市场** | 仅 `SSE_MAIN`（沪主板）、`SZSE_MAIN`（深主板）、`SZSE_CHINEXT`（创业板）。 |
| **明确排除** | **ST / \*ST**、**退市**、**科创板 `SSE_STAR`（688/689）**、**北证 `BSE`**。上游账本可先保留五板块做 PIT 证明，下游策略宇宙必须过滤。 |
| **主绩效** | 真实成本/滑点后：**滚动 12 个月净年化 ≥ 26%**，**最大回撤 ≤ 15%**。两线同时过才算过。 |
| **辅助诊断** | Profit Factor ≥ 1.3、Calmar ≥ 1.5；胜率观察约 52%–60%。辅助指标不能替代主绩效。 |
| **分区** | `development → embargo → final-OOS` 严格隔离；development 结果不得直接注册生产 profile。 |
| **产出** | 每日最多 **0–3** 只研究建议；**禁止自动下单**。 |

development 数字只是不可晋级的假设筛选。正式承认“有效”仍要独立 OOS。

### 当前主线（2026-08-13，以 Codex `codex/main` 为准）

Zcode 的 7 月接管与 8 月初路径 A 有效，但 **Codex 分支更新**。后续会话以本文件 + `项目进度.md` 为准，不要回到“必须先采全新 v3 store”当主线。

**已完成、仍有效**

- 池子口径已对齐：主板 + 创业板，排除 ST / 科创 / 北证。
- 路径 A 策略矩阵完成；当前最佳 development 候选是 `p0_loss_streak_3`（约 2 年窗：收益 85.6%，回撤 -14.7%）。
- 冻结主候选全路径 MDD **-21.5%**，所以才要看约 3 年窗。
- 2026-08-12 已用 Jiaoch `stk_mins` 合成日线回填约 733 个交易日 / 约 5284 只；不是“没采过”。
- 单位、QFQ、frozen fail-closed、frozen-v3 coverage-audit 锚已修。coverage-audit 只证明“可以开采集门”，不等于 3 年研究输入已干净。

**当前阶段 ID**：`path-a-protocol-signal-horizon/v1`

假设用中文分支记账，前缀 `假设/`，例如 `假设/回踩抗追高`。`codex/main` 仍是当前工作线。新假设开新的 `假设/` 分支，不要在别人的假设分支上改参。见 `项目进度.md` §1.25。

计划已改序。**2026-08-15 用户把收益门槛从 30% 调到 26%**，回撤仍是 15%，对齐冻结假设 holdout +26.5%。`vol_skip_rsi_adv_s85` 仍是 **rejected_slice_hypothesis**，禁止回 191 调参。

1. **协议**（`app/factor_v3_path_a_research_protocol.py`）：train `2023-07-03..2025-06-30`，holdout `2025-07-01..2026-07-03`。搜索宇宙 = Jiaoch v2 holdout，不是 191 成交切片。双过 = **最新滚动 12 月收益 ≥26%** 且 **该分区 MDD ≤15%**。全路径收益只做诊断，不当年化。
2. **预注册基线** holdout 全灭。`proto_t2_m1` 最好 MDD **-23.97%**。
3. **因子刀**（仍在 e4 突破核上叠质量过滤）：holdout 全灭；最接近 15% 的是 `factor_ma70` **-12.0% / -15.96%**。
4. **身份刀**：holdout 最好 `alt_rs_leader` **+11.95% / -14.16%**。
5. **信号刀**：2774 只上 `sig_pull_negext` **+16.34% / -14.15%**。
6. **持有期 + 大宇宙**：`sig_pull_negext_h5` **+26.45% / -9.48%**，仍是 holdout 最近。
7. **第三种入场** holdout 全灭。
8. **冻结假设** `sig_pull_negext_h5` holdout **+26.5% / -9.5%**，按新口径 **26/15 数字双过**。**仍不是有效策略**：这段 holdout 选规则时已经看过。
9. **独立 OOS** `2026-07-04` 起：同一 3497 只宇宙，zero-refit。Jiaoch 日线到 **2026-08-20**。回踩 selected=1。连跌反弹纸面账本 **1 笔 / 净 -2.64%**（华鑫股份，含 25+10bps）。**12 月满窗日 = 2027-07-04**，还差约 **318** 天，不能评 26/15。
10. **日滚已装**：工作日 18:30 任务 `quant-signal-lkj-path-a-reclaim-oos-roll`。有新 K 线就追加缓存并 zero-refit；没新数据就记还差多少天。不改规则，不放宽 `market_level`，非正式 final-OOS，不注册生产 profile，不自动交易。
11. **诊断切分** `2025-01-01`：冻结回踩 zero-refit。eval 最新 12 月 **-3.5%** / **-16.9%**，26/15 不过。不是独立 OOS。
12. **反弹身份族**（不是拧回踩）：`bounce_dn2_negext` 锁定切分 **train+holdout 双过**。2025-01-01 诊断 eval **+17.4%**，收益不够。**仍非正式有效**。
13. **形态族** 全灭。十日均线收回 holdout +24% 但 train 很差。
14. **月度三臂**：23 次决策只换 1 次（空仓→连跌反弹）。holdout +15.9% 不过。
15. **每周三臂**：同一规则，59 次决策仍只换 **1** 次（2023-07-11 空仓→连跌反弹），回踩从未入选。holdout 仍 **+15.9% / -14.2%**。加快换档没用。
16. **近月三臂**：估计窗改成近 **21** 个共享交易日。59 次决策换了 **14** 次，回踩占用 18。train **+8.8% / -15.7%**、holdout **+8.4% / -14.5%**，都不过。短窗会换，但比一直做连跌反弹更差。
17. **经典指标**：MACD 金叉 / KDJ 超卖金叉 / BOLL 下轨收回，教科书参数。锁定切分无一双过。最接近的是 `classic_kdj_negext` holdout **-0.6% / -11.8%**，没收益。不拧周期。
18. **缺口回补**：真跳空当日回补 / 低开 3% 收回 / 隔日回补。锁定切分无一双过。最接近的是 `gap_true_negext` holdout **+16.8% / -8.9%**，收益不够。不拧 3%。
19. **daily_basic**：733 日旧采集没有 PE/PB。Path A 另采了锁定窗 728 日 `pe/pe_ttm/pb/total_mv/circ_mv`。**亏损股不一定不能买**。
20. **估值排序**：连跌反弹/回踩/缺口当时机，PB/PE_TTM/市值当排序。锁定切分无一双过。亏损低 PB train +20% holdout 垮。最好发展假设仍是 `bounce_dn2_negext`。
21. **换手放大**：2 倍自身中位或截面 90 分位。无一双过。`turn_x2` holdout +25.5% / -16.6%。
22. **资金流**：净流入由负转正。`flow_in_negext` holdout +18.1% / -14.2%，收益不够。
23. **行业轮动**：`ind_enter` holdout +30.4% / -13.4% 数字过，train -38.8%。不拧。
24. **有利市开关**：favorable 才做连跌反弹。train 双过，holdout +11.5% / -6.4%。仓位太少。
25. **持有/冷却**：同一套连跌反弹抗追高，预注册持有 1/3/7/10、冷却 0/1/3/10。换持有期全灭。换冷却与原 5/5 数字完全相同（单槽稀疏，冷却绑不上）。不是新身份。
26. **每日选股**：`scripts/run_path_a_protocol_bounce_daily.py` 按 `bounce_dn2_negext` 零改参记账。只打印/落盘当天选了谁，不注册生产。2026-07-04 起独立 OOS 至今合格信号 0（市场不在 favorable/neutral）。

**明确不做**

- 不自动交易；不放宽 market_level 去凑短 OOS。
- 不把 TCB / formal materializer / 728-session v3 capture lineage 当本阶段门槛。
- 不把 development 数字写成已证实有效。

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
python -m app.jobs monitor-planned-exits --force   # 盘后日级计划退出（profit-lock / 长假）
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
- `research_backtest.py`（约 1150 行）+ `research_sweep.py`（约 1130 行）：严格历史回测引擎，支持资本模型（`slot-exit` / `slot-daily` 逐日盯市）、暴露倍数、相关性预算、前日高点保护止损、分批止盈、长假前退出等。当前主研究目标为真实成本/滑点后最近滚动 12 个月净收益约 26%、最大回撤不超过 15%。胜率观察区间为 52%-60%，并联合检查盈亏比、Profit Factor（至少约 1.3）、Calmar（至少 1.5，2 更佳）和滚动稳定性。研究缓存结论不等于实盘可用证明。
- `research_backtest` 的纯计算层已按职责拆成子模块，`research_backtest` 本身只负责回测编排与 payload 构造，并**反向 import 这些子模块**以保持内部调用点与测试路径稳定（改调用方前先看这条约定）：
  - `research_equity.py`：权益曲线 / 资本模型数学（`slot-exit` / `slot-daily` 逐日盯市、最大回撤）。
  - `research_context.py`：信号日上下文计算（大盘强度 / 代理收益 / 相对强度 / K 线形态 / 市场宽度 / 行业轮动 / 历史质量）。
  - `research_stats.py`：trades 统计聚合与各维度分桶（`_research_group_stats` 等）。
  - `research_portfolio.py`：组合管理（滚动窗口权益、按信号日选股与仓位控制）与单笔交易实现（止损/止盈/移动止损 + mark-to-market）。
  - `research_cache.py`：K 线与公告的 JSON 文件缓存 fetcher（miss 时拉外部源）。
  - `research_common.py`：底层无依赖 helper（`_num` / `_date_value` / `_date_yyyymmdd`），其他 research_* 模块共享。

**推荐服务（生产路径）**

- `recommendations.py`（约 1500 行）：`RecommendationService` 负责全市场扫描。流程是**先预筛全市场快照，再对行业分层候选池深扫**，叠加市场环境过滤（`market_regime.py`）、行业强弱（`industry_strength.py`）、历史信号质量、消息/公告/资金流风险叠加，最后用 `RECOMMENDATION_*` 环境变量定义的强信号门槛过滤。
- 跨进程互斥锁（`RECOMMENDATION_LOCK_PATH`）：扫描运行中再次触发返回当前状态，不重复开扫。
- 四个 run slot（`pre_open` 09:00 / `open_confirm` 09:32 / `pre_close` 14:55 / `post_close` 15:02）口径不同，盘前跳过 L1 确认，非交易日经交易日历跳过。
- 退出 alert **双轨**：盘中 `monitor_recommendations`（止损/支撑/盘中跌幅/信号转弱，实时）+ 盘后日级 `monitor_planned_exits`（计划性退出：profit-lock exit 按 T+1 开盘口径、长假前退出按收盘口径，均执行性 alert，对齐回测 `_apply_partial_profit_lock` / `_truncate_trade_before_calendar_gap`）。阈值 `MONITOR_PROFIT_LOCK_ACTIVATION_PCT` / `MONITOR_PRE_EXIT_CALENDAR_GAP_DAYS`。
- `performance.py`：跟踪已推荐标的的 1/3/5/10 日固定收益（选股 alpha baseline）**及** `strategy_exit`（复用回测退出管线 `_realized_trade_from_future`→profit-lock→长假`，单笔退出语义与回测标尺可比；组合资本模型口径不在单笔跟踪范围）。落盘 `recommendations_history.jsonl`。
- `execution.py`：可执行性过滤（剔除高开过大、一字板、入场振幅过大等难成交样本）。

**API / 前端**

- `main.py`：`create_app()` 构造 FastAPI。所有 `/api/*` 端点走 `require_basic_auth`（`BASIC_AUTH_USER/PASSWORD` 都空时禁用）；`MarketDataError` 统一映射 502。SPA fallback 把非 api 路径都回 `static/index.html`。
- 关键端点：`POST /api/analyze`（单标的）、`POST /api/recommendations/run`（后台触发扫描）、`GET /api/recommendations/latest`、`GET /api/performance/recommendations`、`POST /api/alerts/monitor`（盘中实时）、`POST /api/alerts/planned-exits`（盘后日级计划退出）。
- 前端是 `app/static/` 下的原生 HTML/CSS/JS（无构建步骤），直接调上述 API。

**运行时数据**：全部落 `data/` 目录（K 线 SQLite、推荐 JSON/jsonl、各上下文缓存、alerts）。已 gitignore，不要提交。

## Key conventions

- 所有可调参数走环境变量 + `Settings` dataclass（`frozen=True`），不要在代码里硬编码阈值。改门槛前先看 `.env.example` 里对应的 `SCAN_*` / `RECOMMENDATION_*` / `MIN_BACKTEST_*` 段落。
- 测试用 `monkeypatch` 替换 `main.DATA_PROVIDER` / `main.SETTINGS`，用 `tmp_path` 隔离落盘——**不要让测试触碰真实 `data/` 或外部网络**。
- 历史回测严格防 lookahead：当前成分不当历史成分、当前上交所名单不回填历史融资标的。新增历史标签时遵循同一原则（见 `PLAN.md` Phase 2 的 `--margin-eligibility-context` / `--industry-rotation-context` 设计）。
- `PLAN.md` 是研究决策日志（记录了每一轮 sweep 的样本数、胜率、回撤、收益），改策略门槛前先查它确认当前生产切片及其边界条件。
- `DATA_SOURCES.md` 列出每个外部数据源的官方锚点和已知局限，接新数据源前先查。
