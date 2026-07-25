#!/bin/bash
set -euo pipefail

# Retired compatibility entrypoint. Research, fitting, tuning, replay, and
# model-package creation run only on the local workstation. The VPS may load a
# verified frozen package to generate and distribute 0-3 recommendations.
VPS_RUNTIME_ROLE=recommendation_only
export VPS_RUNTIME_ROLE

echo "已停用：VPS 仅负责推荐推理与分发，请在本机运行研究和训练任务。" >&2
exit 64
