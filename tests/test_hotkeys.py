"""Tests for the hotkeys module.

Covers: _parse_hotkey parsing, HotkeyManager extra-hotkey registration,
        unregister_extra, clear_extras, thread-safety of extras.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from dailystream.hotkeys import _parse_hotkey, HotkeyManager


# ── _parse_hotkey ──────────────────────────────────────────────────────

class TestParseHotkey:
    """Test hotkey string → (keycode, modifier_mask) parsing."""

    def test_simple_cmd_number(self):
        keycode, mods = _parse_hotkey("<cmd>+1")
        assert keycode == 18  # '1' keycode
        assert mods != 0  # should have Command modifier

    def test_cmd_plus_letter(self):
        keycode, mods = _parse_hotkey("<cmd>+a")
        assert keycode == 0  # 'a' keycode
        assert mods != 0

    def test_ctrl_shift_combination(self):
        keycode, mods = _parse_hotkey("<ctrl>+<shift>+s")
        assert keycode == 1  # 's' keycode
        # Both Control and Shift should be set
        import Quartz
        assert mods & Quartz.kCGEventFlagMaskControl
        assert mods & Quartz.kCGEventFlagMaskShift

    def test_alt_option_alias(self):
        """<alt> and <option> should produce the same modifier."""
        _, mods_alt = _parse_hotkey("<alt>+a")
        _, mods_opt = _parse_hotkey("<option>+a")
        assert mods_alt == mods_opt

    def test_command_alias(self):
        """<cmd> and <command> should produce the same modifier."""
        _, mods_cmd = _parse_hotkey("<cmd>+a")
        _, mods_command = _parse_hotkey("<command>+a")
        assert mods_cmd == mods_command

    def test_function_key(self):
        keycode, mods = _parse_hotkey("<cmd>+f1")
        assert keycode == 122  # F1 keycode
        assert mods != 0

    def test_special_keys(self):
        keycode, _ = _parse_hotkey("<cmd>+return")
        assert keycode == 36

        keycode2, _ = _parse_hotkey("<cmd>+escape")
        assert keycode2 == 53

        keycode3, _ = _parse_hotkey("<cmd>+space")
        assert keycode3 == 49

    def test_arrow_keys(self):
        keycode, _ = _parse_hotkey("<cmd>+up")
        assert keycode == 126

        keycode2, _ = _parse_hotkey("<cmd>+down")
        assert keycode2 == 125

    def test_no_key_returns_invalid(self):
        """Only modifiers, no key → invalid."""
        keycode, mods = _parse_hotkey("<cmd>+<shift>")
        assert keycode == -1
        assert mods == 0

    def test_unknown_part_returns_invalid(self):
        keycode, mods = _parse_hotkey("<cmd>+unknown_key")
        assert keycode == -1
        assert mods == 0

    def test_empty_string_returns_invalid(self):
        keycode, mods = _parse_hotkey("")
        assert keycode == -1
        assert mods == 0

    def test_no_modifier_just_key(self):
        """A single key with no modifier is still valid (keycode != -1)."""
        keycode, mods = _parse_hotkey("a")
        assert keycode == 0
        assert mods == 0

    def test_case_insensitive(self):
        """Parsing should be case-insensitive."""
        kc1, m1 = _parse_hotkey("<CMD>+A")
        kc2, m2 = _parse_hotkey("<cmd>+a")
        assert kc1 == kc2
        assert m1 == m2

    def test_whitespace_tolerance(self):
        """Spaces around parts should be tolerated."""
        kc1, m1 = _parse_hotkey(" <cmd> + 1 ")
        kc2, m2 = _parse_hotkey("<cmd>+1")
        assert kc1 == kc2
        assert m1 == m2

    def test_multiple_modifiers_combined(self):
        """Three modifiers + key should work."""
        import Quartz
        keycode, mods = _parse_hotkey("<cmd>+<ctrl>+<shift>+a")
        assert keycode == 0
        assert mods & Quartz.kCGEventFlagMaskCommand
        assert mods & Quartz.kCGEventFlagMaskControl
        assert mods & Quartz.kCGEventFlagMaskShift


# ── HotkeyManager extra-hotkey management ─────────────────────────────

class TestHotkeyManagerExtras:
    """Test register_extra / unregister_extra / clear_extras without
    actually starting the CGEventTap (which requires accessibility permissions).
    """

    def _make_manager(self):
        """Create a HotkeyManager with dummy callbacks (don't start it)."""
        return HotkeyManager(
            on_screenshot=MagicMock(),
            on_clipboard=MagicMock(),
        )

    def test_register_extra_valid(self):
        mgr = self._make_manager()
        cb = MagicMock()
        result = mgr.register_extra("preset_1", "<cmd>+3", cb)
        assert result is True
        assert "preset_1" in mgr._extra

    def test_register_extra_invalid_hotkey(self):
        mgr = self._make_manager()
        result = mgr.register_extra("bad", "<cmd>+unknown", MagicMock())
        assert result is False
        assert "bad" not in mgr._extra

    def test_register_extra_empty_hotkey(self):
        mgr = self._make_manager()
        result = mgr.register_extra("empty", "", MagicMock())
        assert result is False

    def test_unregister_extra(self):
        mgr = self._make_manager()
        mgr.register_extra("x", "<cmd>+5", MagicMock())
        assert "x" in mgr._extra

        mgr.unregister_extra("x")
        assert "x" not in mgr._extra

    def test_unregister_nonexistent_is_safe(self):
        mgr = self._make_manager()
        # Should not raise
        mgr.unregister_extra("ghost")

    def test_clear_extras(self):
        mgr = self._make_manager()
        for i in range(5):
            mgr.register_extra(f"p{i}", f"<cmd>+{i}", MagicMock())
        assert len(mgr._extra) == 5

        mgr.clear_extras()
        assert len(mgr._extra) == 0

    def test_register_overwrites_same_label(self):
        mgr = self._make_manager()
        cb1 = MagicMock()
        cb2 = MagicMock()

        mgr.register_extra("dup", "<cmd>+3", cb1)
        mgr.register_extra("dup", "<cmd>+4", cb2)

        assert len(mgr._extra) == 1
        # The callback should be the latest one
        _, _, stored_cb = mgr._extra["dup"]
        assert stored_cb is cb2

    def test_extra_stores_correct_keycode_and_modifiers(self):
        mgr = self._make_manager()
        cb = MagicMock()
        mgr.register_extra("test", "<cmd>+3", cb)

        keycode, modifiers, callback = mgr._extra["test"]
        assert keycode == 20  # '3' keycode
        assert callback is cb

    def test_thread_safety_concurrent_registration(self):
        """Multiple threads registering extras concurrently should not corrupt state."""
        mgr = self._make_manager()
        errors = []

        def register_batch(start):
            try:
                for i in range(20):
                    mgr.register_extra(f"t{start}_{i}", f"<cmd>+{i % 10}", MagicMock())
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_batch, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent registration errors: {errors}"
        # All 100 registrations should be present (5 threads × 20 each)
        assert len(mgr._extra) == 100


# ── HotkeyManager init ───────────────────────────────────────────────

class TestHotkeyManagerInit:
    def test_default_hotkeys_parsed(self):
        mgr = HotkeyManager(
            on_screenshot=MagicMock(),
            on_clipboard=MagicMock(),
        )
        # Default hotkeys should be parsed
        assert mgr._ss_keycode != -1
        assert mgr._cb_keycode != -1

    def test_custom_hotkeys(self):
        mgr = HotkeyManager(
            on_screenshot=MagicMock(),
            on_clipboard=MagicMock(),
            hotkey_screenshot="<ctrl>+<shift>+s",
            hotkey_clipboard="<ctrl>+<shift>+v",
        )
        assert mgr._ss_keycode == 1  # 's' keycode
        assert mgr._cb_keycode == 9  # 'v' keycode

    def test_invalid_hotkey_gives_minus_one(self):
        mgr = HotkeyManager(
            on_screenshot=MagicMock(),
            on_clipboard=MagicMock(),
            hotkey_screenshot="invalid_hotkey",
            hotkey_clipboard="<cmd>+2",
        )
        assert mgr._ss_keycode == -1
        assert mgr._cb_keycode != -1

    def test_is_running_false_before_start(self):
        mgr = HotkeyManager(
            on_screenshot=MagicMock(),
            on_clipboard=MagicMock(),
        )
        assert mgr.is_running is False

    def test_safe_call_suppresses_exceptions(self):
        """_safe_call should not propagate exceptions."""
        def bad_fn():
            raise ValueError("boom")

        # Should not raise
        HotkeyManager._safe_call(bad_fn)
