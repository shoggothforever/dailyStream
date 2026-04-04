"""Input capture module for DailyStream — screenshot and clipboard."""

import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from .config import now_filename, CLIPBOARD_IMAGE_MARKER


def take_screenshot(save_dir: Path, mode: str = "interactive") -> Optional[Path]:
    """Call macOS screencapture to capture screenshot.

    Args:
        save_dir: Directory to save screenshot
        mode: "interactive" for user selection, "fullscreen" for entire screen
    
    Saves to save_dir with a timestamped filename.
    Returns the screenshot path, or None if user cancelled.
    """
    save_dir.mkdir(parents=True, exist_ok=True)
    filename = f"screenshot_{now_filename()}.png"
    save_path = save_dir / filename

    try:
        if mode == "fullscreen":
            # Capture entire screen without user interaction
            result = subprocess.run(
                ["screencapture", str(save_path)],
                timeout=10,
            )
        else:
            # Interactive mode: user selects region
            result = subprocess.run(
                ["screencapture", "-i", str(save_path)],
                timeout=120,  # generous timeout for user interaction
            )
    except subprocess.TimeoutExpired:
        return None

    # screencapture returns 0 on success, 1 if user pressed ESC
    if save_path.exists():
        return save_path
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


def save_clipboard_image(save_dir: Path) -> Optional[Path]:
    """Save clipboard image to a file. Returns path or None."""
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
            return save_path
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None
