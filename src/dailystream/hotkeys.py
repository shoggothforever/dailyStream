"""Global hotkey management for DailyStream.

Uses macOS native CGEventTap instead of pynput to avoid SIGTRAP crashes
caused by HIToolbox's dispatch_assert_queue when the input method switches
on a non-main thread.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional, Dict, Tuple

import Quartz

# ---- Hotkey string → (keycode, modifier_mask) ----

# macOS virtual key codes (US keyboard layout; hardware codes are layout-independent)
_KEY_CODES: Dict[str, int] = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7,
    "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14, "r": 15,
    "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22,
    "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28, "0": 29,
    "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37,
    "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44,
    "n": 45, "m": 46, ".": 47, "`": 50, " ": 49,
    "return": 36, "tab": 48, "space": 49, "delete": 51, "escape": 53,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
    "up": 126, "down": 125, "left": 123, "right": 124,
}

_MODIFIER_MAP: Dict[str, int] = {
    "<cmd>": Quartz.kCGEventFlagMaskCommand,
    "<command>": Quartz.kCGEventFlagMaskCommand,
    "<ctrl>": Quartz.kCGEventFlagMaskControl,
    "<control>": Quartz.kCGEventFlagMaskControl,
    "<shift>": Quartz.kCGEventFlagMaskShift,
    "<alt>": Quartz.kCGEventFlagMaskAlternate,
    "<option>": Quartz.kCGEventFlagMaskAlternate,
}

# Mask to keep only the modifier bits we care about
_MODIFIER_CARE_MASK = (
    Quartz.kCGEventFlagMaskCommand
    | Quartz.kCGEventFlagMaskControl
    | Quartz.kCGEventFlagMaskShift
    | Quartz.kCGEventFlagMaskAlternate
)


def _parse_hotkey(hotkey_str: str) -> Tuple[int, int]:
    """Parse a hotkey string like '<cmd>+1' or '`+2' into (keycode, modifier_mask).

    Returns (-1, 0) if parsing fails.
    """
    parts = hotkey_str.lower().strip().split("+")
    keycode = -1
    modifiers = 0

    for part in parts:
        part = part.strip()
        if part in _MODIFIER_MAP:
            modifiers |= _MODIFIER_MAP[part]
        elif part in _KEY_CODES:
            keycode = _KEY_CODES[part]
        else:
            # Unknown part
            return (-1, 0)

    if keycode == -1:
        # No valid key found
        return (-1, 0)

    return (keycode, modifiers)


class HotkeyManager:
    """Register and manage global hotkeys using macOS CGEventTap."""

    def __init__(
        self,
        on_screenshot: Callable,
        on_clipboard: Callable,
        hotkey_screenshot: str = "<cmd>+1",
        hotkey_clipboard: str = "<cmd>+2",
    ) -> None:
        self._on_screenshot = on_screenshot
        self._on_clipboard = on_clipboard

        self._ss_keycode, self._ss_modifiers = _parse_hotkey(hotkey_screenshot)
        self._cb_keycode, self._cb_modifiers = _parse_hotkey(hotkey_clipboard)

        self._tap: Optional[Any] = None  # type: ignore[assignment]
        self._run_loop_source = None
        self._thread: Optional[threading.Thread] = None
        self._running = False

    def start(self) -> None:
        """Start listening for hotkeys in a background thread with its own CFRunLoop."""
        if self._running:
            self.stop()

        # Create event tap for keyDown events
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,      # tap at session level
            Quartz.kCGHeadInsertEventTap,    # insert at head
            Quartz.kCGEventTapOptionListenOnly,  # passive (don't consume events)
            Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown),  # only keyDown
            self._tap_callback,
            None,  # user info
        )

        if self._tap is None:
            return  # accessibility permission not granted

        self._run_loop_source = Quartz.CFMachPortCreateRunLoopSource(
            None, self._tap, 0
        )

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def _run_loop(self) -> None:
        """Run a dedicated CFRunLoop for the event tap on a background thread."""
        rl = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(rl, self._run_loop_source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        # Run until stopped
        Quartz.CFRunLoopRun()

    def _tap_callback(self, proxy, event_type, event, refcon):
        """CGEventTap callback — invoked for each keyDown event."""
        if event_type != Quartz.kCGEventKeyDown:
            # If the tap gets disabled by the system (timeout), re-enable it
            if event_type == Quartz.kCGEventTapDisabledByTimeout:
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
            return event

        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        flags = Quartz.CGEventGetFlags(event) & _MODIFIER_CARE_MASK

        if keycode == self._ss_keycode and flags == self._ss_modifiers:
            # Fire screenshot callback on a separate thread to avoid blocking
            threading.Thread(target=self._safe_call, args=(self._on_screenshot,), daemon=True).start()
        elif keycode == self._cb_keycode and flags == self._cb_modifiers:
            threading.Thread(target=self._safe_call, args=(self._on_clipboard,), daemon=True).start()

        return event

    @staticmethod
    def _safe_call(fn: Callable) -> None:
        """Call fn with exception suppression."""
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()

    def stop(self) -> None:
        """Stop listening for hotkeys."""
        self._running = False

        if self._tap is not None:
            Quartz.CGEventTapEnable(self._tap, False)

        # Stop the CFRunLoop so the thread can exit
        if self._thread is not None and self._thread.is_alive():
            # Get the CFRunLoop of the background thread and stop it
            # We do this by invalidating the source, which will cause
            # CFRunLoopRun to return
            if self._run_loop_source is not None:
                # CFRunLoopStop needs the run loop ref, but we don't have it.
                # Instead, invalidate the source which will signal the loop.
                Quartz.CFRunLoopSourceInvalidate(self._run_loop_source)
            try:
                self._thread.join(timeout=2.0)
            except Exception:
                pass

        self._tap = None
        self._run_loop_source = None
        self._thread = None

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()
