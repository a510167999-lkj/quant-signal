const state = {
  market: "a",
  watchlist: [],
  scanResults: new Map(),
};
const RECOMMENDATION_POLL_BASE_DELAY_MS = 15_000;
const RECOMMENDATION_POLL_MAX_DELAY_MS = 120_000;
const recommendationPolling = {
  active: false,
  consecutiveFailures: 0,
  controller: null,
  requestSequence: 0,
  timer: null,
};
const HOT_INDUSTRY_WINDOWS = [
  { key: "1d", label: "当日" },
  { key: "3d", label: "3日" },
  { key: "5d", label: "5日" },
  { key: "10d", label: "10日" },
];

const els = {
  serviceStatus: document.querySelector("#serviceStatus"),
  lastRunValue: document.querySelector("#lastRunValue"),
  marketStateValue: document.querySelector("#marketStateValue"),
  hotIndustryValue: document.querySelector("#hotIndustryValue"),
  candidateValue: document.querySelector("#candidateValue"),
  qualifiedValue: document.querySelector("#qualifiedValue"),
  performanceWinValue: document.querySelector("#performanceWinValue"),
  performanceSampleValue: document.querySelector("#performanceSampleValue"),
  performanceReturnValue: document.querySelector("#performanceReturnValue"),
  performanceAdverseValue: document.querySelector("#performanceAdverseValue"),
  alertValue: document.querySelector("#alertValue"),
  holdingsStateValue: document.querySelector("#holdingsStateValue"),
  form: document.querySelector("#analyzeForm"),
  symbol: document.querySelector("#symbolInput"),
  adjust: document.querySelector("#adjustSelect"),
  lookback: document.querySelector("#lookbackSelect"),
  analyzeButton: document.querySelector("#analyzeButton"),
  scanButton: document.querySelector("#scanButton"),
  runRecommendationsButton: document.querySelector("#runRecommendationsButton"),
  monitorButton: document.querySelector("#monitorButton"),
  holdingsRefreshButton: document.querySelector("#holdingsRefreshButton"),
  holdingsMeta: document.querySelector("#holdingsMeta"),
  holdingsList: document.querySelector("#holdingsList"),
  hotIndustryMeta: document.querySelector("#hotIndustryMeta"),
  hotIndustriesList: document.querySelector("#hotIndustriesList"),
  recommendationMeta: document.querySelector("#recommendationMeta"),
  recommendationEvidence: document.querySelector("#recommendationEvidence"),
  bounceDailyHero: document.querySelector("#bounceDailyHero"),
  bounceDailyStatus: document.querySelector("#bounceDailyStatus"),
  bounceDailyMeta: document.querySelector("#bounceDailyMeta"),
  bounceDailyHeadline: document.querySelector("#bounceDailyHeadline"),
  bounceDailyDetail: document.querySelector("#bounceDailyDetail"),
  bounceDailyList: document.querySelector("#bounceDailyList"),
  bounceFactSignal: document.querySelector("#bounceFactSignal"),
  bounceFactAction: document.querySelector("#bounceFactAction"),
  bounceFactExt: document.querySelector("#bounceFactExt"),
  bounceFactAsOf: document.querySelector("#bounceFactAsOf"),
  bounceFactOos: document.querySelector("#bounceFactOos"),
  bounceFactDue: document.querySelector("#bounceFactDue"),
  analyzeWorkspace: document.querySelector("#analyzeWorkspace"),
  recommendationsList: document.querySelector("#recommendationsList"),
  performanceMeta: document.querySelector("#performanceMeta"),
  performanceList: document.querySelector("#performanceList"),
  alertMeta: document.querySelector("#alertMeta"),
  alertsList: document.querySelector("#alertsList"),
  watchlist: document.querySelector("#watchlist"),
  watchForm: document.querySelector("#watchForm"),
  watchSymbol: document.querySelector("#watchSymbol"),
  watchMarket: document.querySelector("#watchMarket"),
  watchName: document.querySelector("#watchName"),
  resultTitle: document.querySelector("#resultTitle"),
  resultMeta: document.querySelector("#resultMeta"),
  actionBadge: document.querySelector("#actionBadge"),
  lastClose: document.querySelector("#lastClose"),
  scoreValue: document.querySelector("#scoreValue"),
  confidenceValue: document.querySelector("#confidenceValue"),
  stopLoss: document.querySelector("#stopLoss"),
  entryZone: document.querySelector("#entryZone"),
  supportLevel: document.querySelector("#supportLevel"),
  resistanceLevel: document.querySelector("#resistanceLevel"),
  takeProfit: document.querySelector("#takeProfit"),
  shortPlanHorizon: document.querySelector("#shortPlanHorizon"),
  shortPlanAction: document.querySelector("#shortPlanAction"),
  shortPlanStop: document.querySelector("#shortPlanStop"),
  shortPlanTarget: document.querySelector("#shortPlanTarget"),
  shortPlanList: document.querySelector("#shortPlanList"),
  longPlanHorizon: document.querySelector("#longPlanHorizon"),
  longPlanAction: document.querySelector("#longPlanAction"),
  longPlanZone: document.querySelector("#longPlanZone"),
  longPlanStop: document.querySelector("#longPlanStop"),
  longPlanList: document.querySelector("#longPlanList"),
  reasonList: document.querySelector("#reasonList"),
  riskList: document.querySelector("#riskList"),
  btStrategy: document.querySelector("#btStrategy"),
  btHold: document.querySelector("#btHold"),
  btDrawdown: document.querySelector("#btDrawdown"),
  btTrades: document.querySelector("#btTrades"),
  chart: document.querySelector("#priceChart"),
};

