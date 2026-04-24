"""Data model for the Capture Mode Designer.

Three-layer architecture::

    Mode (container) ──┐
                        └── Preset (recipe) ─── Source + [Attachment, ...] + hotkey
                                                                │
                                                                └── Attachment
                                                                     (atomic capability)

Every dataclass here is JSON-serialisable via :meth:`to_dict` /
:meth:`from_dict` so we can persist the full state inside
``~/.dailystream/config.json`` under the ``capture_modes`` key.

Conventions
-----------
* IDs are short slugs — lowercased, hyphen-separated.  The Swift UI
  generates them from ``name`` on create; the Python side does **not**
  regenerate to keep stable references.
* Unknown fields from future versions are ignored (forward-compat).
* Missing required fields fall back to sensible defaults so a partially
  migrated config still loads.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class AttachmentKind(str, Enum):
    """Category of an Attachment — drives UI grouping + combination rules.

    * ``STRATEGY`` and ``DELIVERY`` are **single-choice** (at most one
      attachment of each kind per preset).
    * ``FEEDBACK``, ``WINDOW_CTRL`` and ``POST`` are **multi-choice**
      (any subset allowed, order matters for POST only).
    """

    STRATEGY = "strategy"
    FEEDBACK = "feedback"
    WINDOW_CTRL = "window_ctrl"
    POST = "post"
    DELIVERY = "delivery"

    @classmethod
    def single_choice(cls) -> set["AttachmentKind"]:
        return {cls.STRATEGY, cls.DELIVERY}


class SourceKind(str, Enum):
    """Where the capture image / content comes from."""

    INTERACTIVE = "interactive"    # interactive drag-to-select overlay
    FULLSCREEN = "fullscreen"
    REGION = "region"              # fixed x,y,w,h
    WINDOW = "window"              # reserved for future — use interactive for now
    CLIPBOARD = "clipboard"        # grab current clipboard instead of screen


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass
class Source:
    """Defines *where* the capture comes from.

    Only ``REGION`` uses the ``region`` field (an ``"x,y,w,h"`` string
    for backward compat with the legacy preset format).
    """

    kind: SourceKind = SourceKind.INTERACTIVE
    region: Optional[str] = None   # "x,y,w,h" — only valid when kind == REGION

    def to_dict(self) -> dict:
        d: dict = {"kind": self.kind.value}
        if self.region is not None:
            d["region"] = self.region
        return d

    @classmethod
    def from_dict(cls, data: Any) -> "Source":
        if not isinstance(data, dict):
            return cls()
        try:
            kind = SourceKind(str(data.get("kind", "interactive")))
        except ValueError:
            kind = SourceKind.INTERACTIVE
        region = data.get("region")
        return cls(
            kind=kind,
            region=str(region) if region is not None else None,
        )


@dataclass
class Attachment:
    """A single atomic capability plugged into a Preset.

    ``id`` must match a key in ``ATTACHMENT_CATALOG``; ``params`` is a
    free-form JSON-compatible payload validated against the catalog's
    schema when :func:`validate_attachments` is called.
    """

    id: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, data: Any) -> Optional["Attachment"]:
        if not isinstance(data, dict):
            return None
        aid = data.get("id")
        if not isinstance(aid, str) or not aid:
            return None
        raw = data.get("params") or {}
        if not isinstance(raw, dict):
            raw = {}
        return cls(id=aid, params=dict(raw))


@dataclass
class Preset:
    """A named capture recipe — Source + Attachments + hotkey."""

    id: str
    name: str
    emoji: str = "📸"
    source: Source = field(default_factory=Source)
    attachments: list[Attachment] = field(default_factory=list)
    # Hotkey string e.g. "<cmd>+1" / "<option>+3".  None = not bound.
    hotkey: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "emoji": self.emoji,
            "source": self.source.to_dict(),
            "attachments": [a.to_dict() for a in self.attachments],
            "hotkey": self.hotkey,
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["Preset"]:
        if not isinstance(data, dict):
            return None
        pid = data.get("id") or _slugify(data.get("name", ""))
        if not pid:
            pid = f"preset-{uuid.uuid4().hex[:8]}"
        name = str(data.get("name") or pid)
        emoji = str(data.get("emoji") or "📸")
        source = Source.from_dict(data.get("source") or {})
        atts_raw = data.get("attachments") or []
        attachments: list[Attachment] = []
        if isinstance(atts_raw, list):
            for item in atts_raw:
                att = Attachment.from_dict(item)
                if att is not None:
                    attachments.append(att)
        hotkey = data.get("hotkey")
        if hotkey is not None and not isinstance(hotkey, str):
            hotkey = None
        if hotkey == "":
            hotkey = None
        return cls(
            id=pid,
            name=name,
            emoji=emoji,
            source=source,
            attachments=attachments,
            hotkey=hotkey,
        )


@dataclass
class Mode:
    """A collection of Presets.  Only one Mode is active at a time."""

    id: str
    name: str
    emoji: str = "🗂"
    presets: list[Preset] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "emoji": self.emoji,
            "presets": [p.to_dict() for p in self.presets],
        }

    @classmethod
    def from_dict(cls, data: Any) -> Optional["Mode"]:
        if not isinstance(data, dict):
            return None
        mid = data.get("id") or _slugify(data.get("name", ""))
        if not mid:
            mid = f"mode-{uuid.uuid4().hex[:8]}"
        name = str(data.get("name") or mid)
        emoji = str(data.get("emoji") or "🗂")
        presets_raw = data.get("presets") or []
        presets: list[Preset] = []
        if isinstance(presets_raw, list):
            for item in presets_raw:
                p = Preset.from_dict(item)
                if p is not None:
                    presets.append(p)
        return cls(id=mid, name=name, emoji=emoji, presets=presets)


@dataclass
class ModesState:
    """Top-level container persisted inside ``config.json``."""

    modes: list[Mode] = field(default_factory=list)
    active_mode_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "modes": [m.to_dict() for m in self.modes],
            "active_mode_id": self.active_mode_id,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ModesState":
        if not isinstance(data, dict):
            return cls()
        modes_raw = data.get("modes") or []
        modes: list[Mode] = []
        if isinstance(modes_raw, list):
            for item in modes_raw:
                m = Mode.from_dict(item)
                if m is not None:
                    modes.append(m)
        active = data.get("active_mode_id")
        if active is not None and not isinstance(active, str):
            active = None
        if active and not any(m.id == active for m in modes):
            active = modes[0].id if modes else None
        if active is None and modes:
            active = modes[0].id
        return cls(modes=modes, active_mode_id=active)

    # -- Mutating helpers ----------------------------------------------

    def get_active(self) -> Optional[Mode]:
        if self.active_mode_id is None:
            return None
        for m in self.modes:
            if m.id == self.active_mode_id:
                return m
        return None

    def find_preset(self, mode_id: str, preset_id: str) -> Optional[Preset]:
        for m in self.modes:
            if m.id != mode_id:
                continue
            for p in m.presets:
                if p.id == preset_id:
                    return p
            return None
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def _slugify(raw: str) -> str:
    """Deterministic id-from-name helper."""
    out: list[str] = []
    for ch in raw.strip().lower():
        if ch in _SAFE_CHARS:
            out.append(ch)
        elif ch.isspace() or ch in "_":
            out.append("-")
    slug = "".join(out).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug
