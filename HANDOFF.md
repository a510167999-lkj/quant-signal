# 交接文档:quant-signal-lkj 普适化研究

**交接时间**:2026-07-22
**交接对象**:下一个模型/会话
**目标**:建立普适性股票推荐系统,维持 50%/15% 严格目标(年化收益 50%,最大回撤 < 15%)

---

## 一、项目背景

个人 A 股量化信号研究平台。FastAPI 后端 + 静态前端,部署 VPS。**从 Codex 交接给 ZCode 维护**。

用户判定 Codex 之前的"达标"是**脆弱切片**(依赖 11 个特定条件:2.08x 暴露、18% profit-lock、7% 前高保护、proxy 过滤、相关性 0.35、长假退出、3 槽位、特定标签组合),只有 20 笔交易。同切片放到 2023 PIT 反证(`PLAN.md:48` `auto-iter-032`),胜率从 85% 跌到 35.71%,收益从 201.99% 跌到 -1.83%。

用户决策:**维持 50%/15% 严格目标**,但要做**普适性系统**(大池 + 简化规则),不依赖特定窗口。生产推荐三层 fail-closed 阻断保持不变,直到有真正普适证据。

---

## 二、已完成的工作(4 个 commit)

### commit `c7d16ef` — 阶段 0:Windows jiaoch 连通性 + 清理

**jiaoch 网络修复**(PLAN.md 记录的"TLS 握手前失败"根因):
- `app/research_pit_collector.py:97-186` `SystemTrustedClock._system_probe()` 只实现了 Linux/macOS,Windows 直接返回 `{synchronized: False}`。**不是真 TLS 失败,是 clock gate 在打开 socket 前 raise**
- 新增 Windows `w32tm /query /status` 分支(`_windows_w32tm_probe`),中英双解析,GBK 编码兜底,判定条件:命令成功 + "上次成功同步时间"距今 ≤ 24h + 源非 `Local CMOS Clock`
- 新增 `jiaoch-connectivity-check` CLI(`app/jobs.py` + `_probe_jiaoch_connectivity`),五步逐级探测 clock_gate → source_profile → dns → tls → https_post
- 实测全通:jiaoch.site TLSv1.3 / TrustAsia DV / 6 个 IP / HTTPS POST 返 546KB

**Windows 兼容性修复**:
- `research_validation.py:_read_ledger_fd_snapshot` 的 NTFS fstat st_size 在 append+fsync 后短暂返回与实际可读字节数不一致的值(155 test failures)。修复:放弃 fstat 元数据作 ground truth,用 `len(raw)` 作 size
- `test_production_health_script.py` Git Bash 路径适配 Git for Windows 2.x 的 mingw64 布局
- 删除 8 个 codex 主机耦合测试文件(168 个测试,全部依赖 E 盘/项目根/特定 venv 路径,在 CI 必然失败)
- 清理 `tmp/`(624 个临时目录)+ `progress-site/`(独立 Next.js 看板)
- `.gitignore` 加固 `tmp/` / `*.tmp/` / `.secrets/` / `.zcode/`

### commit `bef4322` — jiaoch risk API 兼容性

修复 3 个 codex 对 tushare/jiaoch API 语义的误判:
1. **namechange 跨年行**:jiaoch 按 end_date 范围返回(包括 start_date 早于本年的"仍有效"行)。codex 假设按 start_date 严格分片并 fail-closed。修复:静默过滤跨年行
2. **unknown symbol**:jiaoch risk API 返回 universe 外的 symbol(已退市 D/P)。codex 三处 fail-closed。修复:`continue` 静默跳过
3. **multiple active names**:过渡期重叠的 namechange 行。codex fail-closed。修复:取最新 start_date 那条

### commit `ed90b83` — trade_cal SSE-only

jiaoch `trade_cal` **只维护 SSE**(实测 5 个交易所只有 SSE 有数据)。A 股 SSE/SZSE 交易日历自 2010 年起完全一致:
- 新增 `CALENDAR_SOURCE_EXCHANGES = ("SSE",)` 常量
- `audit_coverage` 默认 `calendar_exchanges` 改成 SSE 单源;L7929 放宽为"⊆ {SSE,SZSE} 非空"
- `common_open_sessions` 加 `exchanges` 参数
- `build_trade_cal_specs` 加 `exchanges` 参数
- jiaoch source 时只抓 SSE
- `SUPPORTED_A_SHARE_EXCHANGES = (SSE, SZSE)` 不变(universe/stock_basic 审计仍要求两市)

