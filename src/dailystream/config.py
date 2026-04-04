"""Configuration management for DailyStream."""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


CONFIG_DIR = Path.home() / ".dailystream"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_WORKSPACE_ROOT = CONFIG_DIR / "workspaces"


@dataclass
class Config:
    """DailyStream configuration."""

    hotkey_screenshot: str = "<ctrl>+<shift>+s"
    hotkey_clipboard: str = "<ctrl>+<shift>+v"
    default_workspace_path: str = ""
    note_sync_backend: str = "markdown"  # "markdown" | "obsidian" | "both" | "none"
    obsidian_vault_path: str = ""

    @classmethod
    def load(cls) -> "Config":
        """Load config from file, create default if not exists."""
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
            except (json.JSONDecodeError, TypeError):
                pass
        config = cls()
        config.save()
        return config

    def save(self) -> None:
        """Save config to file."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


# --- JSON helpers ---

def read_json(path: Path) -> dict:
    """Read a JSON file, return empty dict if not exists."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def write_json(path: Path, data: dict) -> None:
    """Write dict to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# --- Time helpers ---

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