function fmt(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toFixed(digits);
}

function fmtPct(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const sign = Number(value) > 0 ? "+" : "";
  return `${sign}${Number(value).toFixed(2)}%`;
}

function fmtAmount(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  const number = Number(value);
  if (Math.abs(number) >= 100000000) return `${fmt(number / 100000000, 2)}亿`;
  if (Math.abs(number) >= 10000) return `${fmt(number / 10000, 1)}万`;
  return fmt(number, 0);
}

function safeToken(value, allowed, fallback) {
  const token = String(value || "").toLowerCase();
  return allowed.includes(token) ? token : fallback;
}

function clear(element) {
  element.replaceChildren();
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = String(text);
  return element;
}

function stack(...children) {
  const wrapper = node("span");
  children.forEach((child) => wrapper.appendChild(child));
  return wrapper;
}

function safeApiErrorMessage(body, status) {
  const candidate =
    typeof body?.error?.message === "string"
      ? body.error.message
      : typeof body?.detail === "string"
      ? body.detail
      : "";
  const message = candidate.trim().slice(0, 240);
  return message || `HTTP ${status}`;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  });
  const isJson = response.headers.get("content-type")?.includes("application/json");
  const body = isJson ? await response.json() : await response.text();
  if (!response.ok) {
    const error = new Error(safeApiErrorMessage(body, response.status));
    error.httpStatus = response.status;
    throw error;
  }
  return body;
}

function clearRecommendationPollTimer() {
  if (recommendationPolling.timer !== null) {
    window.clearTimeout(recommendationPolling.timer);
  }
  recommendationPolling.timer = null;
  window.__recommendationPollTimer = null;
}

function stopRecommendationPolling({ abort = true } = {}) {
  clearRecommendationPollTimer();
  recommendationPolling.active = false;
  recommendationPolling.consecutiveFailures = 0;
  recommendationPolling.requestSequence += 1;
  if (abort && recommendationPolling.controller) {
    recommendationPolling.controller.abort();
  }
  recommendationPolling.controller = null;
}

function recommendationRetryDelay(failureCount) {
  const exponent = Math.max(0, Number(failureCount || 1) - 1);
  const exponentialDelay = Math.min(
    RECOMMENDATION_POLL_MAX_DELAY_MS,
    RECOMMENDATION_POLL_BASE_DELAY_MS * 2 ** exponent,
  );
  const randomSource =
    typeof window.__recommendationPollRandom === "function"
      ? window.__recommendationPollRandom
      : Math.random;
  const randomValue = Math.max(0, Math.min(1, Number(randomSource())));
  const jitteredDelay = Math.round(exponentialDelay * (0.8 + randomValue * 0.4));
  return Math.min(RECOMMENDATION_POLL_MAX_DELAY_MS, jitteredDelay);
}

function isRetryableRecommendationError(error) {
  const status = Number(error?.httpStatus);
  if (!Number.isFinite(status)) return true;
  return status === 408 || status === 429 || (status >= 500 && status <= 599);
}

function scheduleRecommendationPoll(delay) {
  if (!recommendationPolling.active) return;
  clearRecommendationPollTimer();
  recommendationPolling.timer = window.setTimeout(() => {
    recommendationPolling.timer = null;
    window.__recommendationPollTimer = null;
    void loadRecommendations();
  }, delay);
  window.__recommendationPollTimer = recommendationPolling.timer;
}

async function requestRecommendationSnapshot(path, options = {}) {
  recommendationPolling.active = true;
  clearRecommendationPollTimer();
  const requestSequence = recommendationPolling.requestSequence + 1;
  recommendationPolling.requestSequence = requestSequence;
  if (recommendationPolling.controller) {
    recommendationPolling.controller.abort();
  }
  const controller = new AbortController();
  recommendationPolling.controller = controller;

  try {
    const data = await api(path, { ...options, signal: controller.signal });
    if (requestSequence !== recommendationPolling.requestSequence) return null;

    recommendationPolling.consecutiveFailures = 0;
    renderRecommendations(data);
    if (data?.summary?.running) {
      scheduleRecommendationPoll(RECOMMENDATION_POLL_BASE_DELAY_MS);
    } else {
      stopRecommendationPolling({ abort: false });
    }
    return data;
  } catch (error) {
    if (requestSequence !== recommendationPolling.requestSequence || controller.signal.aborted) {
      return null;
    }
    if (!isRetryableRecommendationError(error)) {
      const message = typeof error?.message === "string" ? error.message : "请求失败";
      stopRecommendationPolling({ abort: false });
      els.recommendationMeta.textContent = message;
      return null;
    }
    recommendationPolling.consecutiveFailures += 1;
    const delay = recommendationRetryDelay(recommendationPolling.consecutiveFailures);
    els.recommendationMeta.textContent = `网络请求失败，${Math.ceil(delay / 1000)} 秒后自动重试`;
    scheduleRecommendationPoll(delay);
    return null;
  } finally {
    if (
      requestSequence === recommendationPolling.requestSequence &&
      recommendationPolling.controller === controller
    ) {
      recommendationPolling.controller = null;
    }
  }
}