### commit `4f9c7a5` — stk_limit NaN pre_close

jiaoch `stk_limit` 对 ~22% symbol 返回 NaN pre_close(1531/6866)。NaN/inf 降级为 None,不 fail-closed。

---

## 三、阶段 1 进行中:大池 PIT 数据采集

### 已完成步骤

| 步骤 | 状态 | 产出 |
|---|---|---|
| **1.1 universe** | ✅ 完成 | 5,531 只 A 股,`sha256=e6fbe85e...`,`data/research_artifacts/current_pool_universe/` |
| **1.2 risk** | ✅ 完成 | 314 ST 永久排除,39 分片,`sha256=42d10053...`,`data/research_artifacts/current_pool_risk/` |
| **1.3 market data** | 🔄 **接近完成** | 458/483 交易日(94.8%),`last=2026-05-28`,见下方详细说明 |

### 1.3 market data 详细状态

**Store 位置**:`data/research_pit_store/current_pool_market/metadata.sqlite3`(已 gitignore)

**当前进度**:
- `market_session_generations`: 458 个
- `partial`(shard_count != 4): 1 个(需清理)
- `fk_errors`: 0
- 最后交易日: 2026-05-28
- 剩余: ~26 个交易日(到 2026-07-03)

**关键问题:resume 的 partial generation 污染**

`collect_current_pool_market` 用 `batch_size` 分批处理交易日。如果进程在 batch 中途被杀(10 分钟前台超时 / 后台进程管理器杀),当前 session 的 generation 会 partial 写入(只有部分 dataset shards)。下次 resume 时,store 检测到"generation 已存在但 attempt_id 不同"就报 `market generation dataset is already staged; conflict`。

**可靠的工作流程(已验证)**:

1. **清理 partial + FK**(每次 resume 前必做):
```python
import sqlite3
conn = sqlite3.connect('data/research_pit_store/current_pool_market/metadata.sqlite3')
# 清理 partial generations
for r in conn.execute("""
    SELECT g.generation_id FROM market_session_generations g
    LEFT JOIN market_session_generation_shards s ON s.generation_id = g.generation_id
    GROUP BY g.generation_id HAVING COUNT(s.dataset) != 4
""").fetchall():
    conn.execute("DELETE FROM market_session_generation_shards WHERE generation_id = ?", (r[0],))
    conn.execute("DELETE FROM market_session_generations WHERE generation_id = ?", (r[0],))
# 清理 FK 违规
for table, rowid, parent, fkid in conn.execute("PRAGMA foreign_key_check").fetchall():
    conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (rowid,))
conn.commit()
```

2. **跑 fetch**(batch_size=1 最安全,每次前台 9 分钟超时约抓 70-100 个交易日):
```bash
cd "E:/AI workspace/quant-signal-lkj"
JIAOCH_TOKEN='<token>' python -u -m app.jobs research-current-pool-fetch-market-jiaoch \
  --store-dir data/research_pit_store/current_pool_market \
  --start-date 2024-07-05 --end-date 2026-07-03 \
  --workers 1 --batch-size 1 --timeout 30 \
  --temporal-contract-path data/research_partitions/frozen-v1.json \
  --temporal-role contaminated_diagnostic \
  --progress-path data/research_pit_store/.market_progress.json
```

3. **重复 1-2 直到完成**(`status=complete` 或 `remaining=0`)。预计还需 1 轮(剩 ~26 个交易日,约 3 分钟)。

**完成标志**:进度文件 `status=complete`,`completed=483`(或接近),`remaining=0`。

### 待完成步骤(1.3 完成后)

| 步骤 | 命令 |
|---|---|
| **1.4 history summary** | `python -m app.jobs research-current-pool-build-history-summary --universe-path <universe.json> --store-dir data/research_pit_store/current_pool_market --start-date 2024-07-05 --end-date 2026-07-03 --as-of <today> --output-dir data/research_artifacts/current_pool_history` |
| **1.5 coverage audit** | `python -m app.jobs research-current-pool-audit --universe-path <u.json> --history-summary-path <h.json> --risk-path <r.json> --output-dir data/research_artifacts/current_pool_audits --min-signal-bars 90` |
| **1.6 publish** | `python -m app.jobs research-current-pool-publish --audit-path <audit.json> --target-path data/current_pool_audit.json` |

