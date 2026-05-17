"""Input capture module for DailyStream — screenshot and clipboard."""

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from .config import now_filename, CLIPBOARD_IMAGE_MARKER

logger = logging.getLogger(__name__)


# --- JPEG compression helper -----------------------------------------

# Emit the "Pillow not installed" warning only once per process so
# users aren't spammed on every capture.
_pillow_missing_warned = False


def _try_compress_to_jpeg(
    png_path: Path, quality: int = 85
) -> Optional[Path]:
    """Transcode ``png_path`` to JPEG next to it and delete the PNG.

    Returns the new ``.jpg`` path on success, or ``None`` if Pillow is
    unavailable or anything goes wrong.  The original PNG is kept on
    any failure so callers never lose data.

    Resolution is preserved exactly — only the codec changes.  That
    trades a small amount of quality for a ~5-10× smaller file, which
    is plenty to bring a retina-sized ``screencapture`` output from
    ~3 MB down to ~400 KB.
    """
    try:
        from PIL import Image as PILImage
    except ImportError:
        global _pillow_missing_warned
        if not _pillow_missing_warned:
            logger.warning(
                "screenshot_compress=on but Pillow is not installed; "
                "screenshots will be saved as PNG.  Install with "
                "'pip install dailystream[ai]' or 'pip install Pillow' "
                "to enable compression."
            )
            _pillow_missing_warned = True
        return None

    jpeg_path = png_path.with_suffix(".jpg")
    try:
        with PILImage.open(png_path) as img:
            # JPEG doesn't support alpha.  screencapture always writes
            # opaque images so this is a safe flatten.
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGB")
            img.save(
                jpeg_path,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=True,
            )
    except Exception as e:  # noqa: BLE001
        # Keep the PNG on any failure so the user never loses a shot.
        logger.warning("JPEG transcode failed (%s); keeping PNG: %s",
                       e, png_path)
        try:
            jpeg_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    # Transcode succeeded — remove the PNG.  If unlink fails (e.g.
    # read-only FS) we still return the JPEG path, but we log so the
    # user can clean up the duplicate later.
    try:
        png_path.unlink()
    except OSError as e:
        logger.warning("Could not remove source PNG after compress: %s", e)
    return jpeg_path


# --- Module-level ObjC overlay class (registered once) ---

_OverlayView = None  # lazily created on first use
_overlay_result: list[Optional[str]] = [None]  # shared result slot


def _get_overlay_view_class():
    """Return the _OverlayView ObjC class, creating it once."""
    global _OverlayView
    if _OverlayView is not None:
        return _OverlayView

    import AppKit

    class _OverlayViewImpl(AppKit.NSView):
        """Custom view that draws a selection rectangle."""

        _origin = None
        _current = None

        def acceptsFirstResponder(self):
            return True

        def canBecomeKeyView(self):
            return True

        def mouseDown_(self, event):
            self._origin = self.convertPoint_fromView_(event.locationInWindow(), None)
            self._current = self._origin
            self.setNeedsDisplay_(True)

        def mouseDragged_(self, event):
            self._current = self.convertPoint_fromView_(event.locationInWindow(), None)
            self.setNeedsDisplay_(True)

        def mouseUp_(self, event):
            self._current = self.convertPoint_fromView_(event.locationInWindow(), None)
            if self._origin and self._current:
                o = self._origin
                c = self._current
                x = int(min(o.x, c.x))
                # Convert from flipped AppKit coords → screen coords
                frame = self.window().frame()
                y_bottom = int(min(o.y, c.y))
                h = int(abs(c.y - o.y))
                w = int(abs(c.x - o.x))
                # AppKit y=0 is bottom; screen y=0 is top
                y = int(frame.size.height - y_bottom - h)
                if w > 5 and h > 5:
                    _overlay_result[0] = f"{x},{y},{w},{h}"
            AppKit.NSApp.stopModal()

        def keyDown_(self, event):
            # Escape key → cancel
            if event.keyCode() == 53:
                _overlay_result[0] = None
                AppKit.NSApp.stopModal()

        def drawRect_(self, rect):
            # Semi-transparent dark overlay
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0, 0, 0, 0.3).set()
            AppKit.NSBezierPath.fillRect_(self.bounds())

            if self._origin and self._current:
                o = self._origin
                c = self._current
                sel_rect = AppKit.NSMakeRect(
                    min(o.x, c.x),
                    min(o.y, c.y),
                    abs(c.x - o.x),
                    abs(c.y - o.y),
                )
                # Clear the selected region (show through)
                AppKit.NSColor.clearColor().set()
                AppKit.NSBezierPath.fillRect_(sel_rect)
                # Draw border
                AppKit.NSColor.whiteColor().set()
                path = AppKit.NSBezierPath.bezierPathWithRect_(sel_rect)
                path.setLineWidth_(2.0)
                path.stroke()

                # Draw dimensions label
                w = int(abs(c.x - o.x))
                h = int(abs(c.y - o.y))
                label = f"{w} × {h}"
                attrs = {
                    AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(14),
                    AppKit.NSForegroundColorAttributeName: AppKit.NSColor.whiteColor(),
                }
                ns_str = AppKit.NSAttributedString.alloc().initWithString_attributes_(
                    label, attrs
                )
                label_size = ns_str.size()
                label_x = min(o.x, c.x) + (abs(c.x - o.x) - label_size.width) / 2
                label_y = min(o.y, c.y) - label_size.height - 4
                if label_y < 0:
                    label_y = max(o.y, c.y) + 4
                ns_str.drawAtPoint_(AppKit.NSMakePoint(label_x, label_y))

    _OverlayView = _OverlayViewImpl
    return _OverlayView