function setBusy(isBusy) {
  els.analyzeButton.disabled = isBusy;
  els.analyzeButton.textContent = isBusy ? "分析中" : "分析";
}

function setStatus(text, ok = true) {
  els.serviceStatus.textContent = text;
  els.serviceStatus.dataset.state = ok ? "ok" : "bad";
}

function setMarket(market) {
  state.market = market;
  document.querySelectorAll(".segment-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.market === market);
  });
}

function renderList(element, items, mode = "reason") {
  element.classList.toggle("risk", mode === "risk");
  clear(element);
  const list = items && items.length ? items : ["暂无明显信号。"];
  list.forEach((item) => element.appendChild(node("li", "", item)));
}

function actionClass(action) {
  return safeToken(action, ["buy", "watch", "hold", "reduce", "sell"], "neutral");
}

function compactReason(item) {
  const reasons = item.reasons || [];
  return reasons.length ? reasons[0] : "信号达到候选标准。";
}

function recommendationMetaLine(item) {
  const parts = [item.action_label, `评分 ${fmt(item.score, 2)}`];
  if (item.market_data_source) {
    parts.push(`行情 ${item.market_data_source}`);
  }
  if (item.industry?.industry) {
    parts.push(`${item.industry.industry} #${item.industry.industry_rank}`);
  }
  if (item.strategy_quality) {
    parts.push(`胜率 ${fmt(item.strategy_quality.win_rate_pct, 1)}%`);
  }
  if (item.market_context?.label) {
    parts.push(item.market_context.label);
  }
  if (item.news_context?.level && item.news_context.level !== "neutral") {
    parts.push(`消息 ${item.news_context.level}`);
  } else if (item.news_context?.article_count) {
    parts.push(`消息 ${item.news_context.article_count}条`);
  }
  if (item.announcement_context?.level && item.announcement_context.level !== "neutral") {
    parts.push(`公告 ${item.announcement_context.level}`);
  } else if (item.announcement_context?.announcement_count) {
    parts.push(`公告 ${item.announcement_context.announcement_count}条`);
  }
  if (item.announcement_context?.source && item.announcement_context.source !== "unknown") {
    const fallback = item.announcement_context?.fallback_used ? "（回退）" : "";
    parts.push(`公告源 ${item.announcement_context.source}${fallback}`);
  }
  if (item.fund_flow_context?.level && item.fund_flow_context.level !== "neutral") {
    parts.push(`资金 ${item.fund_flow_context.level}`);
  }
  return parts.filter(Boolean).join(" · ");
}

function recommendationAdviceLine(item) {
  const advice = item.operation_advice || {};
  const zone = advice.entry_zone || item.entry_zone || {};
  const levels = item.levels || {};
  const plan = (item.trade_plans || {}).short_term || {};
  const parts = [];
  if (zone.low !== null && zone.low !== undefined && zone.high !== null && zone.high !== undefined) {
    parts.push(`入场 ${fmt(zone.low, 2)}-${fmt(zone.high, 2)}`);
  }
  if (levels.stop_loss !== null && levels.stop_loss !== undefined) {
    parts.push(`止损 ${fmt(levels.stop_loss, 2)}`);
  }
  if (levels.take_profit !== null && levels.take_profit !== undefined) {
    parts.push(`止盈 ${fmt(levels.take_profit, 2)}`);
  }
  if (advice.holding_period || plan.horizon || plan.holding_period) {
    parts.push(`周期 ${advice.holding_period || plan.horizon || plan.holding_period}`);
  }
  const triggers = Array.isArray(advice.trigger_conditions) ? advice.trigger_conditions : [];
  if (triggers.length) {
    parts.push(`触发条件 ${triggers.join("；")}`);
  }
  const reduce = advice.take_profit_or_reduce || {};
  if (reduce.trigger_price !== null && reduce.trigger_price !== undefined) {
    parts.push(`止盈/减仓条件 价格达到 ${fmt(reduce.trigger_price, 2)}`);
  }
  if (advice.invalidation) {
    parts.push(`失效 ${advice.invalidation}`);
  }
  return parts.length ? parts.join(" · ") : "操作参数待人工核验。";
}

function updateRecommendationSummary(payload) {
  const summary = payload?.summary || {};
  const market = summary.market_context || {};
  const items = payload?.items || [];
  const hotIndustriesByWindow = summary.hot_industries_by_window || { "1d": summary.hot_industries || [] };
  const hotIndustries = hotIndustriesByWindow["1d"] || [];
  const marketLabel = market.label || "--";
  els.marketStateValue.textContent = marketLabel;
  els.hotIndustryValue.textContent = hotIndustries[0]?.name || "--";
  els.candidateValue.textContent = summary.candidate_count ?? "--";
  els.qualifiedValue.textContent = summary.returned_count ?? items.length ?? "--";
  els.lastRunValue.textContent = payload?.generated_at
    ? `${payload.target_trade_date || payload.trade_date || ""} · ${summary.running ? "扫描中" : "已生成"}`
    : "等待交易日扫描";
}

