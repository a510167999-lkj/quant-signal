# Ranked-liquidity rolling-126 OOF v3 预注册

日期：2026-07-24

状态：`preregistered_pre_implementation_pre_outcome`

## 决策

`development-pit-cross-sectional-ranked-liquidity-ridge-oof/v2` 已在 frozen-v2 development 区间完成唯一一次正式 OOF 评估并失败。v3 只检验一个新假设：

> 在收益预测关系非平稳时，只使用验证折开始前最近 126 个 frozen market session 的成熟训练标签，可能比 expanding history 更能跟踪近期关系。

唯一允许的策略变化是：

```text
expanding training prefix
→ trailing 126 frozen market sessions
```

以下内容全部保持 v2 不变：

- exact-PIT 股票池、稳定证券身份与主板/创业板范围；
- 明确排除科创板、北交所、ST、退市整理和不满足上市日期契约的样本；
- 483 个 frozen-v2 development session；
- 61-session 特征历史、8 个横截面 ranked liquidity/risk 特征和 2 个市场上下文特征；
- `gross_return_pct - 0.45` 连续标签；
- 按信号日等权、训练折内标准化、带截距 Ridge、`lambda=1.0`、平方损失；
- 六个连续不重叠验证折，几何仍为 `63 × 5 + 42`；
- `exit_date < validation_start` 的严格结果成熟 purge；
- 次日开盘可执行性、5-session 持有、收盘触发 5% 止损、阻塞卖出重试；
- 25 bps 往返成本、单边各 10 bps 滑点、8% 年化融资成本；
- `predicted_net_return_pct > 0`、Top 3、最多 3 个活跃仓位、每行业最多 1 个；
- amount baseline、slot-daily 资本模型、全部滚动 365 日指标与所有晋级门槛；
- `development_only=true`，不读取 embargo/final-OOS，不注册生产 profile，不生成 VPS 实盘推荐。

不得在同一 v3 中改变 `lambda`、特征、市场状态阈值、选股阈值、持有期、止损、仓位、成本或任何退出规则。

## 精确训练窗口

令 frozen session 序列为：

```text
S = [s_0, s_1, ..., s_482]
```

验证折仍从位置 126 开始，由既有 `_fold_ranges` 产生。对验证起点位于位置 `j` 的每一折：

```text
training_window_sessions = S[j-126:j]
training_window_start = S[j-126]
training_window_end = S[j-1]
```

训练记录必须同时满足：

```text
training_window_start <= signal_date <= training_window_end
AND outcome is complete
AND exit_date < validation_start
```

边界语义：

- 窗口按 frozen market `signal_date` session 定义，不按行数、候选数或 `exit_date` 定义。
- 126 个 session 是完整日历窗口；某日没有合格训练行时不得用更早日期补齐。
- 窗口末端仍执行结果成熟 purge，因此实际训练 signal-date 数可以少于 126。
- 更早行情可以作为窗口内样本计算 61-session 特征的历史输入，但更早信号的标签不得进入模型拟合。
- 第一折与 v2 使用相同的前 126-session 训练窗口；第二折及以后才排除窗口外旧标签。
- 不尝试 63、189、252 或其他窗口，也不根据 v3 结果改窗口。

## 模型与 OOF 契约

v3 schema 和 signal tag 必须独立于 v2，建议固定为：

```text
schema_version =
  development-pit-cross-sectional-ranked-liquidity-ridge-rolling-oof/v3

signal_tag =
  cross_sectional_ranked_liquidity_ridge_rolling_126_oof
```

模型仍是确定性 float64 线性 Ridge。每折的均值、标准差、截距、系数和预测分数只允许由该折窗口内的成熟训练记录生成。禁止：

- 用验证折或更晚日期选择窗口；
- 用组合收益、Sharpe、回撤、PF、Calmar 或 Top-3 结果选择任何参数；
- 在同一 outer OOF 上比较多个窗口后只发布最佳窗口；
- 删除弱市训练日、增加结果后 breadth/return gate，或把 v2 的后段状态诊断编码为阈值；
- 对失败折重训、重采样、换损失函数或换模型类别。

## 审计与内容寻址

v3 必须保留 v2 的完整 feature、entry、outcome、OOF、selection 和 producer 证据，并提升所有受影响 schema。每个 fold receipt 除现有字段外，至少新增：

```text
training_window_type = trailing_frozen_signal_sessions
training_window_session_count = 126
training_window_start
training_window_end
training_window_sessions_sha256
eligible_window_training_candidate_count
purged_immature_candidate_count
```

现有字段继续保留：

```text
training_candidate_count
training_signal_date_count
training_last_exit_date
training_candidate_keys_sha256
training_rows_sha256
model_sha256
score_rows_sha256
```

独立 replay verifier 必须证明：

1. 窗口 session 恰好等于验证起点前 126 个 frozen session；
2. 窗口外旧标签不影响该折模型与分数；
3. 窗口内成熟标签会影响模型；
4. `exit_date >= validation_start` 的窗口内标签仍被 purge；
5. 六折边界与 v2 完全一致；
6. strategy hash、producer root、主 artifact 与 sidecar 均内容寻址；
7. 主策略和 amount baseline 共享同一 OOF 正分候选池；
8. 所有 selected evidence 中不存在科创板或北交所证券。

## 正式评估与停止规则

完成 TDD、交叉审核、全量测试并提交 producer 代码后，才允许启动一次正式 frozen-v2 development 评估。建议输出目录：

```text
data/research_runs/
  audited_pit_ranked_liquidity_ridge_rolling126_oof_v3_development_1
```

统计结果一旦产生，必须完整追加到 trial ledger，不论成功、失败或与 v2 相同。

晋级仍要求同时满足：

- 完整交易数 `>= 20`；
- 全 development 胜率 `>= 52%`；
- 全 development 最大回撤绝对值 `<= 15%`；
- 全 development Profit Factor `>= 1.3`；
- 最新完整 365 日净收益 `>= 50%`；
- 最新完整 365 日 Calmar `>= 1.5`；
- 每一个完整滚动 365 日窗口同时满足收益、回撤、payoff、PF、Calmar 五项原门槛；
- 主策略和 amount baseline 证据完整、无选中右删失。

若任一硬门槛失败：

- `advancement_gate_passed=false`；
- v3 立即退休；
- 不试相邻窗口，不调 `lambda`，不追加 market breadth/median-return gate；
- 不打开 embargo/final-OOS；
- 不注册 profile，不改生产门槛，不发布股票建议。

即使 development 全部通过，也只能请求下一阶段的既有 embargo/final-OOS 流程；不能直接声称实现年化 50% 或投入生产。

## 文献审查

### 支持有限记忆假设

1. [Paye & Timmermann, *Instability of Return Prediction Models*](https://rady.ucsd.edu/_files/faculty-research/timmermann/instability.pdf)：多国股票收益预测关系存在广泛结构不稳定，说明 expanding history 可能混合不同关系。
2. [Giacomini & White, *Tests of Conditional Predictive Ability*](https://economia.uc3m.es/jgonzalo/teaching/PhdTimeSeries/GiacominiWhite.pdf)：在模型错设或异质性未建模时，有限记忆估计可作为局部近似；rolling 与 expanding 的优劣应视作可检验问题。
3. [Inoue, Jin & Rossi, *Rolling Window Selection for Out-of-Sample Forecasting with Time-Varying Parameters*](https://crei.cat/wp-content/uploads/users/pages/InoueLuRossi.pdf)：时变参数下训练窗口会影响预测误差，窗口选择本身属于模型设计。
4. [Capponi et al., *The Nonstationarity-Complexity Tradeoff in Return Prediction*](https://arxiv.org/abs/2512.23596)：收益预测存在模型复杂度、估计方差和非平稳性之间的权衡；该文支持研究窗口效应，但不证明 126 是最优值。

### 限制与反证风险

5. [Pesaran & Timmermann, *Selection of Estimation Window in the Presence of Breaks*](https://rady.ucsd.edu/_files/faculty-research/timmermann/estimation-window.pdf)：即使存在断点，保留断点前数据仍可能降低方差；无断点时 expanding 更有效，不能把 rolling 当作必然改进。
6. [Rossi & Inoue, *Out-of-Sample Forecast Tests Robust to the Choice of Window Size*](https://crei.cat/wp-content/uploads/users/working-papers/rossi_outofsample.pdf)：试多个窗口再择优会形成 data snooping；因此 v3 只允许预注册的 126。
7. [Leippold, Wang & Zhou, *Machine Learning in the Chinese Stock Market*](https://doi.org/10.1016/j.jfineco.2021.08.017)：A 股关系可能受结构变化影响，且流动性特征重要；但该研究使用 expanding training，不能作为 rolling-126 的直接实证保证。
8. [Cawley & Talbot, *On Over-fitting in Model Selection and Subsequent Selection Bias in Performance Evaluation*](https://www.jmlr.org/papers/v11/cawley10a.html)：有限样本中模型选择准则本身会被过拟合，禁止在同一 OOF 上搜索窗口和报告最优值。
9. [Arnott, Harvey & Markowitz, *A Backtesting Protocol in the Era of Machine Learning*](https://people.duke.edu/~charvey/Research/Published_Papers/SSRN-id3275654.pdf)：金融数据有效独立样本有限，理论先验、追加式试验账本和真正独立 OOS 都不可省略。
10. [Bailey et al., *The Probability of Backtest Overfitting*](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf) 与 [Bailey & López de Prado, *The Deflated Sharpe Ratio*](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)：重复试验需计入多重选择诊断；PBO/DSR 不能替代 embargo/final-OOS。

### 本轮未采用的替代假设

11. [Zaremba et al., *Herding for Profits: Market Breadth and the Cross-Section of Global Equity Returns*](https://www.sciencedirect.com/science/article/pii/S0264999319312982) 支持市场宽度可包含未来收益信息，但其月度 advance/decline 定义不等于本项目的日频 MA20 breadth。
12. [Campbell & Thompson, *Predicting Excess Stock Returns Out of Sample*](https://dash.harvard.edu/bitstream/handle/1/2622619/Campbell_Predicting.pdf) 支持对权益溢价预测施加经济约束，但不直接验证本项目应采用哪个 A 股状态弃权谓词。
13. [Moreira & Muir, *Volatility Managed Portfolios*](https://www.nber.org/papers/w22208) 与 [Daniel & Moskowitz, *Momentum Crashes*](https://www.nber.org/papers/w20439) 支持风险状态和波动率会改变策略表现，但不授权根据 v2 后段结果拼接 breadth、收益或波动阈值。
14. [Geifman & El-Yaniv, *Selective Classification for Deep Neural Networks*](https://proceedings.neurips.cc/paper/2017/file/4a8423d5e91fda00bb7e46540e2b0cf1-Paper.pdf) 说明弃权可形成风险—覆盖率权衡，但其分类保证不能直接移植为金融回撤保证。

综合判断：rolling-126 有可检验的理论动机，但证据强度不足以预言成功。它被选中是因为能复用 v2 已冻结的 126-session 最小训练长度，并把研究者自由度限制为一个；不是因为 126 已被证明最优，也不是为了事后“修复”某个折。
