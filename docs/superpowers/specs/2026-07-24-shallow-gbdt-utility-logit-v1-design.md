# Shallow GBDT Utility-Logit OOF v1 设计

日期：2026-07-24

状态：预注册；尚未实现、尚未生成本模型的任何 OOF 分数或绩效

研究角色：development-only；失败即退休，成功也只允许预注册下一冻结阶段

## 1. 决策背景

`development-pit-cross-sectional-ranked-liquidity-ridge-oof/v2` 与 rolling-126 v3 已分别完成唯一一次正式 development 评估并失败。v2 检验扩展训练窗线性 Ridge，v3 只把训练窗改为最近 126 个 frozen sessions；两者均不得做结果后邻近参数修补。

本设计登记一个新的统计假设，而不是继续搜索 Ridge 的窗口、`lambda`、正分阈值或状态过滤：

> 在完全相同的 exact-PIT 股票池、十维价量/流动性特征、严格执行标签、126/63 outer folds、成本、退出和组合约束下，固定容量的浅层 GBDT 可以通过非线性交互改善横截面排序；收益绝对值加权的 logistic 目标可以用自然的 `p > 0.5` 零效用门决定是否参与，从而允许每天推荐 0 只。

本假设是在已知 v2/v3 development 失败后提出，必须作为追加 trial 计入选择路径；其 development 结果不是独立发现证明。PBO/DSR 只作为多重试验诊断，不能代替 embargo/final-OOS。

## 2. 文献依据与适用边界

文献事实：

