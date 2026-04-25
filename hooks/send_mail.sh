#!/bin/bash
# send_mail.sh — 把 DailyStream 截图作为附件发到指定邮箱
#
# 从 macOS Keychain 读取 SMTP 密码，环境变量由 DailyStream 注入。
# 依赖：python3（macOS 自带）+ Keychain 里存了一条 smtp 密码。

set -euo pipefail

# ========== 你改这几处 ==========
SMTP_HOST="smtp.gmail.com"
SMTP_PORT="587"
SMTP_USER="you@gmail.com"
MAIL_TO="you@gmail.com"
# 以下两条不用改：从 Keychain 按 label 取密码
KEYCHAIN_SERVICE="dailystream-smtp"
KEYCHAIN_ACCOUNT="$SMTP_USER"
# ==================================

SMTP_PASS="$(security find-generic-password \
    -s "$KEYCHAIN_SERVICE" \
    -a "$KEYCHAIN_ACCOUNT" \
    -w 2>/dev/null || true)"

if [[ -z "$SMTP_PASS" ]]; then
    echo "No SMTP password in Keychain (service=$KEYCHAIN_SERVICE)" >&2
    exit 1
fi

FRAME="${DAILYSTREAM_FRAME_PATH:-}"
if [[ -z "$FRAME" || ! -f "$FRAME" ]]; then
    echo "Missing DAILYSTREAM_FRAME_PATH" >&2
    exit 1
fi

TS="${DAILYSTREAM_TIMESTAMP:-$(date -Iseconds)}"
PRESET="${DAILYSTREAM_PRESET_NAME:-capture}"
PIPELINE="${DAILYSTREAM_PIPELINE:-}"
AI_DESC="${DAILYSTREAM_AI_DESCRIPTION:-}"

SUBJECT="[$PRESET] $TS"
BODY="DailyStream capture\nPipeline: $PIPELINE\nTimestamp: $TS\n\nAI:\n$AI_DESC"

SMTP_HOST="$SMTP_HOST" \
SMTP_PORT="$SMTP_PORT" \
SMTP_USER="$SMTP_USER" \
SMTP_PASS="$SMTP_PASS" \
MAIL_TO="$MAIL_TO" \
SUBJECT="$SUBJECT" \
BODY="$BODY" \
FRAME="$FRAME" \
/usr/bin/env python3 <<'PY'
import os, ssl, smtplib, mimetypes
from email.message import EmailMessage

msg = EmailMessage()
msg["From"] = os.environ["SMTP_USER"]
msg["To"] = os.environ["MAIL_TO"]
msg["Subject"] = os.environ["SUBJECT"]
msg.set_content(os.environ["BODY"])

path = os.environ["FRAME"]
ctype, _ = mimetypes.guess_type(path)
maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
with open(path, "rb") as f:
    msg.add_attachment(
        f.read(),
        maintype=maintype, subtype=subtype,
        filename=os.path.basename(path),
    )

ctx = ssl.create_default_context()
with smtplib.SMTP(os.environ["SMTP_HOST"],
                  int(os.environ["SMTP_PORT"])) as s:
    s.starttls(context=ctx)
    s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
    s.send_message(msg)

print(f"sent {path} to {os.environ['MAIL_TO']}")
PY

