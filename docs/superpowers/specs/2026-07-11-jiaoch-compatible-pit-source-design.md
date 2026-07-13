# Jiaoch 兼容源 PIT 审计采集设计

## 目标

把 `https://jiaoch.site` 作为一个固定、Tushare Pro 协议兼容的数据源接入现有
point-in-time 审计采集链，为多年 A 股历史研究生成可离线复验的 v4 工件。

本阶段只改变历史数据采集来源，不改变日常推荐 provider、策略参数、成本模型、
市场状态阈值、训练/验证/OOS 分区或生产部署。最终 OOS 保持关闭。

## 本轮研究假设

### 假设

固定的 Jiaoch HTTPS 兼容源能够为现有七类必需数据集返回与 Tushare Pro 规范一致的
完整响应，因此可以在不放宽任何覆盖、血缘或执行门禁的前提下生成同等强度的审计工件。

### 单一改动

仅新增一个固定的 `jiaoch` 采集源配置。数据规范、解析器、SQLite schema、coverage
audit、artifact verifier 和回放策略均不修改其经济语义。

### 主指标

- 七类必需数据集均通过真实权限冒烟。
- 所有成功响应在解析前保存原始字节并绑定请求语义。
- token 在日志、错误、manifest、SQLite 元数据和输出中出现次数为零。
- 同一测试数据经官方 profile 与 Jiaoch profile 进入存储后，规范化行和覆盖审计结果一致。

### 反证条件

出现以下任一情况即淘汰本轮，不得进入多年回填：

- 任一必需接口不存在、无权限、字段缺失或响应协议不兼容。
- 响应日期与请求日期不一致，或行数达到上限而无法证明完整。
- `daily`、`adj_factor`、`stk_limit` 无法与当日 PIT 股票集合闭合。
- `suspend_d` 的空结果无法与成功请求证据区分。
- token 出现在任何持久化或用户可见输出中。
- 采集器需要回退到 AKShare、MOOTDX、当前快照或可变缓存。

### 退出条件

单元、集成、CLI 和真实七接口冒烟全部通过，完整 pytest、Ruff、diff check 通过，且独立
审查无 P0/P1/P2，才允许开始独立的多年数据回填实验。该条件不等于策略或 OOS 晋级。

## 方案选择

### 采用：固定 source profile

CLI 新增 `--source-profile`，取值为：

- `official`：保持当前官方 Tushare 行为。
- `jiaoch`：固定为 `https://jiaoch.site`，固定 host pin 为 `jiaoch.site`，凭据读取
  `JIAOCH_TOKEN`。

不提供任意 URL/host 参数组合。这样可以使 source identity 进入请求语义哈希，避免同一
实验在不同服务之间静默漂移。

### 不采用：任意兼容 URL

任意 URL 灵活但无法稳定证明数据源身份，容易让环境变量或 CLI 参数改变实验语义。

### 不采用：修改 Tushare SDK 私有属性

教程中的 `_DataApi__token` 和 `_DataApi__http_url` 能用于人工测试，但它绕过现有受控
transport，不能保证原始实体字节、重定向拒绝、大小上限、可信时钟和 attempt lineage，
因此不进入严格采集路径。

## 架构

### SourceProfile

新增不可变 profile 描述：

- `name`
- `api_url`
- `allowed_hosts`
- `token_env`
- `request_protocol`

profile 只负责确定连接身份，不参与字段解析或数据规范化。

### CLI

`research-pit-fetch-tushare` 保持原命令名以避免破坏既有自动化，并增加：

```text
--source-profile official|jiaoch
```

默认现为 `jiaoch`；`official` 仅作为显式兼容/诊断选项。选择 `jiaoch` 时：

- 禁止同时通过 `--api-url` 改写 URL。
- 必须从 `JIAOCH_TOKEN` 读取非空凭据。
- 只允许 `https://jiaoch.site`。
- 不接受 `--allow-insecure-official-http`。

### 请求路径

按附件中的网络请求示例，Jiaoch 使用固定的接口路径协议：向
`https://jiaoch.site/{api_name}` POST：

