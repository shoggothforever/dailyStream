"""Configurable Markdown entry templates for DailyStream.

Each entry type (image, url, text) can have a custom template.
Templates use ``{variable}`` placeholders that are filled at render time.

Available variables
-------------------
- ``{time}``        — short time string (e.g. ``14:30:25``)
- ``{type}``        — input type label (``image`` / ``url`` / ``text``)
- ``{description}`` — user description (may be empty)
- ``{content}``     — raw content (file path, URL, or text body)
- ``{image}``       — Markdown image link (only meaningful for image type)
- ``{link}``        — Markdown hyperlink (only meaningful for url type)
- ``{quote}``       — block-quoted content (only meaningful for text type)
- ``{pipeline}``    — pipeline name (available in timeline context)
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote as url_quote

from .config import short_time


# ------------------------------------------------------------------
# Default templates
# ------------------------------------------------------------------

# stream.md (LocalMarkdownSyncer) entry template — compact, timeline-style
_DEFAULT_ENTRY_TEMPLATES: dict[str, str] = {
    "image": "**{time}** · {type}\n\n{description}\n\n{image}\n\n---",
    "url":   "**{time}** · {type}\n\n{description}\n\n{link}\n\n---",
    "text":  "**{time}** · {type}\n\n{description}\n\n{quote}\n\n---",
}

# Obsidian entry template — uses ### heading & wikilink images
_DEFAULT_OBSIDIAN_TEMPLATES: dict[str, str] = {
    "image": "### {time} — {type}\n\n{description}\n\n{image}\n",
    "url":   "### {time} — {type}\n\n{description}\n\n{link}\n",
    "text":  "### {time} — {type}\n\n{description}\n\n{quote}\n",
}

# Timeline report entry template — includes pipeline name
_DEFAULT_TIMELINE_TEMPLATES: dict[str, str] = {
    "image": "### {time} — [{pipeline}] ({type})\n\n{description}\n\n{image}\n",
    "url":   "### {time} — [{pipeline}] ({type})\n\n{description}\n\n{link}\n",
    "text":  "### {time} — [{pipeline}] ({type})\n\n{description}\n\n{quote}\n",
}

# Fallback template when input_type is unknown
_FALLBACK_TEMPLATE = "**{time}** · {type}\n\n{description}\n\n{content}\n\n---"


# ------------------------------------------------------------------
# Template context builder
# ------------------------------------------------------------------

@dataclass
class EntryContext:
    """All variables available for template rendering."""

    time: str = ""
    type: str = ""
    description: str = ""
    content: str = ""
    image: str = ""
    link: str = ""
    quote: str = ""
    pipeline: str = ""


def build_context(
    *,
    timestamp: str,
    input_type: str,
    description: str,
    content: str,
    pipeline: str = "",
    image_path: Optional[str] = None,
    workspace_dir: Optional[Path] = None,
    obsidian_rel_img: Optional[str] = None,
    content_max_len: int = 500,
) -> EntryContext:
    """Build a template context dict from entry data.

    Parameters
    ----------
    image_path
        Absolute path to screenshot file (for standard Markdown ``![](…)``).
    workspace_dir
        If provided, image paths are made relative to this directory.
    obsidian_rel_img
        Pre-computed relative image path for Obsidian wikilinks.
    content_max_len
        Max characters for text quoting.
    """
    time_short = short_time(timestamp)

    ctx = EntryContext(
        time=time_short,
        type=input_type,
        description=description,
        content=content,
        pipeline=pipeline,
    )

    # Build image markdown
    if input_type == "image":
        if obsidian_rel_img:
            ctx.image = f"![[{obsidian_rel_img}]]"
        elif image_path:
            if workspace_dir:
                try:
                    rel = Path(image_path).resolve().relative_to(workspace_dir.resolve())
                except ValueError:
                    rel = Path(image_path)
                rel_encoded = url_quote(str(rel.as_posix()), safe="/")
            else:
                rel_encoded = url_quote(str(Path(image_path).as_posix()), safe="/")
            ctx.image = f"![screenshot]({rel_encoded})"
        else:
            ctx.image = f"![screenshot]({content})"

    # Build link markdown
    if input_type == "url":
        ctx.link = f"[{content}]({content})"

    # Build block quote
    if input_type == "text" and content and content != description:
        truncated = content[:content_max_len]
        ctx.quote = f"> {truncated}"

    return ctx


def render_entry(
    templates: dict[str, str],
    ctx: EntryContext,
) -> str:
    """Render an entry using the given template set and context.

    Selects the template by ``ctx.type`` (input_type). Falls back to
    ``_FALLBACK_TEMPLATE`` for unknown types.

    After substitution, empty placeholder lines are cleaned up so the
    output stays tidy when optional fields (e.g. description) are blank.
    """
    tpl = templates.get(ctx.type, _FALLBACK_TEMPLATE)

    result = tpl.format(
        time=ctx.time,
        type=ctx.type,
        description=ctx.description,
        content=ctx.content,
        image=ctx.image,
        link=ctx.link,
        quote=ctx.quote,
        pipeline=ctx.pipeline,
    )

    # Clean up blank lines from empty placeholders
    result = _cleanup_blank_lines(result)
    return result


def _cleanup_blank_lines(text: str) -> str:
    """Collapse 3+ consecutive blank lines down to 2, and strip trailing whitespace."""
    # Remove lines that are just whitespace
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        cleaned.append(line if line.strip() else "")
    # Collapse runs of blank lines (max 1 consecutive blank line)
    result_lines: list[str] = []
    prev_blank = False
    for line in cleaned:
        if line == "":
            if not prev_blank:
                result_lines.append(line)
            prev_blank = True
        else:
            prev_blank = False
            result_lines.append(line)
    return "\n".join(result_lines).strip()


# ------------------------------------------------------------------
# Template sets accessor (for future config-file override)
# ------------------------------------------------------------------

def get_entry_templates(config_templates: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Return entry templates, merging user overrides on top of defaults."""
    templates = dict(_DEFAULT_ENTRY_TEMPLATES)
    if config_templates:
        templates.update(config_templates)
    return templates


def get_obsidian_templates(config_templates: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Return Obsidian templates, merging user overrides on top of defaults."""
    templates = dict(_DEFAULT_OBSIDIAN_TEMPLATES)
    if config_templates:
        templates.update(config_templates)
    return templates


def get_timeline_templates(config_templates: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Return timeline templates, merging user overrides on top of defaults."""
    templates = dict(_DEFAULT_TIMELINE_TEMPLATES)
    if config_templates:
        templates.update(config_templates)
    return templates