function renderHotIndustries(payload) {
  const summary = payload?.summary || {};
  const hotIndustriesByWindow = summary.hot_industries_by_window || { "1d": summary.hot_industries || [] };
  const windowCount = HOT_INDUSTRY_WINDOWS.filter((window) => (hotIndustriesByWindow[window.key] || []).length).length;
  const slotLabel = payload?.run_slot_label || summary.run_slot?.label || "";
  els.hotIndustryMeta.textContent = windowCount
    ? `${slotLabel || payload?.trade_date || "最新"} · ${windowCount} 个周期`
    : "等待板块强度扫描";
  clear(els.hotIndustriesList);
  if (!windowCount) {
    els.hotIndustriesList.appendChild(node("div", "hot-industry-item empty-row", "暂无板块强度数据。"));
    return;
  }

  HOT_INDUSTRY_WINDOWS.forEach((window) => {
    const industries = hotIndustriesByWindow[window.key] || [];
    const group = node("div", "hot-industry-window");
    const groupHead = node("div", "hot-industry-window-head");
    groupHead.appendChild(node("span", "hot-industry-window-title", window.label));
    groupHead.appendChild(node("span", "hot-industry-window-count", industries.length ? `Top ${industries.length}` : "--"));
    group.appendChild(groupHead);

    const list = node("div", "hot-industry-window-list");
    if (!industries.length) {
      list.appendChild(node("div", "hot-industry-item empty-row", "暂无数据"));
    } else {
      industries.slice(0, 3).forEach((industry) => {
        const row = node("div", "hot-industry-item");
        const returnPct = industry.return_pct ?? industry.change_pct;
        const returnClass = Number(returnPct) >= 0 ? "positive" : "negative";
        row.appendChild(
          stack(
            node("span", "hot-industry-name", industry.name || "--"),
            node(
              "span",
              "hot-industry-subtitle",
              `候选 ${industry.candidate_count ?? 0} 只 · 换手 ${
                industry.turnover === null || industry.turnover === undefined ? "--" : `${fmt(industry.turnover, 2)}%`
              }`,
            ),
          )
        );
        const score = node("span", "hot-industry-score");
        score.appendChild(node("span", "hot-industry-rank", `#${industry.rank ?? "--"}`));
        score.appendChild(node("strong", returnClass, fmtPct(returnPct)));
        row.appendChild(score);
        list.appendChild(row);
      });
    }
    group.appendChild(list);
    els.hotIndustriesList.appendChild(group);
  });
}

function renderRecommendations(payload) {
  if (!els.recommendationsList) {
    return;
  }
  const rawItems = Array.isArray(payload?.items) ? payload.items : [];
  const summary = payload?.summary || {};
  const marketLabel = summary.market_context?.label ? ` · ${summary.market_context.label}` : "";
  const targetDate = payload?.target_trade_date || payload?.trade_date || "--";
  const dataAsOf = payload?.data_as_of ? ` · 数据截至 ${payload.data_as_of}` : "";
  const generatedAt = payload?.generated_at ? payload.generated_at.replace("T", " ").slice(0, 16) : "--";
  const slotLabel = payload?.run_slot_label || summary.run_slot?.label || "";
  const profileGate = payload?.profile_gate || summary.profile_gate || {};
  const currentPoolGate = payload?.current_pool_gate || summary.current_pool_gate || {};
  const profileId = payload?.strategy_profile?.profile_id || summary.strategy_profile?.profile_id || "";
  const blockedByProfileGate =
    payload?.recommendation_status === "blocked_profile_gate" ||
    (profileGate.enabled === true && profileGate.development_ready !== true);
  const blockedByCurrentPoolGate =
    payload?.recommendation_status === "blocked_current_pool_gate" ||
    currentPoolGate.passed !== true ||
    currentPoolGate.production_recommendation_eligible !== true;
  const oversizedSnapshot = rawItems.length > 3;
  // Never render stale/oversized items under a blocked or malformed snapshot.
  const items = blockedByCurrentPoolGate || blockedByProfileGate || oversizedSnapshot ? [] : rawItems.slice(0, 3);
  updateRecommendationSummary(payload);
  renderHotIndustries(payload);
  els.recommendationMeta.textContent = summary.running
    ? `扫描进行中 · 深扫 ${summary.max_deep ?? "--"}`
    : payload?.generated_at
    ? `目标交易日 ${targetDate}${dataAsOf} · 生成 ${generatedAt}${slotLabel ? ` · ${slotLabel}` : ""} · 候选 ${summary.candidate_count ?? "--"} · 入选 ${summary.returned_count ?? items.length}${marketLabel}`
    : "暂无推荐结果";
  if (els.recommendationEvidence) {
    const developmentOnly =
      payload?.evidence_scope === "development_only" ||
      (payload?.evidence_scope !== "live_proven" && payload?.live_proof !== true);
    els.recommendationEvidence.textContent = blockedByCurrentPoolGate
      ? "今日不推荐 · 股票池审计门禁未通过或仅有研究证据 · 不自动下单"
      : blockedByProfileGate
      ? `今日不推荐 · 策略证据/生产健康门槛未通过${profileId ? ` · ${profileId}` : ""}${profileGate.receipt_status ? ` · 证据 ${profileGate.receipt_status}` : ""} · 不自动下单`
      : developmentOnly
      ? `研究开发候选${profileId ? ` · ${profileId}` : ""} · 数据/回测不等于实盘证明 · 不自动下单`
      : "请人工核验数据新鲜度与实盘可用性";
  }

  clear(els.recommendationsList);
  if (!items.length) {
    const emptyMessage = oversizedSnapshot
      ? "今日不展示：推荐快照超过 3 只上限，需重新生成。"
      : blockedByCurrentPoolGate
      ? `今日不推荐：${(currentPoolGate.reasons || []).slice(0, 3).join("、") || "股票池审计门禁未通过"}。`
      : blockedByProfileGate
      ? `今日不推荐：${(profileGate.reasons || []).slice(0, 3).join("、") || "证据门槛未通过"}。`
      : summary.skipped
      ? "非交易日已跳过。"
      : summary.selection_funnel?.explanation || "暂无达到阈值的推荐。";
    const empty = node("div", "recommendation-item empty-row", emptyMessage);
    els.recommendationsList.appendChild(empty);
    return;
  }

  items.forEach((item, index) => {
    const row = node("button", "recommendation-item");
    row.type = "button";
    row.appendChild(
      stack(
        node("span", "recommendation-title", `${item.name || ""} ${item.symbol}`.trim()),
        node("span", "recommendation-reason", recommendationMetaLine(item)),
        node("span", "recommendation-reason", recommendationAdviceLine(item)),
        node("span", "recommendation-reason", compactReason(item)),
      )
    );
    row.appendChild(node("span", "rank-badge", `#${index + 1}`));
    row.addEventListener("click", () => {
      els.symbol.value = item.symbol;
      setMarket("a");
      analyze(item.symbol, "a", item.name || null);
    });
    els.recommendationsList.appendChild(row);
  });
}

