#!/usr/bin/env bash
# jsonl_logger.sh — 把每一帧 + 全部 artifacts 写成 JSONL 便于后期分析。
#
# 需要 `jq`（brew install jq）。
# 生成 ~/DailyStreamArchive/YYYY-MM-DD/frames.jsonl，一行一帧，包含
# 时间戳、preset、category、key_elements、完整 artifacts。

set -euo pipefail

command -v jq >/dev/null || { echo "[jsonl_logger] jq not installed" >&2; exit 0; }

TODAY=$(date +%F)
OUT_DIR="$HOME/DailyStreamArchive/$TODAY"
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/frames.jsonl"

ARTIFACTS_JSON="${DAILYSTREAM_ARTIFACTS_JSON:-{}}"

# 把 DAILYSTREAM_* env 折叠进一条 record，再拼上 artifacts。
jq -n \
    --arg ts "${DAILYSTREAM_TIMESTAMP:-}" \
    --arg preset "${DAILYSTREAM_PRESET_NAME:-}" \
    --arg mode "${DAILYSTREAM_MODE_NAME:-}" \
    --arg pipeline "${DAILYSTREAM_PIPELINE:-}" \
    --arg frame "${DAILYSTREAM_FRAME_PATH:-}" \
    --arg idx "${DAILYSTREAM_FRAME_INDEX:-0}" \
    --argjson art "$ARTIFACTS_JSON" \
    '{
        ts: $ts, mode: $mode, preset: $preset, pipeline: $pipeline,
        frame: $frame, frame_index: ($idx | tonumber),
        artifacts: $art
    }' >> "$OUT"

echo "[jsonl_logger] → $OUT"
