"""Integration tests for config module.

Covers: Config load/save, read_json/write_json, short_time, now_iso,
        now_filename, get/set_active_workspace_path, CLIPBOARD_IMAGE_MARKER.
"""

import json
from pathlib import Path

import pytest

from dailystream.config import (
    Config,
    CLIPBOARD_IMAGE_MARKER,
    read_json,
    write_json,
    short_time,
    SHORT_TIME_PATTERN,
    now_iso,
    now_filename,
    get_active_workspace_path,
    set_active_workspace_path,
)


# ── JSON helpers ──────────────────────────────────────────────────────

class TestReadWriteJson:
    def test_roundtrip(self, tmp_path):
        """write_json → read_json should return identical data."""
        p = tmp_path / "data.json"
        data = {"key": "值", "nested": {"a": 1}}
        write_json(p, data)
        assert read_json(p) == data

    def test_read_nonexistent_returns_empty(self, tmp_path):
        assert read_json(tmp_path / "no_such.json") == {}

    def test_read_invalid_json_raises(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("{oops}", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            read_json(bad)

    def test_write_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "a" / "b" / "c" / "data.json"
        write_json(nested, {"ok": True})
        assert nested.exists()
        assert read_json(nested) == {"ok": True}

    def test_unicode_preserved(self, tmp_path):
        p = tmp_path / "cn.json"
        data = {"title": "清明时节", "emoji": "🎉"}
        write_json(p, data)
        assert read_json(p) == data


# ── Time helpers ──────────────────────────────────────────────────────

class TestTimeHelpers:
    def test_short_time_iso(self):
        assert short_time("2026-04-04T14:30:25.123+08:00") == "14:30:25"

    def test_short_time_no_t(self):
        """If no 'T' separator, returns as-is."""
        assert short_time("14:30:25") == "14:30:25"

    def test_short_time_pattern_alias(self):
        assert SHORT_TIME_PATTERN is short_time

    def test_now_iso_format(self):
        ts = now_iso()
        assert "T" in ts
        # Should contain timezone offset
        assert "+" in ts or "Z" in ts

    def test_now_filename_format(self):
        fn = now_filename()
        # e.g. 20260405_011200
        assert len(fn) == 15
        assert fn[8] == "_"
        assert fn[:8].isdigit()
        assert fn[9:].isdigit()


# ── Config load / save ────────────────────────────────────────────────

class TestConfig:
    def test_load_default(self, tmp_config_dir):
        """Loading config when no file exists creates a default."""
        cfg = Config.load()
        assert cfg.screenshot_mode == "interactive"
        assert cfg.note_sync_backend == "markdown"

    def test_save_and_reload(self, tmp_config_dir):
        cfg = Config.load()
        cfg.screenshot_mode = "fullscreen"
        cfg.save()

        cfg2 = Config.load()
        assert cfg2.screenshot_mode == "fullscreen"

    def test_unknown_keys_ignored(self, tmp_config_dir):
        """Extra keys in JSON should be silently ignored."""
        from dailystream.config import CONFIG_FILE
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps({"screenshot_mode": "fullscreen", "bogus_key": 123}),
            encoding="utf-8",
        )
        cfg = Config.load()
        assert cfg.screenshot_mode == "fullscreen"
        assert not hasattr(cfg, "bogus_key")

    def test_corrupt_json_returns_default(self, tmp_config_dir):
        from dailystream.config import CONFIG_FILE
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text("NOT JSON", encoding="utf-8")
        cfg = Config.load()
        assert cfg.screenshot_mode == "interactive"  # default


# ── State (active workspace) ─────────────────────────────────────────

class TestActiveWorkspaceState:
    def test_set_and_get(self, tmp_config_dir, tmp_path):
        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()

        set_active_workspace_path(ws_dir)
        result = get_active_workspace_path()
        assert result == ws_dir

    def test_clear(self, tmp_config_dir, tmp_path):
        ws_dir = tmp_path / "ws"
        ws_dir.mkdir()
        set_active_workspace_path(ws_dir)
        set_active_workspace_path(None)
        assert get_active_workspace_path() is None

    def test_nonexistent_path_returns_none(self, tmp_config_dir):
        set_active_workspace_path(Path("/nonexistent/path"))
        assert get_active_workspace_path() is None


# ── Screenshot presets in Config ──────────────────────────────────────

class TestScreenshotPresets:
    def test_presets_default_is_none(self, tmp_config_dir):
        cfg = Config.load()
        assert cfg.screenshot_presets is None

    def test_save_and_load_presets(self, tmp_config_dir):
        cfg = Config.load()
        cfg.screenshot_presets = [
            {"name": "Left", "region": "0,0,960,1080"},
            {"name": "Right", "region": "960,0,960,1080", "hotkey": "<cmd>+4"},
        ]
        cfg.save()

        cfg2 = Config.load()
        assert cfg2.screenshot_presets is not None
        assert len(cfg2.screenshot_presets) == 2
        assert cfg2.screenshot_presets[0]["name"] == "Left"
        assert cfg2.screenshot_presets[1]["hotkey"] == "<cmd>+4"

    def test_save_none_presets(self, tmp_config_dir):
        """Setting presets to None should persist and reload as None."""
        cfg = Config.load()
        cfg.screenshot_presets = [{"name": "Temp", "region": "0,0,1,1"}]
        cfg.save()

        cfg.screenshot_presets = None
        cfg.save()

        cfg2 = Config.load()
        assert cfg2.screenshot_presets is None

    def test_preset_with_all_fields(self, tmp_config_dir):
        cfg = Config.load()
        cfg.screenshot_presets = [
            {"name": "Full", "region": "100,200,800,600", "hotkey": "<cmd>+3"},
        ]
        cfg.save()

        cfg2 = Config.load()
        p = cfg2.screenshot_presets[0]
        assert p["name"] == "Full"
        assert p["region"] == "100,200,800,600"
        assert p["hotkey"] == "<cmd>+3"

    def test_empty_list_presets(self, tmp_config_dir):
        """An empty list should be preserved as empty list, not None."""
        cfg = Config.load()
        cfg.screenshot_presets = []
        cfg.save()

        cfg2 = Config.load()
        assert cfg2.screenshot_presets == []


# ── Constants ─────────────────────────────────────────────────────────

def test_clipboard_image_marker():
    assert CLIPBOARD_IMAGE_MARKER == "__clipboard_image__"