function renderAlerts(payload) {
  const items = payload?.items || [];
  els.alertValue.textContent = items.length || 0;
  els.alertMeta.textContent = items.length ? `最近 ${items.length} 条` : "暂无提醒";
  clear(els.alertsList);
  if (!items.length) {
    els.alertsList.appendChild(node("div", "alert-item empty-row", "最近没有卖出或失效提醒。"));
    return;
  }
  items.forEach((item) => {
    const severity = safeToken(item.severity, ["critical", "warning", "info"], "warning");
    const row = node("div", `alert-item severity-${severity}`);
    row.appendChild(
      stack(
        node("span", "alert-title", `${item.title || "提醒"} · ${item.name || ""} ${item.symbol || ""}`.trim()),
        node("span", "alert-message", item.message || ""),
      )
    );
    row.appendChild(node("span", "mini-badge", item.event_type || "alert"));
    els.alertsList.appendChild(row);
  });
}

function renderPerformance(payload) {
  const summary = payload?.summary || {};
  const items = payload?.items || [];
  const winRate = summary.win_rate_10d_pct;
  els.performanceWinValue.textContent = winRate === null || winRate === undefined ? "--" : `${fmt(winRate, 1)}%`;
  els.performanceSampleValue.textContent = String(summary.matured_10d_count ?? 0);
  els.performanceReturnValue.textContent = fmtPct(summary.avg_return_10d_pct);
  els.performanceAdverseValue.textContent = fmtPct(summary.avg_adverse_10d_pct);
  els.performanceMeta.textContent =
    (summary.matured_10d_count || 0) > 0
      ? `已成熟 ${summary.matured_10d_count} 条 · 待成熟 ${summary.pending_10d_count ?? 0} 条`
      : "暂无足够成熟样本，继续按交易日积累";

  clear(els.performanceList);
  if (!items.length) {
    els.performanceList.appendChild(node("div", "performance-item empty-row", "还没有可验证的历史推荐。"));
    return;
  }
  items.slice(0, 8).forEach((item) => {
    const status = item.status === "matured_10d" ? fmtPct(item.return_10d_pct) : "待成熟";
    const row = node("div", "performance-item");
    row.appendChild(
      stack(
        node("span", "performance-title", `${item.name || ""} ${item.symbol || ""}`.trim()),
        node(
          "span",
          "performance-meta",
          `${item.signal_date || "--"} 入场 ${item.entry_date || "--"} · 1日 ${fmtPct(item.return_1d_pct)} · 3日 ${fmtPct(item.return_3d_pct)} · 5日 ${fmtPct(item.return_5d_pct)}`,
        ),
      )
    );
    row.appendChild(node("span", "performance-return", status));
    els.performanceList.appendChild(row);
  });
}

function holdingLevelClass(level) {
  return safeToken(level, ["ok", "info", "warning", "critical"], "ok");
}

function renderHoldings(payload) {
  const items = payload?.items || [];
  const warningCount = items.filter((item) => ["warning", "critical"].includes(item.status?.level)).length;
  els.holdingsStateValue.textContent = items.length ? (warningCount ? `${warningCount}警` : `${items.length}正常`) : "--";
  const l1 = payload?.l1_quote || {};
  els.holdingsMeta.textContent = payload?.updated_at
    ? `更新 ${payload.updated_at.slice(11, 19)} · L1 ${l1.quote_count ?? 0}/${items.length}`
    : "等待持仓快照";

  clear(els.holdingsList);
  if (!items.length) {
    els.holdingsList.appendChild(node("div", "holding-item empty-row", "暂无持仓。"));
    return;
  }

  items.forEach((item) => {
    const status = item.status || {};
    const row = node("button", `holding-item holding-${holdingLevelClass(status.level)}`);
    row.type = "button";
    const head = node("div", "holding-head");
    head.appendChild(
      stack(
        node("span", "holding-title", `${item.name || ""} ${item.symbol}`.trim()),
        node("span", "holding-subtitle", `${status.label || "--"} · ${item.action_label || "--"} · 评分 ${fmt(item.score, 1)}`),
      )
    );
    head.appendChild(node("span", "holding-price", fmt(item.current_price, 3)));

    const metrics = node("div", "holding-metrics");
    metrics.appendChild(stack(node("span", "", "涨跌"), node("strong", Number(item.change_pct) >= 0 ? "positive" : "negative", fmtPct(item.change_pct))));
    metrics.appendChild(stack(node("span", "", "止损距"), node("strong", "", fmtPct(item.distance_to_stop_pct))));
    metrics.appendChild(stack(node("span", "", "止盈距"), node("strong", "", fmtPct(item.distance_to_take_profit_pct))));
    metrics.appendChild(stack(node("span", "", "成交额"), node("strong", "", fmtAmount(item.amount))));

    const levels = item.levels || {};
    const line = node(
      "div",
      "holding-line",
      `支撑 ${fmt(levels.support, 3)} · 止损 ${fmt(levels.stop_loss, 3)} · 止盈 ${fmt(levels.take_profit, 3)}`
    );
    const message = node("div", "holding-message", status.message || compactReason(item));
    row.appendChild(head);
    row.appendChild(metrics);
    row.appendChild(line);
    row.appendChild(message);
    row.addEventListener("click", () => {
      els.symbol.value = item.symbol;
      setMarket(item.market || "etf");
      analyze(item.symbol, item.market || "etf", item.name || null);
    });
    els.holdingsList.appendChild(row);
  });
}

