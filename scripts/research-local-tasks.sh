#!/bin/bash
# 本地研究任务：第4（slippage/成本敏感性）+ 第5（市场风格切换）
# 用从 VPS 拉来的 413-symbols 缓存跑（akshare 取数不通但缓存够）
# 用法：nohup bash scripts/research-local-tasks.sh &
cd "$(dirname "$0")/.." || exit 1
export UNIVERSE_CACHE_PATH=data/uni_cached413.json
OUT=data/research_cache/local_research
mkdir -p "$OUT"
PY=.venv/bin/python

BASE="--hold-days 5 --capital-model slot-daily --exposure-multiplier 2.08 \
  --annual-financing-rate-pct 8 --pre-exit-calendar-gap-days 7 \
  --correlation-threshold 0.35 --partial-profit-activation-pct 18 --partial-profit-fraction 1.0 \
  --required-signal-tags breadth_advancing_gte_50,breakout_20d,price_gap_up_2_to_5 \
  --excluded-signal-tags entry_gap_lt_neg1,proxy20_avg_gte_15,proxy60_avg_lt_0 \
  --market-levels favorable,neutral --max-deep 80 --top-n 10 --max-universe-symbols 300"

run() {
  local tag=$1; shift
  echo "=== START $tag $(date) ===" >> "$OUT/run.log"
  $PY -m app.jobs research-historical-sweep $BASE "$@" \
    --qualified-trades-output "$OUT/${tag}.qt.json" \
    > "$OUT/${tag}.sweep.json" 2>>"$OUT/run.log"
  echo "=== DONE $tag $(date) ===" >> "$OUT/run.log"
}

# 第4：slippage/成本敏感性（固定 start 2024-07，变 slippage/cost）
run sens_baseline_slip10_cost25 --start-date 2024-07-05 --slippage-bps 10 --roundtrip-cost-bps 25
run sens_stress_slip30_cost50  --start-date 2024-07-05 --slippage-bps 30 --roundtrip-cost-bps 50

# 第5：市场风格切换（固定 slip10/cost25，变 start-date）
run style_2023_q2 --start-date 2023-05-01 --slippage-bps 10 --roundtrip-cost-bps 25
run style_2025   --start-date 2025-01-01 --slippage-bps 10 --roundtrip-cost-bps 25

echo "=== ALL DONE $(date) ===" >> "$OUT/run.log"
