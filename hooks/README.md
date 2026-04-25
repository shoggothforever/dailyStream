# DailyStream Hooks

仓库里的示例脚本是 `run_command` attachment 的参考实现。模板里的路径都指向
`~/.dailystream/hooks/<script>.sh`，安装方式任选：

```bash
# 方案 A — symlink（推荐：脚本更新时自动同步）
mkdir -p ~/.dailystream/hooks
for f in hooks/*.sh; do
    ln -sf "$(pwd)/$f" "$HOME/.dailystream/hooks/$(basename "$f")"
done

# 方案 B — 直接拷贝（改自己版本）
mkdir -p ~/.dailystream/hooks
cp hooks/*.sh ~/.dailystream/hooks/
chmod +x ~/.dailystream/hooks/*.sh
```

## 注入的环境变量

每次 `run_command` 执行时，DailyStream 都会注入以下 `DAILYSTREAM_*` 环境变量。
**本次调用 POST 链里已经跑完的 attachment 才会贡献对应变量**；例如想拿到
`DAILYSTREAM_AI_DESCRIPTION` 就必须在同一个 preset 里挂 `ai_analyze`。

### 总是可用

| 变量 | 说明 |
|---|---|
| `DAILYSTREAM_FRAME_PATH`    | 本次截图 PNG 的绝对路径 |
| `DAILYSTREAM_FRAME_INDEX`   | 本次 preset 调用内的帧序号（0-based，burst/interval 递增） |
| `DAILYSTREAM_SOURCE_KIND`   | `interactive` / `fullscreen` / `region` / `window` / `clipboard` |
| `DAILYSTREAM_TIMESTAMP`     | ISO 8601 触发时间 |
| `DAILYSTREAM_WORKSPACE_DIR` | 当前 workspace 根目录 |
| `DAILYSTREAM_PIPELINE`      | 当前 active pipeline 名称（可能为空） |
| `DAILYSTREAM_MODE_ID` / `_NAME` / `_PRESET_ID` / `_PRESET_NAME` | 触发的 Mode / Preset 元数据 |
| `DAILYSTREAM_DESCRIPTION`   | silent 模式下用户预填的描述（非 silent 为空） |
| `DAILYSTREAM_ARTIFACTS_JSON`| `frame.post_artifacts` 完整 JSON，适合 `jq` 解析 |

### 依赖 `ai_analyze` attachment

| 变量 | 说明 |
|---|---|
| `DAILYSTREAM_AI_DESCRIPTION`     | 拼了 category+tags 的漂亮单行 summary |
| `DAILYSTREAM_AI_DESCRIPTION_RAW` | 仅 AI 给的 description 原文 |
| `DAILYSTREAM_AI_CATEGORY`        | `coding` / `design` / `browsing` / … |
| `DAILYSTREAM_AI_KEY_ELEMENTS`    | 关键元素标签，换行分隔 |

## 执行顺序保证

**`run_command` 总是 POST 链中最后执行**，不管你在 Designer 里怎么拖。这样无论
attachment 顺序如何，脚本都能读到上游 producer 的结果。

如果上游的 `ai_analyze` 配置为 `wait=false`（异步），AI 结果可能还没落到 artifacts，
此时需要在 `run_command` 参数里把 `wait_for_ai_seconds` 设为 5~15 秒。
脚本会轮询等待 `ai_description` 出现或超时后立即执行。

## 脚本一览

| 脚本 | 配套模板 | 干什么 |
|---|---|---|
| `send_mail.sh` + `add_mail.sh` | Email Backup | 通过 SMTP 把截图发邮件；`add_mail.sh` 往 Keychain 写入 SMTP 密码 |
| `ai_commit_message.sh` | AI Commit Message | 读 `DAILYSTREAM_AI_DESCRIPTION_RAW` 作为 git commit message |
| `smart_archive.sh` | Smart Archive | 按 AI category 归档截图，丢弃分神/other 帧 |
| `jsonl_logger.sh` | 任何 | 把 `DAILYSTREAM_ARTIFACTS_JSON` 逐行追加到 `frames.jsonl`（需 `jq`） |

## 自己写脚本的模板

```bash
#!/usr/bin/env bash
set -euo pipefail

FRAME="${DAILYSTREAM_FRAME_PATH:-}"
[[ -z "$FRAME" || ! -f "$FRAME" ]] && exit 0

# 用 AI 分类做决策
case "${DAILYSTREAM_AI_CATEGORY:-other}" in
    coding)       do_something_with_code_shot ;;
    design)       do_something_with_design_shot ;;
    communication) do_something_with_chat_shot ;;
    *)            exit 0 ;;
esac
```
