"""Configuration management for DailyStream."""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .capture_modes.models import ModesState


CONFIG_DIR = Path.home() / ".dailystream"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_WORKSPACE_ROOT = CONFIG_DIR / "workspaces"
CLIPBOARD_IMAGE_MARKER = "__clipboard_image__"


@dataclass
class Config:
    """DailyStream configuration."""

    hotkey_screenshot: str = "<ctrl>+<shift>+s"
    hotkey_clipboard: str = "<ctrl>+<shift>+v"
    screenshot_mode: str = "interactive"  # "interactive" | "fullscreen"
    screenshot_save_path: str = ""  # Custom screenshot save location. Empty = <workspace>/screenshots/
    # Predefined screenshot regions.
    # Each item: {"name": "...", "region": "x,y,w,h", "hotkey": "<cmd>+3"}
    # "hotkey" is optional — when set, pressing that key combo captures the
    # region instantly without opening a menu.
    # Example: [{"name": "Left Half", "region": "0,0,960,1080", "hotkey": "<cmd>+3"}]
    # When presets are defined, user can pick one or fall back to free selection.
    screenshot_presets: Optional[list[dict[str, str]]] = None
    default_workspace_path: str = ""
    note_sync_backend: str = "markdown"  # "markdown" | "obsidian" | "both" | "none"
    obsidian_vault_path: str = ""

    # Customisable Markdown templates per input_type.
    # Keys: "image", "url", "text" (or any custom type).
    # Available placeholders: {time}, {type}, {description}, {content},
    #   {image}, {link}, {quote}, {pipeline}
    #   AI placeholders (when ai_mode != "off"):
    #   {ai_analysis}, {ai_category}, {ai_elements}
    # Set to None / omit to use built-in defaults.
    entry_templates: Optional[dict[str, str]] = None
    obsidian_templates: Optional[dict[str, str]] = None
    timeline_templates: Optional[dict[str, str]] = None

    # --- AI analysis configuration ---
    # NOTE: ai_mode ("off"/"realtime"/"daily_report") is a workspace-level
    # attribute stored in workspace_meta.json, NOT here.
    ai_api_key: str = ""  # Anthropic API key (env DAILYSTREAM_AI_KEY takes priority)
    ai_model: str = "claude-sonnet-4-20250514"
    ai_timeout: int = 30  # seconds
    ai_prompt: str = ""  # custom analysis prompt (empty = use built-in default)
    ai_max_image_size_kb: int = 150  # compress images before sending to AI
    ai_batch_size: int = 10  # max images per batch in daily_report mode
    ai_default_mode: str = "off"  # default ai_mode when creating a new workspace

    # --- Capture Mode Designer state (not a dataclass field — kept as
    # an attribute so it survives ``asdict`` without clobbering the
    # legacy ``screenshot_*`` fields).  Populated by :meth:`load` via
    # the capture_modes migration helpers.
    #
    # ``capture_modes`` is intentionally declared *outside* the
    # ``@dataclass`` fields so existing call sites that ``asdict`` the
    # config won't crash on a new field, and legacy readers stay happy.

    @classmethod
    def load(cls) -> "Config":
        """Load config from file, create default if not exists.

        Also materialises the Capture Mode Designer state
        (``capture_modes``), running a one-shot migration from the
        legacy ``screenshot_mode`` + ``screenshot_presets`` fields when
        needed.  Legacy fields are retained on disk for rollback.
        """
        raw: dict = {}
        if CONFIG_FILE.exists():
            try:
                raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raw = {}
            except (json.JSONDecodeError, TypeError):
                raw = {}

        field_names = set(cls.__dataclass_fields__)
        config = cls(**{k: v for k, v in raw.items() if k in field_names})

        from .capture_modes.migrations import migrate_legacy_presets

        try:
            state, did_migrate = migrate_legacy_presets(raw)
        except Exception:  # noqa: BLE001
            from .capture_modes.migrations import default_modes
            state = default_modes()
            did_migrate = True

        # Store on the instance (not a dataclass field).
        config.capture_modes = state

        # Persist the first time so users see the new structure in
        # config.json right away.
        if did_migrate or not CONFIG_FILE.exists():
            try:
                config.save()
            except Exception:  # noqa: BLE001
                # Saving is best-effort; keep running in-memory.
                pass
        return config

    def save(self) -> None:
        """Save config to file (including ``capture_modes`` state)."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = asdict(self)

        state: Optional["ModesState"] = getattr(self, "capture_modes", None)
        if state is not None:
            data["capture_modes"] = state.to_dict()

        CONFIG_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


# --- JSON helpers ---

def read_json(path: Path) -> dict:
    """Read a JSON file, return empty dict if not exists.
    
    Raises:
        JSONDecodeError: if file exists but is not valid JSON
    """
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(
                f"Invalid JSON in {path}: {e.msg}",
                e.doc,
                e.pos,
            ) from e
    return {}


def write_json(path: Path, data: dict) -> None:
    """Write dict to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# --- Time helpers ---

def short_time(timestamp: str) -> str:
    """Extract short time string (HH:MM:SS) from ISO 8601 timestamp."""
    return timestamp.split("T")[1][:8] if "T" in timestamp else timestamp


# Alias for readability in timeline
SHORT_TIME_PATTERN = short_time


def now_iso() -> str:
    """Return current time as ISO 8601 string."""
    return datetime.now(timezone.utc).astimezone().isoformat()


def now_filename() -> str:
    """Return current time formatted for filenames."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# --- State file (tracks current active workspace) ---

STATE_FILE = CONFIG_DIR / "state.json"


def get_active_workspace_path() -> Optional[Path]:
    """Get the path to the currently active workspace, or None."""
    state = read_json(STATE_FILE)
    wp = state.get("active_workspace_path")
    if wp and Path(wp).exists():
        return Path(wp)
    return None


def set_active_workspace_path(path: Optional[Path]) -> None:
    """Set (or clear) the active workspace path."""
    state = read_json(STATE_FILE)
    state["active_workspace_path"] = str(path) if path else None
    write_json(STATE_FILE, state)
