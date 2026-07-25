#!/bin/bash
set -u
export VPS_RUNTIME_ROLE=recommendation_only

PROJECT_DIR=${PROJECT_DIR:-/home/ubuntu/quant-signal}
SYSTEMCTL_BIN=${SYSTEMCTL_BIN:-systemctl}
PYTHON_BIN=${PYTHON_BIN:-$PROJECT_DIR/.venv/bin/python}
failures=0
service=quant-signal.service
timers=(quant-signal-recommend.timer quant-signal-monitor.timer quant-signal-planned-exits.timer quant-signal-cache-warm.timer quant-signal-health.timer)
oneshot_services=(quant-signal-recommend.service quant-signal-monitor.service quant-signal-planned-exits.service quant-signal-cache-warm.service quant-signal-health.service)

check_active() {
  local unit=$1
  if ! "$SYSTEMCTL_BIN" is-active --quiet "$unit"; then
    echo "UNHEALTHY inactive: $unit"
    failures=2
  fi
  if "$SYSTEMCTL_BIN" is-failed --quiet "$unit"; then
    echo "UNHEALTHY failed: $unit"
    failures=2
  fi
}

check_active "$service"
for unit in "${timers[@]}"; do
  if ! "$SYSTEMCTL_BIN" is-enabled --quiet "$unit"; then
    echo "UNHEALTHY disabled: $unit"
    failures=2
  fi
  check_active "$unit"
done

for unit in "${oneshot_services[@]}"; do
  if "$SYSTEMCTL_BIN" is-failed --quiet "$unit"; then
    echo "UNHEALTHY failed: $unit"
    failures=2
  fi
done

cd "$PROJECT_DIR" || exit 2
"$PYTHON_BIN" -m app.jobs production-check
app_status=$?
if (( app_status > failures )); then
  failures=$app_status
fi
exit "$failures"
