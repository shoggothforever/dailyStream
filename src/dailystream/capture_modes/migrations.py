"""Migration helpers: convert legacy ``screenshot_mode`` + ``screenshot_presets``
into the new :class:`ModesState` structure.

Legacy config looked like::

    screenshot_mode: "interactive" | "fullscreen"
    screenshot_presets: [{"name": "...", "region": "x,y,w,h", "hotkey": "<cmd>+3"}]

After migration we produce a single built-in ``Default`` Mode that
contains:

1. ``Free Selection`` preset bound to ``<cmd>+1`` (matches the existing
   KeyboardShortcuts ``.screenshot`` default) using the legacy
   ``screenshot_mode`` as its source kind.
2. ``Clipboard`` preset bound to ``<cmd>+2``.
3. One :class:`Preset` per legacy entry in ``screenshot_presets``
   (hotkey preserved verbatim, source = REGION).

The user never sees the old fields again — they stay on disk for
rollback but the authoritative representation is now
``config.capture_modes``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .models import (
    Attachment,
    Mode,
    ModesState,
    Preset,
    Source,
    SourceKind,
    _slugify,
)

logger = logging.getLogger(__name__)

# Legacy fields that feed the migration.  Kept as constants so callers
# can diff them against the config in tests.
LEGACY_FIELDS = ("screenshot_mode", "screenshot_presets")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def default_modes() -> ModesState:
    """Return a fresh :class:`ModesState` with only the built-in Default mode.

    Used when no legacy data is present either.
    """
    return ModesState(
        modes=[_build_default_mode(legacy_mode="interactive", legacy_presets=[])],
        active_mode_id="default",
    )


def migrate_legacy_presets(
    raw_config: dict[str, Any],
) -> tuple[ModesState, bool]:
    """Build a :class:`ModesState` from a raw config dict.

    Returns (state, did_migrate):

    * If ``raw_config`` already contains a valid ``capture_modes`` key
      we simply parse it and return (state, False).
    * Otherwise we synthesise a Default mode from the legacy fields
      and return (state, True).  Callers should ``Config.save()``
      afterwards to persist the new shape.

    Never raises; falls back to :func:`default_modes` on any
    unexpected shape.
    """
    try:
        existing = raw_config.get("capture_modes")
        if isinstance(existing, dict) and existing.get("modes"):
            state = ModesState.from_dict(existing)
            if state.modes:
                return state, False
    except Exception:  # noqa: BLE001
        logger.exception("Failed to parse existing capture_modes; regenerating")

    legacy_mode = str(raw_config.get("screenshot_mode") or "interactive")
    legacy_presets_raw = raw_config.get("screenshot_presets") or []
    if not isinstance(legacy_presets_raw, list):
        legacy_presets_raw = []

    default_mode = _build_default_mode(
        legacy_mode=legacy_mode,
        legacy_presets=legacy_presets_raw,
    )

    state = ModesState(modes=[default_mode], active_mode_id=default_mode.id)
    return state, True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_default_mode(
    legacy_mode: str,
    legacy_presets: list[Any],
) -> Mode:
    presets: list[Preset] = [
        _free_selection_preset(legacy_mode),
        _clipboard_preset(),
    ]

    for idx, raw in enumerate(legacy_presets):
        preset = _legacy_preset_to_new(raw, fallback_idx=idx)
        if preset is not None:
            presets.append(preset)

    return Mode(
        id="default",
        name="Default",
        emoji="🗂",
        presets=presets,
    )


def _free_selection_preset(legacy_mode: str) -> Preset:
    """Preset mirroring the historical ⌘1 behaviour."""
    # We intentionally do NOT pin the hotkey to "<cmd>+1" here — the
    # KeyboardShortcuts library remembers the user's custom binding
    # inside UserDefaults.  Leaving ``hotkey=None`` means "use whatever
    # the legacy ``screenshot`` shortcut name is bound to".
    try:
        kind = SourceKind(legacy_mode)
    except ValueError:
        kind = SourceKind.INTERACTIVE
    # Only interactive / fullscreen are legal sources here; anything
    # else is normalised to interactive.
    if kind not in (SourceKind.INTERACTIVE, SourceKind.FULLSCREEN):
        kind = SourceKind.INTERACTIVE
    return Preset(
        id="free-selection",
        name="Free Selection",
        emoji="✂️",
        source=Source(kind=kind),
        attachments=[
            Attachment(id="single", params={}),
            Attachment(id="current_pipeline", params={}),
        ],
        hotkey=None,   # owned by KeyboardShortcuts.Name.screenshot
    )


def _clipboard_preset() -> Preset:
    """Preset mirroring the historical ⌘2 behaviour."""
    return Preset(
        id="clipboard",
        name="Clipboard",
        emoji="📋",
        source=Source(kind=SourceKind.CLIPBOARD),
        attachments=[
            Attachment(id="single", params={}),
            Attachment(id="current_pipeline", params={}),
        ],
        hotkey=None,   # owned by KeyboardShortcuts.Name.clipboardCapture
    )


def _legacy_preset_to_new(raw: Any, fallback_idx: int) -> Optional[Preset]:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or f"Preset {fallback_idx + 1}")
    region = raw.get("region")
    if not isinstance(region, str) or not region.strip():
        return None
    hotkey_raw = raw.get("hotkey")
    hotkey: Optional[str] = None
    if isinstance(hotkey_raw, str) and hotkey_raw.strip():
        hotkey = hotkey_raw.strip()
    pid = _slugify(name) or f"preset-{fallback_idx + 1}"
    return Preset(
        id=pid,
        name=name,
        emoji="🖼",
        source=Source(kind=SourceKind.REGION, region=region.strip()),
        attachments=[
            Attachment(id="single", params={}),
            Attachment(id="current_pipeline", params={}),
        ],
        hotkey=hotkey,
    )
