#!/usr/bin/env bash
# ai_commit_message.sh — 把 AI 对屏幕的描述当 git commit message。
#
# Preset 建议：
#   Source: fullscreen
#   POST:   ai_analyze (user_hint="用 Conventional Commits 的格式写一条 git
#           commit message 描述屏幕上正在修改的代码或界面。只返回一行。",
#           wait=true, prefill_hud=false, save_to_analysis=false)
#   POST:   run_command (wait_for_ai_seconds=15, wait=true)
#
# 运行：cd 到目标 git 仓库后触发 Preset。或者改 WORKDIR 写死你的主仓库。

set -euo pipefail

MSG="${DAILYSTREAM_AI_DESCRIPTION_RAW:-${DAILYSTREAM_AI_DESCRIPTION:-}}"
if [[ -z "$MSG" ]]; then
    echo "[ai_commit] No AI description available; skipping." >&2
    exit 0
fi

# 只保留第一行，去掉末尾多余空白。
MSG=$(printf '%s' "$MSG" | head -n1 | sed 's/[[:space:]]*$//')

WORKDIR="${WORKDIR:-$(pwd)}"
cd "$WORKDIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "[ai_commit] $WORKDIR is not a git repo; skipping." >&2
    exit 0
fi

if git diff --cached --quiet; then
    echo "[ai_commit] Nothing staged; running 'git add -A'." >&2
    git add -A
fi

if git diff --cached --quiet; then
    echo "[ai_commit] Still nothing to commit." >&2
    exit 0
fi

git commit -m "$MSG"
echo "[ai_commit] ✅ committed with message:"
echo "           $MSG"