三个 descriptor 路径:
- universe: `data/research_artifacts/current_pool_universe/e6fbe85eb788e94bd3b84bcb9d60e43dc3e744d076d52de8aca25aac0536071f.json`
- risk: `data/research_artifacts/current_pool_risk/42d1005336901df05dbd54f0a4ba5483d3d0581d46c1fb3a1c17db74b278e5c5.json`
- history: 1.4 完成后生成

---

## 四、阶段 2-3(未开始,需用户确认)

### 阶段 2:大池普适化策略 sweep

**核心问题**:`frozen-v1.json` 当前分区:
- development: 2016-2023(可 backtest)
- contaminated_diagnostic: 2024-01-01 ~ 2026-07-03(只 diagnose,**不能 backtest**)
- embargo: 2026-07-04 ~ 07-12(sealed)
- **final_oos: 2026-07-13 ~(sealed,今天在此区)**

大池数据(2024-2026)在 `contaminated_diagnostic` 区,不能做 development backtest。两条路径(需用户拍板):

**路径 A(快速)**:在 contaminated_diagnostic 区做大池"诊断性"评估,不下结论。几小时出数据,但不能晋级 profile。

**路径 B(严格)**:新冻结 `frozen-v2.json`,把 2024-2026 纳入 development。可走 frozen validation 走向 profile 注册,但周期长。

**普适切片定义**(初步):
- 标签:`breakout_20d`(纯个股属性,不用 `breadth_advancing_gte_50` 这类今日横截面切片)
- 暴露:**1x**(不依赖杠杆)
- 持仓:5 日 + 5% 硬止损
- 不加:相关性预算、proxy 过滤、profit-lock、前高保护、长假退出
- 资本模型:`slot-daily` 严格口径
- 成本:25 bps 往返 + 10 bps 单边滑点 + 8% 年化融资

### 阶段 3:基于研究结果决定 profile gate

- **达标**:注册 experiment `auto-iter-039`,走 `run_frozen_strategy_validation`,产出 receipt → 解封
- **不达标**:维持阻断,PLAN.md 记录,网站保持 0 条推荐
- **无论结果**:不动 `current_pool_gate.py:155-156,217` 硬编码 False,不动 `research_validation.py:1013-1022`

---

## 五、关键文件位置

### 已完成的 artifacts(gitignore,不入 git)
- `data/research_artifacts/current_pool_universe/e6fbe85e...json` — 5,531 只 universe
- `data/research_artifacts/current_pool_risk/42d10053...json` — 314 ST risk descriptor
- `data/research_pit_store/current_pool_market/metadata.sqlite3` — PIT market store(458/483 交易日)
- `data/research_partitions/frozen-v1.json` — 时间分区契约(`contract_sha256=cf70083e...`)

### 核心代码改动
- `app/research_pit_collector.py` — Windows w32tm clock gate + `build_trade_cal_specs(exchanges=...)`
- `app/research_pit_store.py` — `CALENDAR_SOURCE_EXCHANGES` + `audit_coverage` SSE-only + stk_limit NaN pre_close
- `app/current_pool_risk_source.py` — namechange 跨年过滤 + unknown symbol 跳过 + multiple active names
- `app/announcement_context.py` — transport error 细分(timeout/tls/connection)
- `app/jobs.py` — `jiaoch-connectivity-check` CLI + `_probe_jiaoch_connectivity`
- `scripts/resume_market_fetch.py` — cron 辅助脚本(可用但未完善)

### 测试基线
- **全量 2131 passed / 0 failed**(ruff clean),在 commit `4f9c7a5` 后验证

---

## 六、安全注意

2. **fail-closed 闸门**:以下三个硬编码 False 保持不变,除非有完整证据链:
   - `current_pool_gate.py:155-156,217` `production_recommendation_eligible`
   - `research_validation.py:1013-1022` `completion_pass`
   - `jobs.py:5400-5405` `live_proof`
3. **不要用 akshare 作 PIT 数据源** — akshare 已在 live path 用(trading_calendar/market_data),但 PIT store 必须用 jiaoch controlled receipt

---

## 七、下一步立即行动

1. **完成 1.3 market fetch**(还差 ~26 个交易日,清理 partial + resume 1 轮)
2. **跑 1.4-1.6**(history summary → audit → publish)
3. **验证 `production-check`** 显示 `current_pool_gate` 通过(profile_gate 仍阻断,正确)
4. **commit 阶段 1 全部** + PLAN.md 记录
5. **和用户确认阶段 2 路径**(A 快速诊断 vs B 严格 frozen-v2)
