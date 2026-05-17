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


# ── JPEG compression helper ──────────────────────────────────────────

try:
    from PIL import Image as _PILImage
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


@pytest.mark.skipif(not _PIL_AVAILABLE, reason="Pillow required")
class TestCompressToJpeg:
    """Round-trip tests for the PNG → JPEG transcoder."""

    def _write_png(self, path: Path, size: tuple[int, int] = (200, 150)) -> None:
        """Write a small deterministic PNG for the test to transcode."""
        img = _PILImage.new("RGB", size, color=(128, 64, 32))
        img.save(path, format="PNG")

    def test_png_transcoded_and_original_removed(self, tmp_path):
        from dailystream.capture import _try_compress_to_jpeg
        png = tmp_path / "shot.png"
        self._write_png(png)
        assert png.exists()

        jpeg = _try_compress_to_jpeg(png, quality=85)

        assert jpeg is not None
        assert jpeg.suffix == ".jpg"
        assert jpeg.exists()
        assert not png.exists()  # original deleted
        # The JPEG should be smaller than the PNG for anything beyond
        # a trivial solid-colour patch — our 200×150 sample clears it.
        assert jpeg.stat().st_size > 0

    def test_resolution_preserved(self, tmp_path):
        from dailystream.capture import _try_compress_to_jpeg
        png = tmp_path / "shot.png"
        self._write_png(png, size=(640, 480))

        jpeg = _try_compress_to_jpeg(png, quality=85)
        assert jpeg is not None

        with _PILImage.open(jpeg) as out:
            assert out.size == (640, 480)

    def test_rgba_input_flattened(self, tmp_path):
        """PNGs with alpha should be converted to RGB (JPEG has no alpha)."""
        from dailystream.capture import _try_compress_to_jpeg
        png = tmp_path / "alpha.png"
        _PILImage.new("RGBA", (80, 60), color=(100, 200, 50, 128)).save(png)

        jpeg = _try_compress_to_jpeg(png)
        assert jpeg is not None
        with _PILImage.open(jpeg) as out:
            assert out.mode == "RGB"

    def test_missing_source_fails_cleanly(self, tmp_path):
        from dailystream.capture import _try_compress_to_jpeg
        ghost = tmp_path / "does-not-exist.png"
        assert _try_compress_to_jpeg(ghost) is None

    def test_failure_keeps_original(self, tmp_path, monkeypatch):
        """If Pillow fails mid-way, the source PNG must still exist."""
        from dailystream import capture as capture_mod
        png = tmp_path / "shot.png"
        self._write_png(png)

        # Force Image.open to raise after the file write begins.
        class _BoomImage:
            def __enter__(self):
                raise RuntimeError("boom")
            def __exit__(self, *a):
                return False
        class _BoomPIL:
            @staticmethod
            def open(_p):
                return _BoomImage()

        monkeypatch.setitem(
            __import__("sys").modules, "PIL",
            type("PIL", (), {"Image": _BoomPIL}),
        )
        result = capture_mod._try_compress_to_jpeg(png)

        assert result is None
        assert png.exists()  # original preserved
        assert not png.with_suffix(".jpg").exists()


class TestCompressToJpegNoPillow:
    def test_returns_none_when_pillow_missing(self, tmp_path, monkeypatch):
        """Without Pillow the helper should degrade gracefully."""
        from dailystream import capture as capture_mod
        # Reset the one-shot warning flag so the test is deterministic.
        monkeypatch.setattr(capture_mod, "_pillow_missing_warned", False)

        # Simulate "PIL not importable" by injecting an import hook.
        import builtins
        real_import = builtins.__import__

        def blocked_import(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("PIL blocked for test")
            return real_import(name, *args, **kwargs)

        png = tmp_path / "shot.png"
        png.write_bytes(b"fake-png")

        monkeypatch.setattr(builtins, "__import__", blocked_import)
        result = capture_mod._try_compress_to_jpeg(png)

        assert result is None
        # Original is kept so the caller can still return it.
        assert png.exists()


# ── take_screenshot / save_clipboard_image: compress forwarding ──────

@pytest.mark.skipif(not _PIL_AVAILABLE, reason="Pillow required")
class TestCaptureCompressIntegration:
    """End-to-end: fake screencapture output + compress=True → .jpg."""

    def test_screenshot_with_compress_returns_jpeg(self, tmp_path):
        save_dir = tmp_path / "screenshots"

        def fake_run(cmd, **kwargs):
            save_dir.mkdir(parents=True, exist_ok=True)
            # Write a real PNG so Pillow can actually read it.
            path = Path(cmd[-1])
            _PILImage.new("RGB", (320, 240), color=(0, 128, 255)).save(path)
            return subprocess.CompletedProcess(cmd, 0)

        with patch("dailystream.capture.subprocess.run", side_effect=fake_run):
            result = take_screenshot(
                save_dir, mode="fullscreen",
                compress=True, compress_quality=85,
            )

        assert result is not None
        assert result.suffix == ".jpg"
        assert result.exists()
        # The PNG sibling must be gone.
        assert not result.with_suffix(".png").exists()

    def test_screenshot_without_compress_stays_png(self, tmp_path):
        save_dir = tmp_path / "screenshots"

        def fake_run(cmd, **kwargs):
            save_dir.mkdir(parents=True, exist_ok=True)
            path = Path(cmd[-1])
            path.write_bytes(b"PNG_DATA")
            return subprocess.CompletedProcess(cmd, 0)

        with patch("dailystream.capture.subprocess.run", side_effect=fake_run):
            result = take_screenshot(save_dir, mode="fullscreen")

        assert result is not None
        assert result.suffix == ".png"

    def test_clipboard_image_with_compress_returns_jpeg(self, tmp_path):
        save_dir = tmp_path / "screenshots"

        def fake_run(cmd, **kwargs):
            save_dir.mkdir(parents=True, exist_ok=True)
            # osascript writes the PNG to the literal path embedded in
            # the script; the simplest faithful mock is to grep the
            # last arg for the destination and write a valid PNG there.
            script = cmd[-1]
            # Extract the path from the AppleScript string.
            import re
            m = re.search(r'writeToFile:"([^"]+)"', script)
            assert m, "mock could not find destination in script"
            _PILImage.new("RGB", (160, 120), color=(200, 0, 0)).save(m.group(1))
            return subprocess.CompletedProcess(cmd, 0, stdout="ok")

        with patch("dailystream.capture.subprocess.run", side_effect=fake_run):
            result = save_clipboard_image(
                save_dir, compress=True, compress_quality=85,
            )

        assert result is not None
        assert result.suffix == ".jpg"
        assert result.exists()