async function loadHoldings() {
  const data = await api("/api/holdings");
  renderHoldings(data);
  window.clearTimeout(window.__holdingsPollTimer);
  window.__holdingsPollTimer = window.setTimeout(loadHoldings, 60000);
}

async function refreshHoldings() {
  els.holdingsRefreshButton.disabled = true;
  els.holdingsRefreshButton.textContent = "刷新中";
  try {
    await loadHoldings();
  } catch (error) {
    els.holdingsMeta.textContent = error.message;
  } finally {
    els.holdingsRefreshButton.disabled = false;
    els.holdingsRefreshButton.textContent = "刷新";
  }
}

function setText(el, value) {
  if (el) {
    el.textContent = value;
  }
}

function renderBounceDaily(payload) {
  const available = payload?.available === true;
  const status = payload?.status || "missing";
  const focus = payload?.pick || payload?.holding || {};
  if (els.bounceDailyHero) {
    els.bounceDailyHero.dataset.state = status;
  }
  const verb = { buy: "买入", holding: "持有", cash: "空仓", missing: "等待" }[status] || "等待";
  setText(els.bounceDailyStatus, verb);
  setText(
    els.bounceDailyMeta,
    available
      ? `bounce_dn2_negext · 截至 ${payload.data_through || payload.look_date || "--"} · 不自动下单`
      : "研究账本 · 非正式有效 · 不自动下单"
  );
  setText(
    els.bounceDailyHeadline,
    focus.symbol ? `${focus.symbol}  ${focus.name || ""}`.trim() : payload?.headline || "今日空仓"
  );
  setText(els.bounceDailyDetail, payload?.detail || "跑完盘后扫描后显示买谁或空仓。");
  setText(els.bounceFactSignal, focus.signal_date || payload?.look_date || "--");
  setText(
    els.bounceFactAction,
    status === "holding" ? focus.exit_date || "--" : focus.entry_date || "--"
  );
  setText(
    els.bounceFactExt,
    focus.stock_return_20d_pct == null ? "--" : `${focus.stock_return_20d_pct}`
  );
  setText(els.bounceFactAsOf, payload?.data_through || payload?.as_of || "--");
  setText(
    els.bounceFactOos,
    payload?.days_until_twelve_month == null ? "--" : `还差 ${payload.days_until_twelve_month} 天`
  );
  setText(els.bounceFactDue, payload?.twelve_month_due || "2027-07-04");
  if (!els.bounceDailyList) {
    return;
  }
  clear(els.bounceDailyList);
  const ledger = payload?.ledger || [];
  if (!ledger.length) {
    els.bounceDailyList.hidden = true;
    return;
  }
  els.bounceDailyList.hidden = false;
  ledger
    .slice()
    .reverse()
    .forEach((item) => {
      if (!item?.symbol) {
        return;
      }
      const row = node("div", "recommendation-item");
      row.appendChild(
        stack(
          node("span", "recommendation-title", `${item.signal_date || ""}  ${item.symbol} ${item.name || ""}`.trim()),
          node("span", "recommendation-reason", `入 ${item.entry_date || "--"} · 出 ${item.exit_date || "--"}`)
        )
      );
      els.bounceDailyList.appendChild(row);
    });
}

async function loadBounceDaily() {
  const data = await api("/api/research/bounce-daily");
  renderBounceDaily(data);
}

async function loadRecommendations() {
  return requestRecommendationSnapshot("/api/recommendations/latest");
}

async function loadProductionStatus() {
  const data = await api("/api/production/status");
  const status = data?.status || "unknown";
  setStatus(`服务在线 · 生产检查 ${status}`, status === "healthy");
}

async function loadAlerts() {
  const data = await api("/api/alerts/recent?limit=50");
  renderAlerts(data);
}

async function loadPerformance() {
  const data = await api("/api/performance/recommendations?limit=500");
  renderPerformance(data);
}

async function runRecommendations() {
  els.runRecommendationsButton.disabled = true;
  els.runRecommendationsButton.textContent = "扫描中";
  try {
    await requestRecommendationSnapshot("/api/recommendations/run?force=true&background=true", {
      method: "POST",
      body: "{}",
    });
  } catch (error) {
    els.recommendationMeta.textContent = error.message;
  } finally {
    els.runRecommendationsButton.disabled = false;
    els.runRecommendationsButton.textContent = "立即扫描";
  }
}