def capture_screen_region() -> Optional[str]:
    """Let the user drag-select a screen region and return its coordinates.

    Opens a translucent full-screen overlay.  The user draws a rectangle by
    clicking and dragging; on mouse-up the region string ``"x,y,w,h"`` is
    returned (pixel coordinates in screen space).  Press **Escape** to cancel
    (returns ``None``).

    Uses PyObjC / AppKit — macOS only.
    """
    try:
        import AppKit
        import Quartz
    except ImportError:
        return None

    # Reset shared result
    _overlay_result[0] = None

    OverlayView = _get_overlay_view_class()

    # Get the main screen frame
    screen = AppKit.NSScreen.mainScreen()
    frame = screen.frame()

    # Create a borderless, full-screen, topmost window
    win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame,
        AppKit.NSWindowStyleMaskBorderless,
        AppKit.NSBackingStoreBuffered,
        False,
    )
    win.setLevel_(Quartz.kCGMaximumWindowLevelKey)
    win.setOpaque_(False)
    win.setBackgroundColor_(AppKit.NSColor.clearColor())
    win.setIgnoresMouseEvents_(False)
    win.setAcceptsMouseMovedEvents_(True)

    overlay = OverlayView.alloc().initWithFrame_(frame)
    win.setContentView_(overlay)
    win.makeFirstResponder_(overlay)
    win.makeKeyAndOrderFront_(None)

    # Ensure our app is frontmost
    AppKit.NSApp.activateIgnoringOtherApps_(True)

    # Run modal (blocks until mouse-up or Escape)
    AppKit.NSApp.runModalForWindow_(win)
    win.orderOut_(None)

    return _overlay_result[0]


