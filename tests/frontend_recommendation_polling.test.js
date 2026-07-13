const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const APP_PATH = new URL("../app/static/app.js", `file://${__dirname}/`);

class FakeElement {
  constructor() {
    this.children = [];
    this.classList = { toggle() {} };
    this.dataset = {};
    this.disabled = false;
    this.textContent = "";
    this.value = "";
  }

  addEventListener() {}

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  getBoundingClientRect() {
    return { width: 800, height: 400 };
  }

  getContext() {
    return new Proxy({}, { get: () => () => {} });
  }

  replaceChildren(...children) {
    this.children = children;
  }
}

class FakeAbortController {
  constructor() {
    this.signal = { aborted: false };
  }

  abort() {
    this.signal.aborted = true;
  }
}

function jsonResponse(payload) {
  return {
    ok: true,
    status: 200,
    headers: { get: () => "application/json" },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  };
}

function errorResponse(status, payload) {
  return {
    ok: false,
    status,
    headers: { get: () => "application/json" },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function createHarness() {
  const elements = new Map();
  const timers = new Map();
  const fetchQueue = [];
  const fetchCalls = [];
  let nextTimerId = 1;

  const document = {
    createElement: () => new FakeElement(),
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, new FakeElement());
      return elements.get(selector);
    },
    querySelectorAll: () => [],
  };
  const window = {
    __recommendationPollRandom: () => 0.5,
    addEventListener() {},
    clearTimeout(timerId) {
      timers.delete(timerId);
    },
    devicePixelRatio: 1,
    setTimeout(callback, delay) {
      const timerId = nextTimerId++;
      timers.set(timerId, { callback, delay });
      return timerId;
    },
  };
  const context = {
    AbortController: FakeAbortController,
    console,
    document,
    fetch(path, options) {
      fetchCalls.push({ options, path });
      const next = fetchQueue.shift();
      if (!next) throw new Error(`unexpected fetch: ${path}`);
      return typeof next === "function" ? next(path, options) : next;
    },
    window,
  };
  context.globalThis = context;

  let source = fs.readFileSync(APP_PATH, "utf8");
  source = source.replace("(async function bootstrap() {", "(async function bootstrap() { return;");
  source += `
globalThis.__pollTest = {
  getPollingState: () => typeof recommendationPolling === "undefined" ? null : recommendationPolling,
  loadRecommendations,
  renderRecommendations,
  runRecommendations,
  setRenderer: (renderer) => { renderRecommendations = renderer; },
  stop: (...args) => stopRecommendationPolling(...args),
};`;
  vm.runInNewContext(source, context, { filename: "app/static/app.js" });

  return {
    context,
    element: (selector) => elements.get(selector),
    fetchCalls,
    fetchQueue,
    timers,
  };
}

function onlyTimer(harness) {
  assert.equal(harness.timers.size, 1);
  return [...harness.timers.values()][0];
}

test("blocked current-pool snapshot renders 今日不推荐 and no stocks", () => {
  const harness = createHarness();
  harness.context.__pollTest.renderRecommendations({
    items: [{ name: "should not render", symbol: "000001" }],
    recommendation_status: "blocked_current_pool_gate",
    current_pool_gate: { passed: false, reasons: ["risk_snapshot_missing"] },
    summary: { running: false },
  });

  assert.match(harness.element("#recommendationEvidence").textContent, /今日不推荐/);
  const rows = harness.element("#recommendationsList").children;
  assert.equal(rows.length, 1);
  assert.match(rows[0].textContent, /今日不推荐/);
  assert.doesNotMatch(rows[0].textContent, /000001/);
});

test("running snapshot schedules continuous polling", async () => {
  const harness = createHarness();
  const rendered = [];
  harness.context.__pollTest.setRenderer((payload) => rendered.push(payload));
  harness.fetchQueue.push(Promise.resolve(jsonResponse({ summary: { running: true } })));

  await harness.context.__pollTest.loadRecommendations();

  assert.equal(rendered.length, 1);
  assert.equal(rendered[0].summary.running, true);
  assert.equal(onlyTimer(harness).delay, 15_000);
  assert.equal(harness.context.__pollTest.getPollingState().active, true);
});

