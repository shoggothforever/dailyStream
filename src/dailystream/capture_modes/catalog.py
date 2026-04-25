"""Predefined Attachment catalog.

The catalog is the **source of truth** for what atomic capabilities the
user can pick when building a Preset.  New capabilities are added by
appending to :data:`ATTACHMENT_CATALOG` *and* writing a handler in
``capture_modes/handlers/``.

The Designer UI (Swift side) pulls this catalog via the
``capture_modes.list_attachment_catalog`` RPC so it can render the
checkboxes + parameter forms without hard-coding anything.

Schema of an :class:`AttachmentSpec`:

* ``id``:              unique string used in :class:`Attachment.id`
* ``kind``:            category; drives single/multi-choice UI
* ``label``:           human-readable title for the UI
* ``description``:     one-line helper text
* ``icon``:            SF Symbol hint (Swift side)
* ``params_schema``:   mapping of param_name → (type, default, help)
* ``mutually_exclusive``: optional list of attachment IDs that cannot
                          co-exist with this one *across* kinds
                          (within-kind single-choice is enforced by
                          :class:`AttachmentKind.single_choice`)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .models import Attachment, AttachmentKind


@dataclass(frozen=True)
class ParamSpec:
    """Parameter schema entry (used by Designer UI + runtime validation)."""

    kind: str          # "int" | "float" | "bool" | "string" | "string_list" | "enum"
    default: Any
    help: str = ""
    enum_values: tuple[str, ...] = ()
    min: Optional[float] = None
    max: Optional[float] = None

    def to_dict(self) -> dict:
        d: dict = {"kind": self.kind, "default": self.default}
        if self.help:
            d["help"] = self.help
        if self.enum_values:
            d["enum"] = list(self.enum_values)
        if self.min is not None:
            d["min"] = self.min
        if self.max is not None:
            d["max"] = self.max
        return d


@dataclass(frozen=True)
class AttachmentSpec:
    """Static metadata about one predefined atomic capability."""

    id: str
    kind: AttachmentKind
    label: str
    description: str = ""
    icon: str = "circle"
    params_schema: dict[str, ParamSpec] = field(default_factory=dict)
    mutually_exclusive: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind.value,
            "label": self.label,
            "description": self.description,
            "icon": self.icon,
            "params_schema": {
                k: v.to_dict() for k, v in self.params_schema.items()
            },
            "mutually_exclusive": list(self.mutually_exclusive),
        }


# ---------------------------------------------------------------------------
# Catalog entries
# ---------------------------------------------------------------------------


ATTACHMENT_CATALOG: dict[str, AttachmentSpec] = {}


def _register(spec: AttachmentSpec) -> None:
    if spec.id in ATTACHMENT_CATALOG:
        raise RuntimeError(f"Duplicate attachment id: {spec.id}")
    ATTACHMENT_CATALOG[spec.id] = spec


# -- STRATEGY (single-choice) ----------------------------------------------

_register(AttachmentSpec(
    id="single",
    kind=AttachmentKind.STRATEGY,
    label="Single Shot",
    description="Capture exactly one frame.",
    icon="camera",
))

_register(AttachmentSpec(
    id="burst",
    kind=AttachmentKind.STRATEGY,
    label="Burst",
    description="Capture N frames at a fixed interval — great for fleeting moments.",
    icon="camera.on.rectangle",
    params_schema={
        "count": ParamSpec(kind="int", default=3, help="Frames to capture", min=2, max=30),
        "interval_ms": ParamSpec(kind="int", default=200, help="Milliseconds between frames", min=50, max=5000),
    },
))

_register(AttachmentSpec(
    id="interval",
    kind=AttachmentKind.STRATEGY,
    label="Interval Timer",
    description="Capture every N seconds until stopped from the menu bar.",
    icon="timer",
    params_schema={
        "seconds": ParamSpec(kind="int", default=60, help="Seconds between frames", min=1, max=3600),
        "max_count": ParamSpec(kind="int", default=0, help="Auto-stop after N frames (0 = unlimited)", min=0, max=1000),
    },
))


# -- FEEDBACK (multi-choice) -----------------------------------------------

_register(AttachmentSpec(
    id="silent_save",
    kind=AttachmentKind.FEEDBACK,
    label="Silent Save",
    description="Skip the description HUD; save directly to the active pipeline.",
    icon="speaker.slash",
))

_register(AttachmentSpec(
    id="flash_menubar",
    kind=AttachmentKind.FEEDBACK,
    label="Flash Menu Bar",
    description="Briefly flash the menu-bar icon so you know the shot was captured.",
    icon="bolt.horizontal",
))

_register(AttachmentSpec(
    id="sound",
    kind=AttachmentKind.FEEDBACK,
    label="Shutter Sound",
    description="Play a soft shutter sound effect on capture.",
    icon="speaker.wave.2",
    params_schema={
        "volume": ParamSpec(kind="float", default=0.5, help="0.0 – 1.0", min=0.0, max=1.0),
    },
))

_register(AttachmentSpec(
    id="notification",
    kind=AttachmentKind.FEEDBACK,
    label="System Notification",
    description="Post a macOS notification after every frame is captured.",
    icon="bell",
))


# -- WINDOW_CTRL (multi-choice) --------------------------------------------

_register(AttachmentSpec(
    id="hide_cursor",
    kind=AttachmentKind.WINDOW_CTRL,
    label="Hide Cursor",
    description="Do not include the mouse pointer in the captured image.",
    icon="cursorarrow.slash",
))

_register(AttachmentSpec(
    id="hide_dock",
    kind=AttachmentKind.WINDOW_CTRL,
    label="Hide Dock",
    description="Enable Dock auto-hide during capture (restored afterwards).",
    icon="dock.rectangle",
))


# -- POST (multi-choice, order matters) ------------------------------------

_register(AttachmentSpec(
    id="auto_ocr",
    kind=AttachmentKind.POST,
    label="Auto OCR",
    description="Run Vision OCR on the captured image and attach the text.",
    icon="text.viewfinder",
))

_register(AttachmentSpec(
    id="quick_tags",
    kind=AttachmentKind.POST,
    label="Quick Tags",
    description="Offer a 2-second window after capture to press a key and tag the shot.",
    icon="tag",
    params_schema={
        "window_seconds": ParamSpec(kind="float", default=2.0, help="How long the tag window stays open", min=0.5, max=10.0),
        "tags": ParamSpec(
            kind="tag_list",
            default=[
                {"key": "1", "label": "Highlight", "emoji": "🏆"},
                {"key": "2", "label": "Death",     "emoji": "💀"},
                {"key": "3", "label": "Review",    "emoji": "🤔"},
                {"key": "4", "label": "Funny",     "emoji": "😂"},
                {"key": "5", "label": "TODO",      "emoji": "📝"},
            ],
            help="Each tag binds a single key to a label + emoji",
        ),
    },
))

_register(AttachmentSpec(
    id="auto_copy_clipboard",
    kind=AttachmentKind.POST,
    label="Copy To Clipboard",
    description="Copy the captured image onto the system clipboard.",
    icon="doc.on.clipboard",
))

_register(AttachmentSpec(
    id="ai_analyze",
    kind=AttachmentKind.POST,
    label="AI Analyze",
    description=(
        "Send the frame to the configured Claude model and optionally "
        "prefill the description HUD with the result.  Also populates "
        "post_artifacts (ai_description / ai_description_raw / "
        "ai_category / ai_key_elements) so a later ``run_command`` "
        "can read them from DAILYSTREAM_AI_* environment variables."
    ),
    icon="sparkles",
    params_schema={
        "user_hint": ParamSpec(
            kind="string",
            default="Describe what the user is working on.",
            help="Extra instruction sent to the model as a user hint",
        ),
        "prefill_hud": ParamSpec(
            kind="bool",
            default=True,
            help="Prefill the Screenshot HUD description with the AI output",
        ),
        "save_to_analysis": ParamSpec(
            kind="bool",
            default=True,
            help="Persist the result into ai_analyses.json for the pipeline",
        ),
        "wait": ParamSpec(
            kind="bool",
            default=True,
            help="Wait for the analysis before capturing the next frame",
        ),
    },
))

_register(AttachmentSpec(
    id="run_command",
    kind=AttachmentKind.POST,
    label="Run Command",
    description=(
        "Run a custom shell command / script after each frame.  "
        "Receives full context via DAILYSTREAM_* environment variables "
        "(frame path, OCR text, AI description / category / tags, "
        "artifacts JSON blob).  Always executed last in the POST chain "
        "so all upstream producers are visible."
    ),
    icon="terminal",
    params_schema={
        "command": ParamSpec(
            kind="file_or_command",
            default="",
            help="Path to an executable script (use Browse) or an inline shell command",
        ),
        "wait": ParamSpec(
            kind="bool",
            default=False,
            help="When true, block the pipeline until the command finishes",
        ),
        "timeout_seconds": ParamSpec(
            kind="int",
            default=30,
            help="Kill the command if it runs longer than this",
            min=1, max=600,
        ),
        "wait_for_ai_seconds": ParamSpec(
            kind="int",
            default=0,
            help=(
                "Give an async AI Analyze up to N seconds to finish "
                "before firing.  0 = don't wait (fire immediately)."
            ),
            min=0, max=120,
        ),
    },
))


# -- DELIVERY (single-choice) ----------------------------------------------

_register(AttachmentSpec(
    id="current_pipeline",
    kind=AttachmentKind.DELIVERY,
    label="Active Pipeline",
    description="Send the capture to whichever pipeline is currently active.",
    icon="arrow.right.circle",
))


# ---------------------------------------------------------------------------
# Lookup + validation
# ---------------------------------------------------------------------------


def catalog_as_list() -> list[dict]:
    """Return the catalog as a list of dicts (for RPC responses)."""
    return [spec.to_dict() for spec in ATTACHMENT_CATALOG.values()]


def validate_attachments(attachments: list[Attachment]) -> list[str]:
    """Return a list of human-readable violation messages (empty = OK).

    Rules enforced:
        * Each attachment id is known.
        * STRATEGY / DELIVERY at most one per preset.
        * No ``mutually_exclusive`` pairs co-exist.
    """
    errors: list[str] = []
    seen_single: dict[AttachmentKind, str] = {}
    seen_ids: set[str] = set()

    for att in attachments:
        spec = ATTACHMENT_CATALOG.get(att.id)
        if spec is None:
            errors.append(f"Unknown attachment id: {att.id}")
            continue

        if spec.kind in AttachmentKind.single_choice():
            prev = seen_single.get(spec.kind)
            if prev is not None:
                errors.append(
                    f"Only one {spec.kind.value} attachment allowed; "
                    f"got both '{prev}' and '{att.id}'"
                )
            else:
                seen_single[spec.kind] = att.id

        for exclusive in spec.mutually_exclusive:
            if exclusive in seen_ids:
                errors.append(
                    f"Attachment '{att.id}' is incompatible with '{exclusive}'"
                )
        seen_ids.add(att.id)

    return errors