async function runMonitor() {
  els.monitorButton.disabled = true;
  els.monitorButton.textContent = "检查中";
  try {
    await api("/api/alerts/monitor?force=true", { method: "POST", body: "{}" });
    await loadAlerts();
  } catch (error) {
    els.alertMeta.textContent = error.message;
  } finally {
    els.monitorButton.disabled = false;
    els.monitorButton.textContent = "立即检查";
  }
}

function renderResult(result) {
  const name = result.name ? `${result.name} ` : "";
  els.resultTitle.textContent = `${name}${result.symbol}`;
  els.resultMeta.textContent = `${result.market.toUpperCase()} · ${result.as_of} · ${result.source}`;
  els.actionBadge.textContent = result.action_label;
  els.actionBadge.className = `action-badge ${actionClass(result.action)}`;
  els.lastClose.textContent = fmt(result.last_close, 3);
  els.scoreValue.textContent = fmt(result.score, 2);
  els.confidenceValue.textContent = `${result.confidence}%`;
  els.stopLoss.textContent = fmt(result.levels.stop_loss, 3);
  els.entryZone.textContent = `${fmt(result.entry_zone.low, 3)} - ${fmt(result.entry_zone.high, 3)}`;
  els.supportLevel.textContent = fmt(result.levels.support, 3);
  els.resistanceLevel.textContent = fmt(result.levels.resistance, 3);
  els.takeProfit.textContent = fmt(result.levels.take_profit, 3);
  renderTradePlans(result.trade_plans || {});
  renderList(els.reasonList, result.reasons, "reason");
  renderList(els.riskList, result.risks, "risk");
  els.btStrategy.textContent = fmtPct(result.backtest.strategy_return_pct);
  els.btHold.textContent = fmtPct(result.backtest.buy_hold_return_pct);
  els.btDrawdown.textContent = fmtPct(result.backtest.max_drawdown_pct);
  els.btTrades.textContent = String(result.backtest.trade_count ?? "--");
  drawChart(result.candles, result);
}

function renderTradePlans(plans) {
  const shortPlan = plans.short_term || {};
  const longPlan = plans.long_term || {};
  els.shortPlanHorizon.textContent = shortPlan.horizon || "--";
  els.shortPlanAction.textContent = shortPlan.label || "--";
  els.shortPlanStop.textContent = fmt(shortPlan.stop_loss, 3);
  els.shortPlanTarget.textContent = fmt(shortPlan.take_profit, 3);
  renderList(
    els.shortPlanList,
    [...(shortPlan.conditions || []), ...(shortPlan.risks || [])],
    "reason"
  );

  const zone = longPlan.accumulation_zone || {};
  els.longPlanHorizon.textContent = longPlan.horizon || "--";
  els.longPlanAction.textContent = longPlan.label || "--";
  els.longPlanZone.textContent = `${fmt(zone.low, 3)} - ${fmt(zone.high, 3)}`;
  els.longPlanStop.textContent = fmt(longPlan.trend_stop, 3);
  renderList(
    els.longPlanList,
    [...(longPlan.conditions || []), ...(longPlan.risks || [])],
    "reason"
  );
}

function movingAverage(candles, window) {
  const values = [];
  let sum = 0;
  candles.forEach((candle, index) => {
    sum += candle.close;
    if (index >= window) sum -= candles[index - window].close;
    values.push(index >= window - 1 ? sum / window : null);
  });
  return values;
}

function drawSeries(ctx, points, mapX, mapY, color, width = 2) {
  ctx.beginPath();
  points.forEach((point, index) => {
    if (point === null || point === undefined || Number.isNaN(point)) return;
    const x = mapX(index);
    const y = mapY(point);
    if (ctx.__started) ctx.lineTo(x, y);
    else {
      ctx.moveTo(x, y);
      ctx.__started = true;
    }
  });
  ctx.strokeStyle = color;
  ctx.lineWidth = width;
  ctx.stroke();
  ctx.__started = false;
}

function drawChart(candles, result) {
  const canvas = els.chart;
  const rect = canvas.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(640, Math.floor(rect.width * ratio));
  canvas.height = Math.max(300, Math.floor(rect.height * ratio));
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  const width = canvas.width / ratio;
  const height = canvas.height / ratio;
  ctx.clearRect(0, 0, width, height);

  if (!candles || candles.length < 2) {
    ctx.fillStyle = "#65717b";
    ctx.fillText("暂无图表数据", 24, 32);
    return;
  }

  const padding = { left: 50, right: 18, top: 24, bottom: 34 };
  const closes = candles.map((candle) => candle.close);
  const ma20 = movingAverage(candles, 20);
  const ma60 = movingAverage(candles, 60);
  const allValues = closes.concat(ma20.filter(Boolean), ma60.filter(Boolean));
  const min = Math.min(...allValues);
  const max = Math.max(...allValues);
  const span = max - min || 1;
  const plotW = width - padding.left - padding.right;
  const plotH = height - padding.top - padding.bottom;
  const mapX = (index) => padding.left + (index / (candles.length - 1)) * plotW;
  const mapY = (value) => padding.top + (1 - (value - min) / span) * plotH;

  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  ctx.strokeStyle = "#dbe3e8";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i += 1) {
    const y = padding.top + (plotH / 4) * i;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(width - padding.right, y);
    ctx.stroke();
    const label = max - (span / 4) * i;
    ctx.fillStyle = "#64717d";
    ctx.font = "12px system-ui";
    ctx.fillText(fmt(label, 2), 8, y + 4);
  }

  drawSeries(ctx, closes, mapX, mapY, "#205ec8", 2.4);
  drawSeries(ctx, ma20, mapX, mapY, "#a66c17", 1.6);
  drawSeries(ctx, ma60, mapX, mapY, "#5d5ac9", 1.6);

  const lastIndex = candles.length - 1;
  const x = mapX(lastIndex);
  const y = mapY(candles[lastIndex].close);
  ctx.beginPath();
  ctx.arc(x, y, 5, 0, Math.PI * 2);
  ctx.fillStyle = result.action === "BUY" ? "#0f8f5f" : result.action === "SELL" ? "#c43b3b" : "#205ec8";
  ctx.fill();
  ctx.fillStyle = "#172026";
  ctx.font = "12px system-ui";
  ctx.fillText(candles[0].date, padding.left, height - 12);
  ctx.fillText(candles[lastIndex].date, width - padding.right - 78, height - 12);
}

