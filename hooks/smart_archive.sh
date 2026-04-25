#!/usr/bin/env bash
# smart_archive.sh — 根据 AI 分类把截图自动分文件夹，并过滤低价值画面。
#
# 使用场景：长时间 interval 录屏（比如 meeting-recorder 模板），
# AI 给每帧打 category + key_elements，这个脚本：
#   1. 跳过 category=other 或包含 "distracted" 标签的帧（删图）
#   2. 其他帧 mv 到 ~/DailyStreamArchive/<YYYY-MM-DD>/<category>/
#   3. 把 category + 时间戳 + 路径追加写到 daily index
#
# Preset 建议：
#   POST: ai_analyze(user_hint="...", wait=false, save_to_analysis=true)
#   POST: run_command(wait_for_ai_seconds=10, wait=false)

set -euo pipefail

FRAME="${DAILYSTREAM_FRAME_PATH:-}"
[[ -z "$FRAME" || ! -f "$FRAME" ]] && { echo "[archive] no frame"; exit 0; }

CATEGORY="${DAILYSTREAM_AI_CATEGORY:-unknown}"
KEY_ELEMENTS="${DAILYSTREAM_AI_KEY_ELEMENTS:-}"

# 关键词过滤：分神类标签直接丢弃。
if grep -qE '(?i)(distracted|social|reddit|tiktok|youtube)' <<<"$KEY_ELEMENTS"; then
    rm -f "$FRAME"
    echo "[archive] 🗑  dropped distraction frame"
    exit 0
fi

# 空分类 / other 丢掉，省硬盘。
if [[ -z "$CATEGORY" || "$CATEGORY" == "other" || "$CATEGORY" == "unknown" ]]; then
    rm -f "$FRAME"
    echo "[archive] 🗑  dropped uncategorised frame"
    exit 0
fi

TODAY=$(date +%F)
DEST_DIR="$HOME/DailyStreamArchive/$TODAY/$CATEGORY"
mkdir -p "$DEST_DIR"

mv "$FRAME" "$DEST_DIR/"
NEW_PATH="$DEST_DIR/$(basename "$FRAME")"

INDEX="$HOME/DailyStreamArchive/$TODAY/index.tsv"
printf '%s\t%s\t%s\n' \
    "${DAILYSTREAM_TIMESTAMP:-$(date -Iseconds)}" \
    "$CATEGORY" \
    "$NEW_PATH" >> "$INDEX"

echo "[archive] ✅ $CATEGORY ← $NEW_PATH"
