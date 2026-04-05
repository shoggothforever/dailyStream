"""Tests for the capture module.

Covers: take_screenshot, grab_clipboard, save_clipboard_image,
        _get_overlay_view_class lazy singleton pattern.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from dailystream.capture import (
    take_screenshot,
    grab_clipboard,
    save_clipboard_image,
    _get_overlay_view_class,
    _overlay_result,
)


# ── take_screenshot ──────────────────────────────────────────────────

class TestTakeScreenshot:
    def test_interactive_mode_success(self, tmp_path):
        """Simulate successful interactive screencapture."""
        save_dir = tmp_path / "screenshots"

        def fake_run(cmd, **kwargs):
            # Simulate screencapture creating the file
            save_dir.mkdir(parents=True, exist_ok=True)
            # Find the output path from the command
            path = Path(cmd[-1])
            path.write_bytes(b"PNG_DATA")
            return subprocess.CompletedProcess(cmd, 0)

        with patch("dailystream.capture.subprocess.run", side_effect=fake_run):
            result = take_screenshot(save_dir, mode="interactive")

        assert result is not None
        assert result.exists()
        assert result.suffix == ".png"
        assert "screenshot_" in result.name

    def test_fullscreen_mode_invokes_correct_command(self, tmp_path):
        """Fullscreen mode should NOT pass -i flag."""
        save_dir = tmp_path / "screenshots"
        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            save_dir.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"PNG")
            return subprocess.CompletedProcess(cmd, 0)

        with patch("dailystream.capture.subprocess.run", side_effect=fake_run):
            take_screenshot(save_dir, mode="fullscreen")

        assert "-i" not in captured_cmd
        assert "screencapture" in captured_cmd

    def test_region_mode_passes_dash_R(self, tmp_path):
        """When region is provided, -R flag with coordinates should be used."""
        save_dir = tmp_path / "screenshots"
        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            save_dir.mkdir(parents=True, exist_ok=True)
            Path(cmd[-1]).write_bytes(b"PNG")
            return subprocess.CompletedProcess(cmd, 0)

        with patch("dailystream.capture.subprocess.run", side_effect=fake_run):
            take_screenshot(save_dir, region="100,200,800,600")

        assert "-R" in captured_cmd
        assert "100,200,800,600" in captured_cmd

    def test_user_cancel_returns_none(self, tmp_path):
        """If user presses Escape, no file is created → returns None."""
        save_dir = tmp_path / "screenshots"

        def fake_run(cmd, **kwargs):
            # Simulate user cancel: screencapture exits with 1, no file
            return subprocess.CompletedProcess(cmd, 1)

        with patch("dailystream.capture.subprocess.run", side_effect=fake_run):
            result = take_screenshot(save_dir, mode="interactive")

        assert result is None

    def test_timeout_returns_none(self, tmp_path):
        """TimeoutExpired should return None."""
        save_dir = tmp_path / "screenshots"

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 10))

        with patch("dailystream.capture.subprocess.run", side_effect=fake_run):
            result = take_screenshot(save_dir, mode="interactive")

        assert result is None

    def test_creates_save_dir(self, tmp_path):
        """save_dir should be created if it doesn't exist."""
        save_dir = tmp_path / "deep" / "nested" / "screenshots"
        assert not save_dir.exists()

        def fake_run(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"PNG")
            return subprocess.CompletedProcess(cmd, 0)

        with patch("dailystream.capture.subprocess.run", side_effect=fake_run):
            take_screenshot(save_dir)

        assert save_dir.exists()


# ── grab_clipboard ───────────────────────────────────────────────────

