"""Shareable Mode templates for the Capture Mode Designer.

A **template** is just a regular :class:`Mode` payload (same JSON as
``save_mode`` takes) bundled with some metadata — title, description,
author, icon.  Templates live in three places:

1. **Built-in** (this file).  Ships with DailyStream.
2. **Local user** (``~/.dailystream/templates/*.json``).  Drop a file
   here and it shows up in the Designer's "Import Template" menu.
3. **Remote** (future — a signed URL, shared gist, etc.).  The RPC
   layer already supports installing from raw JSON, which leaves the
   door open without committing to any specific distribution channel.

Template authors: the recommended sharing format is exactly what
``capture_modes.export_mode`` produces — a small JSON document that
round-trips through :func:`Mode.from_dict`.  Any extra metadata fields
(``template_id``, ``author``, ``description``) are preserved on import
but don't affect Executor behaviour.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config import CONFIG_DIR
from .models import Mode, _slugify

logger = logging.getLogger(__name__)

USER_TEMPLATE_DIR = CONFIG_DIR / "templates"


@dataclass
class ModeTemplate:
    """A shareable preset-collection package."""

    template_id: str
    title: str
    description: str
    author: str
    emoji: str
    # The Mode payload — matches ``Mode.to_dict()``.
    mode: dict[str, Any]
    tags: list[str] = field(default_factory=list)
    # Hints about prerequisites (e.g. "Needs AI API key", "Requires
    # ~/.dailystream/hooks/send_mail.sh") surfaced to the Designer UI
    # so the user knows what to set up before using the template.
    prerequisites: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "template_id": self.template_id,
            "title": self.title,
            "description": self.description,
            "author": self.author,
            "emoji": self.emoji,
            "mode": self.mode,
            "tags": list(self.tags),
            "prerequisites": list(self.prerequisites),
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["ModeTemplate"]:
        if not isinstance(data, dict):
            return None
        mode = data.get("mode")
        if not isinstance(mode, dict) or not mode.get("presets"):
            return None
        tid = str(data.get("template_id") or mode.get("id") or
                  f"tpl-{uuid.uuid4().hex[:8]}")
        return cls(
            template_id=tid,
            title=str(data.get("title") or mode.get("name") or tid),
            description=str(data.get("description") or ""),
            author=str(data.get("author") or "anonymous"),
            emoji=str(data.get("emoji") or mode.get("emoji") or "📦"),
            mode=mode,
            tags=list(data.get("tags") or []),
            prerequisites=list(data.get("prerequisites") or []),
        )


# ---------------------------------------------------------------------------
# Built-in templates — hand-crafted "good defaults" for common scenarios.
# ---------------------------------------------------------------------------


def _preset(
    pid: str, name: str, emoji: str, source_kind: str,
    attachments: list[dict[str, Any]],
    hotkey: Optional[str] = None,
    region: Optional[str] = None,
) -> dict:
    """Tiny factory for preset dicts; keeps the template table readable."""
    source: dict[str, Any] = {"kind": source_kind}
    if region:
        source["region"] = region
    return {
        "id": pid,
        "name": name,
        "emoji": emoji,
        "source": source,
        "attachments": attachments,
        "hotkey": hotkey,
    }


def _att(aid: str, **params: Any) -> dict:
    return {"id": aid, "params": dict(params)}


_BUILTIN_TEMPLATES: list[ModeTemplate] = [
    # -------------------------------------------------------------------
    # 1. Daily work — OCR everything, AI-assisted descriptions
    # -------------------------------------------------------------------
    ModeTemplate(
        template_id="daily-work",
        title="Daily Work",
        description=(
            "For office/coding days.  Quick free-selection + full-screen "
            "shots with OCR pre-filled into the description HUD.  When "
            "an API key is configured, AI Analyze turns each capture "
            "into a one-line summary before you even see the HUD."
        ),
        author="DailyStream",
        emoji="💼",
        tags=["work", "daily", "ocr", "ai"],
        prerequisites=[
            "Optional: set ai_api_key in Settings → AI for AI Analyze",
        ],
        mode={
            "id": "daily-work",
            "name": "Daily Work",
            "emoji": "💼",
            "presets": [
                _preset(
                    "selection", "Selection", "✂️",
                    "interactive",
                    attachments=[
                        _att("single"),
                        _att("flash_menubar"),
                        _att("auto_ocr"),
                        _att("ai_analyze",
                             user_hint="One concise sentence describing this screenshot.",
                             prefill_hud=True,
                             save_to_analysis=True,
                             wait=True),
                        _att("current_pipeline"),
                    ],
                    hotkey="<cmd>+1",
                ),
                _preset(
                    "fullscreen", "Full Screen", "🖥",
                    "fullscreen",
                    attachments=[
                        _att("single"),
                        _att("flash_menubar"),
                        _att("hide_dock"),
                        _att("auto_ocr"),
                        _att("current_pipeline"),
                    ],
                    hotkey="<cmd>+<shift>+1",
                ),
                _preset(
                    "clipboard", "Clipboard", "📋",
                    "clipboard",
                    attachments=[
                        _att("single"),
                        _att("current_pipeline"),
                    ],
                    hotkey="<cmd>+2",
                ),
            ],
        },
    ),

    # -------------------------------------------------------------------
    # 2. Gaming highlights — silent burst, no OCR to keep FPS
    # -------------------------------------------------------------------
    ModeTemplate(
        template_id="gaming-highlights",
        title="Gaming Highlights",
        description=(
            "3-frame silent burst tuned for game moments — no HUD, no "
            "OCR, just a menu-bar flash + shutter sound.  Dock is "
            "hidden, cursor is stripped from the PNG."
        ),
        author="DailyStream",
        emoji="🎮",
        tags=["gaming", "burst", "silent"],
        prerequisites=[],
        mode={
            "id": "gaming-highlights",
            "name": "Gaming",
            "emoji": "🎮",
            "presets": [
                _preset(
                    "highlight", "Highlight Burst", "🏆",
                    "fullscreen",
                    attachments=[
                        _att("burst", count=3, interval_ms=120),
                        _att("silent_save"),
                        _att("flash_menubar"),
                        _att("sound", volume=0.4),
                        _att("hide_cursor"),
                        _att("hide_dock"),
                        _att("current_pipeline"),
                    ],
                    hotkey="<option>+1",
                ),
                _preset(
                    "death-marker", "Death Marker", "💀",
                    "fullscreen",
                    attachments=[
                        _att("single"),
                        _att("silent_save"),
                        _att("flash_menubar"),
                        _att("hide_cursor"),
                        _att("current_pipeline"),
                    ],
                    hotkey="<option>+2",
                ),
                _preset(
                    "review-tag", "Review Later", "🤔",
                    "fullscreen",
                    attachments=[
                        _att("single"),
                        _att("silent_save"),
                        _att("notification"),
                        _att("hide_cursor"),
                        _att("current_pipeline"),
                    ],
                    hotkey="<option>+3",
                ),
            ],
        },
    ),

    # -------------------------------------------------------------------
    # 3. Meeting recorder — every 30s, silent, AI summary
    # -------------------------------------------------------------------
    ModeTemplate(
        template_id="meeting-recorder",
        title="Meeting Recorder",
        description=(
            "Silent interval capture every 30 seconds for the whole "
            "meeting.  Each frame is OCR'd and AI-summarised so you "
            "get a searchable timeline without touching the keyboard."
        ),
        author="DailyStream",
        emoji="🎤",
        tags=["meeting", "interval", "ocr", "ai"],
        prerequisites=[
            "Optional: set ai_api_key in Settings → AI for summaries",
            "Start it from the menu when the meeting begins, then "
            "⇧⌘. to stop everything at the end.",
        ],
        mode={
            "id": "meeting-recorder",
            "name": "Meeting",
            "emoji": "🎤",
            "presets": [
                _preset(
                    "continuous", "Continuous Capture", "⏱",
                    "fullscreen",
                    attachments=[
                        _att("interval", seconds=30, max_count=0),
                        _att("silent_save"),
                        _att("flash_menubar"),
                        _att("auto_ocr"),
                        _att("ai_analyze",
                             user_hint=(
                                 "One sentence summarising what's happening "
                                 "on screen during a meeting."
                             ),
                             prefill_hud=False,
                             save_to_analysis=True,
                             wait=False),
                        _att("hide_dock"),
                        _att("current_pipeline"),
                    ],
                    hotkey="<cmd>+<shift>+m",
                ),
                _preset(
                    "whiteboard-snap", "Whiteboard", "📐",
                    "interactive",
                    attachments=[
                        _att("single"),
                        _att("auto_ocr"),
                        _att("current_pipeline"),
                    ],
                    hotkey="<cmd>+<shift>+w",
                ),
            ],
        },
    ),

    # -------------------------------------------------------------------
    # 4. Tutorial — no cursor, with shutter sound, HUD prompts
    # -------------------------------------------------------------------
    ModeTemplate(
        template_id="tutorial-maker",
        title="Tutorial Maker",
        description=(
            "For writing how-to docs: cursor is hidden, Dock is hidden, "
            "each capture plays a gentle shutter so you hear your "
            "progress.  The HUD stays open so you can type the caption "
            "before moving on."
        ),
        author="DailyStream",
        emoji="📖",
        tags=["tutorial", "docs"],
        prerequisites=[],
        mode={
            "id": "tutorial-maker",
            "name": "Tutorial",
            "emoji": "📖",
            "presets": [
                _preset(
                    "step", "Tutorial Step", "1️⃣",
                    "interactive",
                    attachments=[
                        _att("single"),
                        _att("sound", volume=0.3),
                        _att("hide_cursor"),
                        _att("hide_dock"),
                        _att("auto_ocr"),
                        _att("current_pipeline"),
                    ],
                    hotkey="<cmd>+1",
                ),
                _preset(
                    "window", "Window Grab", "🪟",
                    "interactive",
                    attachments=[
                        _att("single"),
                        _att("sound", volume=0.3),
                        _att("hide_cursor"),
                        _att("current_pipeline"),
                    ],
                    hotkey="<cmd>+<shift>+w",
                ),
            ],
        },
    ),

    # -------------------------------------------------------------------
    # 5. Auto email backup — interval + run_command (hook script)
    # -------------------------------------------------------------------
    ModeTemplate(
        template_id="email-backup",
        title="Email Backup",
        description=(
            "Every 5 minutes, snap the full screen and pipe the file "
            "into ~/.dailystream/hooks/send_mail.sh — a shell script "
            "YOU provide that attaches the PNG to an SMTP email.  The "
            "Design Docs repo has a ready-made template for the script."
        ),
        author="DailyStream",
        emoji="📧",
        tags=["backup", "email", "interval", "hook"],
        prerequisites=[
            "Create ~/.dailystream/hooks/send_mail.sh (see docs/capture_mode_designer.md)",
            "Store SMTP password in macOS Keychain: "
            "security add-generic-password -s dailystream-smtp -a you@mail -w <app-password>",
        ],
        mode={
            "id": "email-backup",
            "name": "Email Backup",
            "emoji": "📧",
            "presets": [
                _preset(
                    "every5min", "Every 5 minutes", "⏲",
                    "fullscreen",
                    attachments=[
                        _att("interval", seconds=300, max_count=0),
                        _att("silent_save"),
                        _att("flash_menubar"),
                        _att("hide_cursor"),
                        _att("run_command",
                             command=str(Path.home() /
                                         ".dailystream/hooks/send_mail.sh"),
                             wait=False,
                             timeout_seconds=60),
                        _att("current_pipeline"),
                    ],
                    hotkey=None,
                ),
            ],
        },
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_templates() -> list[ModeTemplate]:
    """Return built-in + user-supplied templates (user dir takes priority)."""
    out: dict[str, ModeTemplate] = {t.template_id: t for t in _BUILTIN_TEMPLATES}
    user_dir = USER_TEMPLATE_DIR
    if user_dir.exists():
        for path in sorted(user_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                logger.warning("Skipping malformed template file: %s", path)
                continue
            tpl = ModeTemplate.from_dict(data)
            if tpl is not None:
                out[tpl.template_id] = tpl
    return list(out.values())


def get_template(template_id: str) -> Optional[ModeTemplate]:
    for t in list_templates():
        if t.template_id == template_id:
            return t
    return None


def save_user_template(tpl: ModeTemplate) -> Path:
    """Persist a template under ``~/.dailystream/templates/``."""
    USER_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slugify(tpl.template_id) or f"template-{uuid.uuid4().hex[:8]}"
    path = USER_TEMPLATE_DIR / f"{slug}.json"
    path.write_text(
        json.dumps(tpl.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def export_mode_as_template(
    mode: Mode,
    title: Optional[str] = None,
    description: str = "",
    author: str = "user",
) -> ModeTemplate:
    """Wrap an existing :class:`Mode` into a shareable template."""
    return ModeTemplate(
        template_id=f"{mode.id}-{uuid.uuid4().hex[:6]}",
        title=title or mode.name,
        description=description,
        author=author,
        emoji=mode.emoji,
        mode=mode.to_dict(),
    )