def take_screenshot(
    save_dir: Path,
    mode: str = "interactive",
    region: Optional[str] = None,
    no_cursor: bool = False,
    compress: bool = False,
    compress_quality: int = 85,
) -> Optional[Path]:
    """Call macOS screencapture to capture screenshot.

    Args:
        save_dir: Directory to save screenshot
        mode: "interactive" for user selection, "fullscreen" for entire screen
        region: Optional region string "x,y,w,h" for preset capture area.
                When provided, ``screencapture -R x,y,w,h`` is used instead
                of interactive or fullscreen mode.
        no_cursor: When True, add ``-C`` so the mouse pointer is omitted
                from the captured image (matches the system
                ``hide_cursor`` attachment).
        compress: When True, transcode the PNG screencapture output to
                JPEG on disk (resolution preserved).  Requires Pillow;
                if unavailable the original PNG is kept and a one-time
                warning is logged.  Default ``False`` for backwards
                compatibility with callers that still expect PNG.
        compress_quality: JPEG quality 1-100 when ``compress`` is on.

    Saves to save_dir with a timestamped filename.
    Returns the screenshot path, or None if user cancelled.
    """
    save_dir.mkdir(parents=True, exist_ok=True)
    filename = f"screenshot_{now_filename()}.png"
    save_path = save_dir / filename

    cursor_flags = ["-C"] if no_cursor else []
    argv: list[str]

    try:
        if region:
            argv = ["screencapture", *cursor_flags, "-R", region,
                    str(save_path)]
            result = subprocess.run(argv, timeout=10,
                                    capture_output=True, text=True)
        elif mode == "fullscreen":
            argv = ["screencapture", *cursor_flags, str(save_path)]
            result = subprocess.run(argv, timeout=10,
                                    capture_output=True, text=True)
        else:
            # Interactive: user drags a selection.  Can take a long time;
            # rc=0 with no file means the user pressed ESC or clicked
            # without dragging.
            argv = ["screencapture", *cursor_flags, "-i", str(save_path)]
            result = subprocess.run(argv, timeout=120,
                                    capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        logger.warning("screencapture timed out (mode=%s, region=%s)",
                       mode, region)
        return None

    # screencapture returns 0 on success, 1 on explicit failure.  User
    # cancel in interactive mode returns 0 with no file.
    if save_path.exists() and save_path.stat().st_size > 0:
        if compress:
            jpeg = _try_compress_to_jpeg(save_path, quality=compress_quality)
            if jpeg is not None:
                return jpeg
        return save_path

    # Something went wrong — leave a breadcrumb so users can diagnose.
    logger.warning(
        "screencapture produced no file: rc=%s mode=%s region=%s "
        "argv=%s stdout=%r stderr=%r",
        result.returncode, mode, region, argv,
        (result.stdout or "").strip()[:200],
        (result.stderr or "").strip()[:200],
    )
    return None


def grab_clipboard() -> Tuple[Optional[str], str]:
    """Read current clipboard content.

    Returns (content, type) where type is 'url', 'image', or 'text'.
    Returns (None, 'text') if clipboard is empty.
    """
    # Try text first
    try:
        result = subprocess.run(
            ["pbpaste"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        text = result.stdout
        if text.strip():
            # Detect URL
            stripped = text.strip()
            if stripped.startswith(("http://", "https://")):
                return stripped, "url"
            return stripped, "text"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # Try image from clipboard (save as PNG)
    try:
        # Use osascript to check for image data in clipboard
        check = subprocess.run(
            [
                "osascript", "-e",
                'the clipboard info for (class PNGf)',
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if check.returncode == 0:
            # There's an image in clipboard — we'll handle it in the feed flow
            return CLIPBOARD_IMAGE_MARKER, "image"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None, "text"


def save_clipboard_image(
    save_dir: Path,
    compress: bool = False,
    compress_quality: int = 85,
) -> Optional[Path]:
    """Save clipboard image to a file. Returns path or None.

    When ``compress`` is on, the PNG written from the pasteboard is
    transcoded to JPEG (resolution preserved) to match
    ``take_screenshot``'s behaviour.  Pillow-less environments keep
    the PNG — see :func:`_try_compress_to_jpeg`.
    """
    save_dir.mkdir(parents=True, exist_ok=True)
    filename = f"clipboard_{now_filename()}.png"
    save_path = save_dir / filename

    try:
        # Use osascript + shell to extract PNG data from clipboard
        script = f'''
        use framework "AppKit"
        set pb to current application's NSPasteboard's generalPasteboard()
        set imgData to pb's dataForType:(current application's NSPasteboardTypePNG)
        if imgData is not missing value then
            imgData's writeToFile:"{save_path}" atomically:true
            return "ok"
        else
            return "no_image"
        end if
        '''
        result = subprocess.run(
            ["osascript", "-l", "AppleScript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if save_path.exists():
            if compress:
                jpeg = _try_compress_to_jpeg(
                    save_path, quality=compress_quality
                )
                if jpeg is not None:
                    return jpeg
            return save_path
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None