class TestGrabClipboard:
    def test_text_content(self):
        """Plain text clipboard content."""
        result = subprocess.CompletedProcess(["pbpaste"], 0, stdout="Hello world")

        with patch("dailystream.capture.subprocess.run", return_value=result):
            content, content_type = grab_clipboard()

        assert content == "Hello world"
        assert content_type == "text"

    def test_url_detected(self):
        """URLs starting with http(s):// should be detected."""
        result = subprocess.CompletedProcess(
            ["pbpaste"], 0, stdout="https://example.com/page"
        )

        with patch("dailystream.capture.subprocess.run", return_value=result):
            content, content_type = grab_clipboard()

        assert content == "https://example.com/page"
        assert content_type == "url"

    def test_http_url_detected(self):
        result = subprocess.CompletedProcess(
            ["pbpaste"], 0, stdout="http://example.com"
        )

        with patch("dailystream.capture.subprocess.run", return_value=result):
            content, content_type = grab_clipboard()

        assert content == "http://example.com"
        assert content_type == "url"

    def test_empty_clipboard(self):
        """Empty clipboard text → fall through to image check, then None."""
        pbpaste = subprocess.CompletedProcess(["pbpaste"], 0, stdout="")
        osascript = subprocess.CompletedProcess(["osascript"], 1, stdout="")

        with patch("dailystream.capture.subprocess.run", side_effect=[pbpaste, osascript]):
            content, content_type = grab_clipboard()

        assert content is None
        assert content_type == "text"

    def test_image_in_clipboard(self):
        """When text is empty but image data exists → return marker."""
        from dailystream.config import CLIPBOARD_IMAGE_MARKER

        pbpaste = subprocess.CompletedProcess(["pbpaste"], 0, stdout="  ")
        osascript = subprocess.CompletedProcess(
            ["osascript"], 0, stdout="«class PNGf», 12345"
        )

        with patch("dailystream.capture.subprocess.run", side_effect=[pbpaste, osascript]):
            content, content_type = grab_clipboard()

        assert content == CLIPBOARD_IMAGE_MARKER
        assert content_type == "image"

    def test_whitespace_only_text_is_empty(self):
        """Whitespace-only text should be treated as empty."""
        pbpaste = subprocess.CompletedProcess(["pbpaste"], 0, stdout="   \n  ")
        osascript = subprocess.CompletedProcess(["osascript"], 1, stdout="")

        with patch("dailystream.capture.subprocess.run", side_effect=[pbpaste, osascript]):
            content, content_type = grab_clipboard()

        assert content is None

    def test_pbpaste_timeout(self):
        """Timeout on pbpaste → fall through to image check."""
        osascript = subprocess.CompletedProcess(["osascript"], 1, stdout="")

        def side_effect(cmd, **kwargs):
            if cmd[0] == "pbpaste":
                raise subprocess.TimeoutExpired(cmd, 5)
            return osascript

        with patch("dailystream.capture.subprocess.run", side_effect=side_effect):
            content, content_type = grab_clipboard()

        assert content is None
        assert content_type == "text"

    def test_url_with_whitespace_stripped(self):
        """URL with surrounding whitespace should be stripped."""
        result = subprocess.CompletedProcess(
            ["pbpaste"], 0, stdout="  https://example.com  \n"
        )

        with patch("dailystream.capture.subprocess.run", return_value=result):
            content, content_type = grab_clipboard()

        assert content == "https://example.com"
        assert content_type == "url"


# ── save_clipboard_image ─────────────────────────────────────────────

class TestSaveClipboardImage:
    def test_success(self, tmp_path):
        save_dir = tmp_path / "screenshots"

        def fake_run(cmd, **kwargs):
            # The osascript writes the file; simulate that
            save_dir.mkdir(parents=True, exist_ok=True)
            # Find filename in the script text
            for c in cmd:
                if "clipboard_" in str(c):
                    break
            # Just create a file in save_dir matching the pattern
            import glob
            # Actually, the save_path is constructed inside the function,
            # so we need to check the script for the path
            script = cmd[-1] if len(cmd) > 1 else ""
            # Extract path from the AppleScript
            if "writeToFile:" in script:
                import re
                m = re.search(r'writeToFile:"([^"]+)"', script)
                if m:
                    Path(m.group(1)).parent.mkdir(parents=True, exist_ok=True)
                    Path(m.group(1)).write_bytes(b"PNG_IMG_DATA")
            return subprocess.CompletedProcess(cmd, 0, stdout="ok")

        with patch("dailystream.capture.subprocess.run", side_effect=fake_run):
            result = save_clipboard_image(save_dir)

        assert result is not None
        assert result.exists()
        assert "clipboard_" in result.name

    def test_no_image_returns_none(self, tmp_path):
        save_dir = tmp_path / "screenshots"

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="no_image")

        with patch("dailystream.capture.subprocess.run", side_effect=fake_run):
            result = save_clipboard_image(save_dir)

        assert result is None

    def test_timeout_returns_none(self, tmp_path):
        save_dir = tmp_path / "screenshots"

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 10)

        with patch("dailystream.capture.subprocess.run", side_effect=fake_run):
            result = save_clipboard_image(save_dir)

        assert result is None

    def test_creates_save_dir(self, tmp_path):
        save_dir = tmp_path / "deep" / "nested" / "dir"
        assert not save_dir.exists()

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout="no_image")

        with patch("dailystream.capture.subprocess.run", side_effect=fake_run):
            save_clipboard_image(save_dir)

        assert save_dir.exists()


# ── _get_overlay_view_class lazy singleton ───────────────────────────

class TestOverlayViewLazySingleton:
    def test_returns_same_class_on_repeated_calls(self):
        """The lazy factory must return the same class every time."""
        cls1 = _get_overlay_view_class()
        cls2 = _get_overlay_view_class()
        assert cls1 is cls2

    def test_class_has_required_methods(self):
        """The returned class should have NSView event methods."""
        cls = _get_overlay_view_class()
        for method_name in [
            "mouseDown_",
            "mouseDragged_",
            "mouseUp_",
            "keyDown_",
            "drawRect_",
            "acceptsFirstResponder",
            "canBecomeKeyView",
        ]:
            assert hasattr(cls, method_name), f"Missing method: {method_name}"

    def test_overlay_result_is_module_level_list(self):
        """_overlay_result should be a mutable list (shared state)."""
        assert isinstance(_overlay_result, list)
        assert len(_overlay_result) == 1