async function analyze(symbol = els.symbol.value, market = state.market, name = null) {
  if (els.analyzeWorkspace) {
    els.analyzeWorkspace.open = true;
  }
  setBusy(true);
  try {
    const result = await api("/api/analyze", {
      method: "POST",
      body: JSON.stringify({
        symbol: symbol.trim(),
        market,
        name,
        adjust: els.adjust.value,
        lookback_days: Number(els.lookback.value),
      }),
    });
    renderResult(result);
    setStatus("已连接", true);
  } catch (error) {
    setStatus("请求失败", false);
    renderList(els.reasonList, [error.message], "risk");
  } finally {
    setBusy(false);
  }
}

function renderWatchlist() {
  clear(els.watchlist);
  state.watchlist.forEach((item) => {
    const row = node("button", "watch-item");
    row.type = "button";
    const result = state.scanResults.get(`${item.market}:${item.symbol}`);
    row.appendChild(
      stack(
        node("span", "watch-symbol", item.symbol),
        node("span", "watch-meta", `${item.name || ""} · ${String(item.market).toUpperCase()}`.trim()),
      )
    );
    row.appendChild(node("span", "mini-badge", result ? result.action : "查看"));
    row.addEventListener("click", () => {
      els.symbol.value = item.symbol;
      setMarket(item.market);
      analyze(item.symbol, item.market, item.name || null);
    });
    els.watchlist.appendChild(row);
  });
}

async function loadWatchlist() {
  const data = await api("/api/watchlist");
  state.watchlist = data.items || [];
  renderWatchlist();
}

async function saveWatchlist() {
  await api("/api/watchlist", {
    method: "PUT",
    body: JSON.stringify({ items: state.watchlist }),
  });
}

async function scanWatchlist() {
  els.scanButton.disabled = true;
  els.scanButton.textContent = "扫描中";
  try {
    const data = await api("/api/watchlist/analyze", { method: "POST", body: "{}" });
    state.scanResults.clear();
    (data.items || []).forEach((item) => {
      state.scanResults.set(`${item.market}:${item.symbol}`, item);
    });
    renderWatchlist();
    if (data.items?.[0]) renderResult(data.items[0]);
  } catch (error) {
    renderList(els.riskList, [error.message], "risk");
  } finally {
    els.scanButton.disabled = false;
    els.scanButton.textContent = "批量扫描";
  }
}

document.querySelectorAll(".segment-button").forEach((button) => {
  button.addEventListener("click", () => setMarket(button.dataset.market));
});

els.form.addEventListener("submit", (event) => {
  event.preventDefault();
  analyze();
});

els.scanButton.addEventListener("click", scanWatchlist);
if (els.runRecommendationsButton) {
  els.runRecommendationsButton.addEventListener("click", runRecommendations);
}
if (els.monitorButton) {
  els.monitorButton.addEventListener("click", runMonitor);
}
if (els.holdingsRefreshButton) {
  els.holdingsRefreshButton.addEventListener("click", refreshHoldings);
}

els.watchForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const item = {
    symbol: els.watchSymbol.value.trim(),
    market: els.watchMarket.value,
    name: els.watchName.value.trim() || null,
  };
  if (!item.symbol) return;
  state.watchlist = state.watchlist.filter((existing) => `${existing.market}:${existing.symbol}` !== `${item.market}:${item.symbol}`);
  state.watchlist.unshift(item);
  state.watchlist = state.watchlist.slice(0, 100);
  els.watchSymbol.value = "";
  els.watchName.value = "";
  renderWatchlist();
  await saveWatchlist();
});

window.addEventListener("resize", () => {
  if (window.__lastResult) drawChart(window.__lastResult.candles, window.__lastResult);
});

const originalRender = renderResult;
renderResult = (result) => {
  window.__lastResult = result;
  originalRender(result);
};

(async function bootstrap() {
  try {
    const health = await api("/health");
    setStatus(health.auth === "enabled" ? "需认证" : "已连接", true);
    await Promise.allSettled([loadBounceDaily(), loadProductionStatus()]);
    await loadWatchlist();
  } catch (error) {
    setStatus("离线", false);
    renderList(els.riskList, [error.message], "risk");
  }
})();
