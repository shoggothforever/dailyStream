"""Global hotkey management for DailyStream."""

from typing import Callable, Optional
from pynput.keyboard import GlobalHotKeys


class HotkeyManager:
    """Register and manage global hotkeys."""

    def __init__(
        self,
        on_screenshot: Callable,
        on_clipboard: Callable,
        hotkey_screenshot: str = "<ctrl>+<shift>+s",
        hotkey_clipboard: str = "<ctrl>+<shift>+v",
    ) -> None:
        self._on_screenshot = on_screenshot
        self._on_clipboard = on_clipboard
        self._hotkey_screenshot = hotkey_screenshot
        self._hotkey_clipboard = hotkey_clipboard
        self._listener: Optional[GlobalHotKeys] = None

    def start(self) -> None:
        """Start listening for hotkeys in a background thread."""
        if self._listener is not None:
            self.stop()

        self._listener = GlobalHotKeys({
            self._hotkey_screenshot: self._on_screenshot,
            self._hotkey_clipboard: self._on_clipboard,
        })
        self._listener.daemon = True
        self._listener.start()

    def stop(self) -> None:
        """Stop listening for hotkeys."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    @property
    def is_running(self) -> bool:
        return self._listener is not None and self._listener.is_alive()
