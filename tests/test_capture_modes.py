"""End-to-end tests for the Capture Mode Designer."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dailystream.capture_modes import (
    ATTACHMENT_CATALOG,
    Attachment,
    AttachmentKind,
    CaptureExecutor,
    ExecutionContext,
    Mode,
    ModesState,
    Preset,
    Source,
    SourceKind,
    catalog_as_list,
    default_modes,
    migrate_legacy_presets,
    validate_attachments,
)


# ---------------------------------------------------------------------------
# Data model + JSON round-trip
# ---------------------------------------------------------------------------


class TestModelsRoundTrip:
    def test_default_modes_contains_defaults(self):
        state = default_modes()
        assert state.active_mode_id == "default"
        assert len(state.modes) == 1
        default = state.modes[0]
        ids = [p.id for p in default.presets]
        assert "free-selection" in ids
        assert "clipboard" in ids

    def test_modesstate_round_trip(self):
        state = default_modes()
        state.modes[0].presets.append(
            Preset(
                id="custom",
                name="Custom",
                source=Source(kind=SourceKind.FULLSCREEN),
                attachments=[
                    Attachment(id="burst", params={"count": 4, "interval_ms": 150}),
                    Attachment(id="silent_save"),
                    Attachment(id="current_pipeline"),
                ],
                hotkey="<option>+5",
            )
        )
        payload = state.to_dict()
        reloaded = ModesState.from_dict(payload)
        assert reloaded.to_dict() == payload

    def test_from_dict_tolerates_garbage(self):
        state = ModesState.from_dict("not a dict")  # type: ignore[arg-type]
        assert state.modes == []
        assert state.active_mode_id is None


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


class TestMigration:
    def test_empty_config(self):
        state, did = migrate_legacy_presets({})
        assert did
        assert state.active_mode_id == "default"
        assert any(p.id == "free-selection" for p in state.modes[0].presets)

    def test_legacy_presets_are_folded_into_default_mode(self):
        raw = {
            "screenshot_mode": "fullscreen",
            "screenshot_presets": [
                {"name": "Top Half", "region": "0,0,2000,540", "hotkey": "<cmd>+5"},
                {"name": "Bottom Half", "region": "0,540,2000,540"},
            ],
        }
        state, did = migrate_legacy_presets(raw)
        assert did
        names = {p.name for p in state.modes[0].presets}
        assert {"Free Selection", "Clipboard", "Top Half", "Bottom Half"} <= names
        # The legacy screenshot_mode is reflected on the free-selection preset.
        free = next(p for p in state.modes[0].presets if p.id == "free-selection")
        assert free.source.kind == SourceKind.FULLSCREEN

    def test_existing_capture_modes_wins(self):
        base, _ = migrate_legacy_presets({})
        raw = {"capture_modes": base.to_dict()}
        state, did = migrate_legacy_presets(raw)
        assert not did
        assert state.to_dict() == base.to_dict()


# ---------------------------------------------------------------------------
# Catalog + validation
# ---------------------------------------------------------------------------


class TestCatalog:
    def test_catalog_has_all_requested_kinds(self):
        kinds = {spec.kind for spec in ATTACHMENT_CATALOG.values()}
        assert AttachmentKind.STRATEGY in kinds
        assert AttachmentKind.FEEDBACK in kinds
        assert AttachmentKind.WINDOW_CTRL in kinds
        assert AttachmentKind.POST in kinds
        assert AttachmentKind.DELIVERY in kinds

    def test_catalog_is_serialisable(self):
        data = catalog_as_list()
        assert isinstance(data, list)
        json.dumps(data)  # must be JSON-clean

    def test_validate_strategy_is_single_choice(self):
        errs = validate_attachments([
            Attachment(id="single"),
            Attachment(id="burst"),
        ])
        assert any("strategy" in e.lower() for e in errs)

    def test_validate_unknown_id(self):
        errs = validate_attachments([Attachment(id="ghost")])
        assert any("Unknown" in e for e in errs)

    def test_validate_ok(self):
        errs = validate_attachments([
            Attachment(id="single"),
            Attachment(id="silent_save"),
            Attachment(id="flash_menubar"),
            Attachment(id="current_pipeline"),
        ])
        assert errs == []


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    def test_fresh_config_gets_default_modes(self, tmp_config_dir):
        from dailystream.config import Config

        cfg = Config.load()
        assert cfg.capture_modes is not None
        assert cfg.capture_modes.active_mode_id == "default"

        saved = json.loads(
            (tmp_config_dir / "config.json").read_text(encoding="utf-8")
        )
        assert "capture_modes" in saved
        assert saved["capture_modes"]["active_mode_id"] == "default"

    def test_config_round_trip_preserves_capture_modes(self, tmp_config_dir):
        from dailystream.config import Config

        cfg = Config.load()
        # Add a custom mode and save.
        cfg.capture_modes.modes.append(
            Mode(
                id="focus",
                name="Focus",
                presets=[Preset(
                    id="silent-shot",
                    name="Silent Shot",
                    source=Source(kind=SourceKind.FULLSCREEN),
                    attachments=[
                        Attachment(id="single"),
                        Attachment(id="silent_save"),
                        Attachment(id="current_pipeline"),
                    ],
                )],
            )
        )
        cfg.save()

        cfg2 = Config.load()
        ids = [m.id for m in cfg2.capture_modes.modes]
        assert "focus" in ids
        focus = next(m for m in cfg2.capture_modes.modes if m.id == "focus")
        assert focus.presets[0].attachments[1].id == "silent_save"

    def test_legacy_config_is_migrated_on_load(self, tmp_config_dir):
        # Write a legacy-only config file (no capture_modes key).
        legacy = {
            "screenshot_mode": "interactive",
            "screenshot_presets": [
                {"name": "Left", "region": "0,0,960,1080", "hotkey": "<cmd>+7"},
            ],
        }
        (tmp_config_dir / "config.json").write_text(
            json.dumps(legacy), encoding="utf-8"
        )

        from dailystream.config import Config

        cfg = Config.load()
        ids = [p.name for p in cfg.capture_modes.modes[0].presets]
        assert "Left" in ids
        # Post-migration the file now has capture_modes written back.
        reloaded = json.loads(
            (tmp_config_dir / "config.json").read_text(encoding="utf-8")
        )
        assert "capture_modes" in reloaded


# ---------------------------------------------------------------------------
# Executor (stub source layer)
# ---------------------------------------------------------------------------


class _StubPM:
    def __init__(self, root: Path):
        self._root = root

    def get_screenshots_dir(self) -> Path:
        return self._root


class _StubWM:
    is_active = True

    def get_active_pipeline(self):
        return "main"


@pytest.fixture()
def stub_capture(monkeypatch, tmp_path):
    """Replace capture.take_screenshot / save_clipboard_image with fakes."""
    def _fake_screenshot(save_dir, mode="interactive", region=None,
                         no_cursor=False):
        p = save_dir / "fake.png"
        save_dir.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        return p

    def _fake_clip(save_dir):
        p = save_dir / "fake_clip.png"
        save_dir.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        return p

    monkeypatch.setattr("dailystream.capture.take_screenshot", _fake_screenshot)
    monkeypatch.setattr("dailystream.capture.save_clipboard_image", _fake_clip)
    return tmp_path


class TestExecutor:
    def test_single_shot(self, stub_capture):
        events = []
        ctx = ExecutionContext(
            wm=_StubWM(),
            pm=_StubPM(stub_capture),
            publish_event=lambda m, p: events.append((m, p)),
            mode_id="default",
        )
        preset = Preset(
            id="p", name="P",
            source=Source(kind=SourceKind.FULLSCREEN),
            attachments=[Attachment(id="single"),
                         Attachment(id="current_pipeline")],
        )
        report = CaptureExecutor().execute(preset, ctx)
        assert len(report.frames) == 1
        assert report.frames[0].path is not None
        assert report.frames[0].source_kind == "fullscreen"
        assert report.silent is False

    def test_burst_emits_feedback(self, stub_capture):
        events = []
        ctx = ExecutionContext(
            wm=_StubWM(),
            pm=_StubPM(stub_capture),
            publish_event=lambda m, p: events.append((m, p)),
            mode_id="default",
        )
        preset = Preset(
            id="p", name="P",
            source=Source(kind=SourceKind.FULLSCREEN),
            attachments=[
                Attachment(id="burst", params={"count": 3, "interval_ms": 5}),
                Attachment(id="flash_menubar"),
                Attachment(id="silent_save"),
            ],
        )
        report = CaptureExecutor().execute(preset, ctx)
        assert len(report.frames) == 3
        assert report.silent is True
        flashes = [e for e in events if e[0] == "capture.flash_menubar"]
        assert len(flashes) == 3

    def test_region_without_coords_skipped(self, stub_capture):
        ctx = ExecutionContext(
            wm=_StubWM(),
            pm=_StubPM(stub_capture),
            publish_event=lambda m, p: None,
            mode_id="default",
        )
        preset = Preset(
            id="p", name="P",
            source=Source(kind=SourceKind.REGION, region=None),
            attachments=[Attachment(id="single")],
        )
        report = CaptureExecutor().execute(preset, ctx)
        assert report.frames[0].skipped is True

    def test_run_command_success(self, stub_capture, tmp_path):
        events = []
        ctx = ExecutionContext(
            wm=_StubWM(),
            pm=_StubPM(stub_capture),
            publish_event=lambda m, p: events.append((m, p)),
            mode_id="default",
            preset_name="HookTest",
        )
        marker = tmp_path / "marker.txt"
        # Inline shell writing frame path + preset name into a marker file
        cmd = (
            f"echo \"$DAILYSTREAM_PRESET_NAME:$DAILYSTREAM_FRAME_PATH\" "
            f"> {marker}"
        )
        preset = Preset(
            id="p", name="HookTest",
            source=Source(kind=SourceKind.FULLSCREEN),
            attachments=[
                Attachment(id="single"),
                Attachment(id="run_command",
                           params={"command": cmd, "wait": True,
                                   "timeout_seconds": 5}),
            ],
        )
        CaptureExecutor().execute(preset, ctx)
        assert marker.exists()
        contents = marker.read_text(encoding="utf-8")
        assert "HookTest:" in contents
        assert str(stub_capture) in contents
        # No hook_failed event
        assert not any(e[0] == "capture.hook_failed" for e in events)

    def test_run_command_failure_emits_event(self, stub_capture):
        events = []
        ctx = ExecutionContext(
            wm=_StubWM(),
            pm=_StubPM(stub_capture),
            publish_event=lambda m, p: events.append((m, p)),
            mode_id="default",
        )
        preset = Preset(
            id="p", name="Bad",
            source=Source(kind=SourceKind.FULLSCREEN),
            attachments=[
                Attachment(id="single"),
                Attachment(id="run_command",
                           params={"command": "exit 7", "wait": True,
                                   "timeout_seconds": 5}),
            ],
        )
        CaptureExecutor().execute(preset, ctx)
        failures = [p for m, p in events if m == "capture.hook_failed"]
        assert len(failures) == 1
        assert failures[0]["returncode"] == 7

    def test_hide_cursor_passes_no_cursor_flag(self, stub_capture,
                                               monkeypatch):
        """hide_cursor attachment must forward no_cursor=True to screencapture."""
        seen: dict = {}

        def _spy(save_dir, mode="interactive", region=None,
                 no_cursor=False):
            seen["no_cursor"] = no_cursor
            seen["mode"] = mode
            p = save_dir / "fake.png"
            save_dir.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
            return p

        monkeypatch.setattr("dailystream.capture.take_screenshot", _spy)

        ctx = ExecutionContext(
            wm=_StubWM(),
            pm=_StubPM(stub_capture),
            publish_event=lambda m, p: None,
            mode_id="default",
        )
        preset = Preset(
            id="p", name="P",
            source=Source(kind=SourceKind.FULLSCREEN),
            attachments=[
                Attachment(id="single"),
                Attachment(id="hide_cursor"),
            ],
        )
        CaptureExecutor().execute(preset, ctx)
        assert seen.get("no_cursor") is True