- Gu、Kelly、Xiu 比较线性模型、树和神经网络后，将树/神经网络的主要预测增益归因于非线性与变量交互；重要信号包括动量、流动性和波动率。但其市场、频率和分散组合与本项目不同，不能直接外推收益目标。[RFS 原文](https://academic.oup.com/rfs/article/33/5/2223/5758276)
- Leippold、Wang、Zhou 的中国 A 股研究显示流动性、流动性波动、Amihud、趋势和波动特征具有预测价值，同时树模型优势对股票规模子样本并不稳定，交易成本会显著削弱结果。[JFE 开放原文](https://zibs.zju.edu.cn/_upload/article/files/21/78/16f84f524d7daebc5d2831d43ec9/d107b0a1-790d-4d4d-b87b-e168849235ca.pdf)
- Freyberger、Neuhierl、Weber 发现很多单变量关系在联合条件下不再增量显著，但横截面关系仍可能是非线性的。[NBER 原文](https://www.nber.org/system/files/working_papers/w23227/w23227.pdf)
- Learning-to-rank 研究说明“先预测收益再排序”并不等同于直接优化 Top-K；但现有证据多来自不同市场、long-short 或更分散组合，因此本轮不引入 LambdaMART/NDCG，以免同时改变模型和目标。[Poh 等](https://arxiv.org/abs/2012.07149)、[Zhang 等](https://arxiv.org/abs/2104.12484)、[Top-K 排名损失](https://marc.najork.org/papers/sigir2022-topk.pdf)
- Selective prediction 用 coverage 与 covered-domain risk 描述“预测或拒绝”的权衡；其标准保证依赖与金融时间序列不完全相容的分布假设，所以这里只借用“显式允许 abstain”的决策结构，不声称有限样本保证。[SelectiveNet](https://proceedings.mlr.press/v97/geifman19a.html)
- Cawley/Talbot 与 Varma/Simon 说明用同一有限样本同时选模型并报告性能会产生选择偏差；因此本轮不做 hyperparameter search、early stopping 或基于 outer-fold 绩效选模型。[JMLR](https://jmlr.org/papers/v11/cawley10a.html)、[BMC Bioinformatics](https://pubmed.ncbi.nlm.nih.gov/16504092/)
- PBO/CSCV 评估“样本内选优后样本外跌至中位数以下”的风险，DSR 校正多重试验与非正态带来的 Sharpe 膨胀；它们要求完整登记尝试，但均不能替代真正冻结的 OOS。[PBO](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)、[DSR](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)、[factor zoo 多重检验](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2249314)
- XGBoost 的 `hist` 是一次 sketch 后重复使用直方图的快速近似树算法；官方 wheel 支持 Windows 与现代 Linux，CPU-only 包可减小体积。[Tree Methods](https://xgboost.readthedocs.io/en/stable/treemethod.html)、[安装文档](https://xgboost.readthedocs.io/en/stable/install.html)

工程推断：

- 当前只有 483 个独立市场会话，横截面百万行不等于百万个独立市场状态；固定浅树比搜索深树、神经网络或排序损失更符合容量约束。
- 本轮的 `depth=2`、200 棵、`eta=0.05` 与 p99 截尾是预先选择的保守容量预算，不是文献证明的最优值。
- 文献不能证明本项目可达到 50% 年化或 15% 回撤；唯一有效结论来自后续严格 OOF 与冻结 OOS。

## 3. 不变的数据与执行契约

以下内容完全继承 rolling-126 v3，不得修改：

- audited PIT universe、coverage/root/temporal/security-transition 输入哈希；
- `2024-07-05..2026-07-03` 共 483 个 exact-PIT development sessions；
- 仅沪深主板与创业板，排除科创板、北交所、ST/退市整理及不满足上市/停牌契约的样本；
- 十维固定特征及其同日 exact-PIT 横截面 mid-rank；
- 126 个训练 sessions、连续不重叠 `63×5 + 42` outer validation folds；
- 训练标签必须是完整 outcome 且 `exit_date < validation_start`；
- 次日开盘执行、5-session 持有/既有止损与退出管线；
- 往返成本 25 bps、单边滑点各 10 bps，总标签成本 45 bps；
- 每日最多 Top 3、最多 3 个活动仓位、每行业最多 1 仓；
- slot-daily 资本模型、1× 暴露及全部现有原始 float64 晋级门槛。

每折训练窗口不得因缺失标签向 126 sessions 之外回填，不得看 validation 标签决定任何模型参数。

## 4. 标签、截尾与日级权重

严格执行后的净收益百分点为：

\[
r^{net}_{it}=r^{gross}_{it}-0.45.
\]

在每个 outer fold 的合格训练记录内，对 \(|r^{net}|\) 升序排列。训练记录数 \(N_f\) 必须大于零，否则 fail-closed。固定零基数组索引：

\[
k_f=\lceil0.99N_f\rceil-1,\qquad
c_f=\operatorname{sorted\_abs\_returns\_zero\_based}[k_f].
\]

不使用插值分位数。对称截尾：

\[
\tilde r_{it}=\operatorname{sign}(r^{net}_{it})
\min(|r^{net}_{it}|,c_f).
\]

对每个训练信号日 \(t\)：

\[
S_t=\sum_{j\in I_t}|\tilde r_{jt}|.
\]

`S_t` 非有限或不大于零时 fail-closed。方向标签与样本权重：

\[
y_{it}=\mathbf 1[\tilde r_{it}>0],\qquad
w_{it}=\frac{|\tilde r_{it}|}{S_t}.
\]

因此每个信号日的总权重为 1。零收益记录权重为 0，但仍保留在成员收据中。定义日归一化效用：

\[
u_{it}=\frac{\tilde r_{it}}{S_t}.
\]

当 \(E[w\mid x]>0\) 且不限制函数类、正则化和 boosting 轮数时，加权 logistic 总体风险最优点满足：

\[
p^*(x)=
\frac{E[w\,y\mid x]}{E[w\mid x]},
\qquad
p^*(x)>0.5\iff E[u\mid x]>0.
\]

实际固定 200 棵、带正则化的浅层 GBDT 只是该总体目标的模型近似；其 `p > 0.5` 是预注册的模型蕴含正效用门，不保证真实条件期望一定为正。该门也不代表未经截尾原始收益的无条件保证。

## 5. 唯一模型规格

依赖固定为 `xgboost==3.2.0`。正式 producer 必须把 Python、NumPy、Pandas、XGBoost 版本、XGBoost `build_info()`、模块哈希和模型参数纳入 producer binding。

实施前兼容性更正：预注册初稿写为 `xgboost==3.3.0`，但该发行版声明 `Requires-Python >=3.12`，而冻结项目运行时为 Python 3.11.5。本更正在安装任何 XGBoost、训练任何模型或生成任何 OOF 分数前完成，只把依赖改为当时可安装的最新 Python 3.11 兼容版 `3.2.0`；模型公式、参数、数据、分折、门槛和一次性证伪规则均不变。

使用 XGBoost 原生 `train`/`DMatrix` 接口，参数唯一固定为：

```json
{
  "objective": "binary:logistic",
  "booster": "gbtree",
  "tree_method": "hist",
  "device": "cpu",
  "max_depth": 2,
  "eta": 0.05,
  "min_child_weight": 4.0,
  "gamma": 0.0,
  "subsample": 1.0,
  "colsample_bytree": 1.0,
  "colsample_bylevel": 1.0,
  "colsample_bynode": 1.0,
  "reg_lambda": 1.0,
  "reg_alpha": 0.0,
  "max_bin": 256,
  "grow_policy": "depthwise",
  "base_score": 0.5,
  "seed": 20260724,
  "nthread": 1,
  "validate_parameters": true
}
```

固定 `num_boost_round=200`。禁止：

- early stopping；
- validation metric 选轮数；
- depth、rounds、eta、p99、权重、阈值或 objective 搜索；
- RF、DART、神经网络、LambdaMART 或第二个模型并行竞赛；
- 对十维特征做新选择、交互枚举或结果后消融；
- GPU 正式训练。

十维树输入不标准化；缺失、NaN 或无穷值 fail-closed。

训练与预测的 `DMatrix` 构造同样冻结：

- 输入容器必须是 C-contiguous `numpy.ndarray`；
- 十维特征值与标签使用 `float64`，权重使用 `float64`；
- feature names 与既有十维 `FEATURE_NAMES` 顺序完全一致；
- feature types 固定为十个 `"float"`；
- `nthread=1`；
- `missing=np.nan`，但上游有限性检查必须保证实际没有 missing；
- `enable_categorical=False`；
- 禁止静默稀疏化、列重排、自动类别推断或其他数据适配器。

输入 shape、dtype、C-contiguous 标记、feature names/types、DMatrix 行列数及有效构造参数必须进入 fold receipt。

## 6. OOF 打分、弃权与选择

每个 validation candidate 输出：

\[
p_{it}=\sigma(F_f(x_{it})).
\]

只有严格 `p > 0.5` 的候选进入固定正效用池。`p == 0.5` 必须拒绝，不能用浮点容差放行。

同一信号日排序固定为：

1. `p` 降序；
2. 信号日成交额降序；
3. 稳定 `security_id` 升序。

随后使用既有 Top 3、活动仓位和行业 cap 状态机。当天没有 `p > 0.5` 候选时输出 0 只，不降低阈值补足三只。

成交额基线只在完全相同的 `p > 0.5` 候选池内按成交额排序；其绩效不是晋级门，evidence 完整性是晋级门。

## 7. 审计证据与 verifier

每折至少落盘并内容寻址：

- 126-session training window、validation sessions、eligible/purged/noncomplete/censored 成员根；
- p99 最近秩索引、`c_f` 原始 float64、逐日 `S_t` 根；
- 标签、权重、十维训练矩阵、训练行顺序与 DMatrix 输入根；
- 完整参数、round count、XGBoost config/model JSON/raw bytes 哈希；
- validation 特征、raw margin、概率、正效用池及逐候选 payload 哈希；
- 主策略和成交额基线 selection decisions、selected full evidence；
- 严格 outcome、尾端删失、成本、退出、日级权益与所有 raw metrics。

post-write verifier 必须重新读取主 artifact 与 sidecar，重算：

- content-addressed 文件名、内嵌哈希和 canonical SHA；
- 固定 upstream、strategy、producer 与 scope；
- 六折窗口、purge、p99、每日权重和模型输入；
- 同一 pinned XGBoost CPU 单线程环境下的模型重放、margin/probability hashes；
- `p > 0.5` 成员、排序、Top 3/仓位/行业 cap；
- 主/基线交易、滚动窗口和 advancement gate。

任何不一致 fail-closed。模型库本身不被宣称为独立数学 oracle；证据强度来自固定版本/build、完整输入与参数、确定性重放、输出根和独立交易/指标重算。

## 8. 唯一晋级门槛

不放宽现有门槛：

- 完整选中交易数 `>=20`；
- 全 development 胜率 `>=52%`；
- 全 development 最大回撤绝对值 `<=15%`；
- 全 development PF `>=1.3`；
- 最新完整 365 日净收益 `>=50%`；
- 最新完整 365 日 Calmar `>=1.5`；
- 每个完整 365 日 audited-session 窗口同时满足收益 `>=50%`、回撤绝对值 `<=15%`、payoff `>=1.3`、PF `>=1.3`、Calmar `>=1.5`；
- 主策略和同候选成交额基线 evidence 均完整；
- 全部比较使用未四舍五入 raw float64。

任一失败即令 `advancement_gate_passed=false` 并永久退休本 v1。全部通过也只允许预注册下一冻结 embargo/OOS；不得直接注册生产 profile、修改生产门槛或发布 VPS 推荐。

## 9. 实现顺序与技术中止

1. 先提交本预注册设计和 trial-ledger 条目。
2. 安装并锁定 XGBoost 依赖；验证 Windows 研究机和 VPS 目标运行时的版本/build 可记录。
3. TDD 实现纯标签/权重、模型包装、fold OOF、正效用池、证据与 verifier。
4. 使用合成数据和不含真实 forward 绩效的固定小样本完成独立重放、篡改测试和 CPU 基准。
5. 交叉审核与全量测试通过后提交 producer。
6. 只启动一次全新输出目录的 frozen-v2 development 正式运行。

若依赖、确定性重放、内存、schema 或 producer binding 在任何新模型分数生成前失败，可登记为 `technical_abort_pre_score_pre_performance`，修复后技术重放不计新的统计结果观察；一旦生成任何真实 OOF 分数或绩效，后续变化必须成为新的追加 trial。

## 10. GPU 决策

本轮不使用 GPU。当前主机为 AMD GPU，现有 Python 环境没有 CUDA；模型只有 10 个特征、深度 2、200 棵树，CPU histogram 足够。GPU 不增加统计证据，并可能引入不同的量化、归约顺序和 build identity。

GPU 只允许在隔离的非 authority benchmark 中评估；不得读取 embargo/final-OOS，不得用 GPU 结果选择模型。只有 CPU 成为经测量的端到端主要瓶颈，且候选顺序、交易集合和指标可证明等价时，才可另立执行环境假设。
