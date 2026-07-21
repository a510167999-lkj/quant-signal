# LKJ A 股推荐研究项目执行计划

状态日期：2026-07-19  
唯一工作区：`E:\AI workspace\quant-signal-lkj`  
证据等级：`official_exchange_pit=false`、`eligible_pool_count=0`、`production_recommendation_eligible=false`

## 当前冻结状态

- ledger 最大序号为 126；seq125 仅 registered，seq126 为 `RUNNER_LAUNCH_STALLED/precompute_launch`，二者永久只读。
- ledger SHA256：`371ddbbfca18c945ff2f4f433a1f41b9384450ff6b9ce84ea0768259a84e5a16`。
- item-1609 失败 staging 永久封存；`treatment-plan.json` SHA256：`6c097634c4e0d9bbadd31883d920bdf5decc99589ecbdb006ec5b8fa3222aac2`。
- 项目相关 Python/pytest/runner 进程为 0；尚无 sequence>126、新 038、raw/cache 或 purged。
- 既有受监督 launcher 的 targeted、回归、独立复审、READY/ACK probe 与旧代码完整 pytest 已通过；任何后续代码变化必须重新取得适用门禁，不能复用旧完整测试作为新代码证明。

## 固定研究边界

- 当前 038 的 `data_cutoff=2026-07-10`，development 数据范围保持独立冻结。
- 固定门禁：净年化收益不低于 50%；胜率 52%–60%（超过 60% 也判 RED）；最大回撤不高于 15%；Profit Factor 不低于 1.3；Calmar 不低于 1.5。
- 主板与创业板；排除科创板、北交所、ST；最终候选最多 3 只。
- 不联网补充 provider 数据，不部署、不下单、不 push，不进入 embargo/final_oos。
- 任何证据不足均输出“无合格推荐”，不得降低门槛换取名单。

## 执行阶段

1. **发布控制面恢复**：运行一次获准的全新 E-only 合成句柄/原子重命名诊断 probe；失败或仍无法区分根因即封存并停止下游。
2. **plan-publication successor**：仅在根因明确且可由 E-only 可逆改动修复时，按 RED→GREEN、targeted、静态检查、独立复审及适用的唯一完整 pytest 发布全新版本。
3. **唯一 038**：确认无活动/未终态实验后创建全新 preregistration 与 sequence>126，使用受监督 launcher 运行一次；任何 RED 均封存且不重试。
4. **唯一 purged**：仅当 038 五项指标与全部完整性门禁均 GREEN 时执行一次预登记 purged validation。
5. **methodology-v2**：建立研究尝试/选择路径台账、PBO/CSCV、Deflated Sharpe 或等价修正、分块 bootstrap、walk-forward、跨市场状态、基准/消融、参数平台与交易现实压力测试。
6. **终态交付**：证据索引、数据血缘、实验台账、方法报告、复现实验入口、失败清单、推荐资格结论和日常候选/无候选操作手册。

## 单写者与失败规则

- 状态改变型实验、ledger、完整 pytest、038、purged 始终严格串行且只执行一次。
- 预期 TDD RED 可修复；真实实验、完整性或控制面 RED 必须原位封存，不补跑、不重标。
- successor 必须使用新 ID、新路径和新哈希，不得伪装成旧 run 的延续。
- 每阶段记录输入/输出 SHA、PID/exit、门禁结果、剩余风险与下一阶段。