```json
{
  "api_name": "daily",
  "token": "<redacted>",
  "params": {},
  "fields": "..."
}
```

不使用教程中的按接口 URL 作为自动回退。若真实根路径冒烟失败，本轮直接失败并重新设计，
`api_name` 只能来自冻结的七接口合同，不允许用户输入任意路径。不得在同一实验中
静默切换协议。

### 存储与血缘

复用 `ControlledTushareCollector`、`PITReceiptStore` 和现有 v4 artifact：

1. 冻结 canonical FetchSpec。
2. 把 profile 名、URL、请求协议、参数、字段和 row cap 写入 request semantics。
3. 记录可信时钟和单调时钟。
4. 通过无重定向 transport 获取完整响应实体。
5. 原始响应先进入 CAS，再进行规范化和 generation staging。
6. 只有 session generation 四个 shard 全部闭合后才能 publish。

token 不属于 request semantics，也不保存 token 原文。现有 `wire_request_sha256` 继续覆盖实际
发送的完整请求体（因此会随 token 改变），但只持久化不可逆摘要；实验的可恢复匹配仍使用
不含 token 的 `request_semantics_sha256`。这样既保留请求与响应的精确绑定，也不让凭据进入
manifest、SQLite 文本字段或用户输出。

## 七接口冒烟矩阵

| 数据集 | 最小请求 | 必须证明 |
|---|---|---|
| `stock_basic` | SSE/SZSE 的 L/D/P/G 分片 | 分片存在、字段完整、代码唯一 |
| `trade_cal` | 一个短日期区间 | 沪深日历一致、开闭市覆盖完整 |
| `bak_basic` | 最近已完成交易日 | 历史名称与行业字段存在 |
| `daily` | 最近已完成交易日 | 原始 OHLCV、成交额、全市场集合 |
| `adj_factor` | 同一交易日 | 正有限因子，覆盖当日预期集合 |
| `stk_limit` | 同一交易日 | 合法上下限，覆盖当日预期集合 |
| `suspend_d` | 同一交易日 | 成功空响应与非空 S/R 均可证明 |

真实冒烟只用最近已完成的交易日，不触碰最终 OOS 划分，不运行策略指标。

## 错误处理

- DNS、TLS、超时、非 2xx、业务 code 非零、非法 JSON、重复 key、字段漂移均 fail closed。
- 429/5xx 按现有有界重试；认证/权限错误不重试。
- 禁止重定向。
- 响应包含 token 时拒绝持久化。
- 错误信息统一走 token redaction。
- 冒烟失败不发布 generation，不更新 active head。

## 测试策略

按 TDD 顺序实现：

1. SourceProfile 单元测试：固定 URL、host、env 和协议，拒绝未知 profile。
2. CLI RED：Jiaoch profile 不读取 `TUSHARE_TOKEN`，缺 `JIAOCH_TOKEN` 在建 store 前失败。
3. host/TLS RED：拒绝 HTTP、重定向、其他 host 和 CLI URL 覆盖。
4. secret RED：token 原文不进入 attempt、错误、manifest、stdout/stderr 或 request semantics；
   `wire_request_sha256` 仅允许保存不可逆摘要。
5. 协议 RED：`/{api_name}` POST 的路径和请求体与 canonical spec 一致。
6. 七接口合成响应集成测试：与 official profile 产生相同 normalized rows 和 audit root。
7. 真实只读权限冒烟：使用进程环境中的 token，报告仅包含接口状态、行数、字段 hash，
   不打印样本行或 token。
8. 相关测试、完整 pytest、Ruff、CLI 异常矩阵和 diff check。

## 非目标

- 不把 Jiaoch 设为日常推荐 provider。
- 不新增实时分钟、历史分钟、Level-2 或自动下单。
- 不运行策略搜索、walk-forward 或最终 OOS。
- 不修改 VPS 或部署配置。
- 不提交或推送 Git 变更。

## 后续阶段

本设计通过后，另行预注册“真实多年 v4 股票工件回填”实验。该实验完成并通过 coverage
audit 后，才恢复冻结开发基线、成本压力和 walk-forward；最终 OOS 仍保持封存。