test("fetch failures use capped exponential backoff with deterministic jitter", async () => {
  const harness = createHarness();
  const expectedDelays = [15_000, 30_000, 60_000, 120_000, 120_000];

  for (const expectedDelay of expectedDelays) {
    harness.fetchQueue.push(Promise.reject(new Error("weak network")));
    await harness.context.__pollTest.loadRecommendations();
    assert.equal(onlyTimer(harness).delay, expectedDelay);
  }

  assert.equal(harness.context.__pollTest.getPollingState().consecutiveFailures, 5);
});

test("permanent 4xx responses stop polling and show only the safe API error", async () => {
  for (const status of [401, 403, 422]) {
    const harness = createHarness();
    harness.fetchQueue.push(
      Promise.resolve(
        errorResponse(status, {
          error: { message: `safe ${status} error` },
          sensitive_debug_body: `must-not-render-${status}`,
        }),
      ),
    );

    await harness.context.__pollTest.loadRecommendations();

    assert.equal(harness.timers.size, 0);
    assert.equal(harness.context.__pollTest.getPollingState().active, false);
    assert.match(harness.element("#recommendationMeta").textContent, new RegExp(`safe ${status} error`));
    assert.doesNotMatch(harness.element("#recommendationMeta").textContent, /must-not-render/);
  }
});

test("408 429 and server errors remain retryable", async () => {
  for (const status of [408, 429, 500, 503]) {
    const harness = createHarness();
    harness.fetchQueue.push(
      Promise.resolve(errorResponse(status, { error: { message: `temporary ${status}` } })),
    );

    await harness.context.__pollTest.loadRecommendations();

    assert.equal(onlyTimer(harness).delay, 15_000);
    assert.equal(harness.context.__pollTest.getPollingState().active, true);
    assert.match(harness.element("#recommendationMeta").textContent, /自动重试/);
  }
});

test("newer request aborts the older request and stale response cannot render", async () => {
  const harness = createHarness();
  const rendered = [];
  const older = deferred();
  const newer = deferred();
  harness.context.__pollTest.setRenderer((payload) => rendered.push(payload.id));
  harness.fetchQueue.push(older.promise, newer.promise);

  const olderLoad = harness.context.__pollTest.loadRecommendations();
  const olderSignal = harness.fetchCalls[0].options.signal;
  const newerLoad = harness.context.__pollTest.loadRecommendations();
  assert.equal(olderSignal.aborted, true);

  newer.resolve(jsonResponse({ id: "newer", summary: { running: false } }));
  await newerLoad;
  older.resolve(jsonResponse({ id: "older", summary: { running: true } }));
  await olderLoad;

  assert.deepEqual(rendered, ["newer"]);
  assert.equal(harness.timers.size, 0);
  assert.equal(harness.context.__pollTest.getPollingState().controller, null);
});

test("terminal snapshot and explicit stop clear timer and controller", async () => {
  const harness = createHarness();
  harness.context.__pollTest.setRenderer(() => {});
  harness.fetchQueue.push(Promise.resolve(jsonResponse({ summary: { running: true } })));
  await harness.context.__pollTest.loadRecommendations();
  assert.equal(harness.timers.size, 1);

  const pending = deferred();
  harness.fetchQueue.push(pending.promise);
  const load = harness.context.__pollTest.loadRecommendations();
  const signal = harness.fetchCalls.at(-1).options.signal;
  harness.context.__pollTest.stop();
  assert.equal(signal.aborted, true);
  assert.equal(harness.timers.size, 0);
  assert.equal(harness.context.__pollTest.getPollingState().controller, null);

  pending.resolve(jsonResponse({ summary: { running: false } }));
  await load;
});
