"""DailyStream menu bar tray application."""

import threading
from pathlib import Path
from typing import Optional

import rumps

from .config import Config, set_active_workspace_path
from .workspace import WorkspaceManager, choose_folder_dialog
from .pipeline import PipelineManager
from .capture import take_screenshot, grab_clipboard, save_clipboard_image
from .hotkeys import HotkeyManager


# --- Focus helper (ObjC class registered once at module level) ---

_FocusTarget = None  # lazily created on first use


def _get_focus_target_class():
    """Return the _FocusTarget ObjC class, creating it once."""
    global _FocusTarget
    if _FocusTarget is not None:
        return _FocusTarget
    import AppKit

    class _FocusTargetImpl(AppKit.NSObject):
        """NSTimer target that calls makeFirstResponder_ on the stored window/field."""

        _alert_window = None
        _textfield = None

        def setFocus_(self, timer):
            try:
                w = self._alert_window
                tf = self._textfield
                if w is not None and tf is not None:
                    w.makeFirstResponder_(tf)
            except Exception:
                pass

    _FocusTarget = _FocusTargetImpl
    return _FocusTarget


def _run_window(win: rumps.Window):
    """Run a rumps.Window with proper app activation and input focus.

    Call this instead of ``win.run()`` to ensure the text input cursor
    is visible in the dialog.

    The root cause of the invisible cursor:
    - NSApplicationActivationPolicyAccessory means the app never auto-becomes
      the key application when a dialog appears.
    - NSAlert.runModal() doesn't make its accessory-view NSTextField the
      firstResponder automatically.

    Fix: we activate the app, then schedule a timer in NSModalPanelRunLoopMode
    (the run-loop mode used by runModal) to set firstResponder on the text
    field after the alert window is on screen.
    """
    try:
        import AppKit

        ns_app = AppKit.NSApplication.sharedApplication()
        ns_app.activateIgnoringOtherApps_(True)

        cls = _get_focus_target_class()
        target = cls.alloc().init()
        target._alert_window = win._alert.window()
        target._textfield = win._textfield
        win._focus_target = target  # prevent GC

        timer = AppKit.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            0.05,  # 50ms — enough for the alert to appear
            target,
            b"setFocus:",
            None,
            False,
        )
        AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(
            timer, AppKit.NSModalPanelRunLoopMode
        )
    except Exception:
        # Fallback: at least try activating
        try:
            import AppKit
            AppKit.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        except Exception:
            pass

    return win.run()


class DailyStreamApp(rumps.App):
    """macOS menu bar tray application for DailyStream."""

    def __init__(self) -> None:
        super().__init__("DS", quit_button=None)
        self.config = Config.load()
        self.wm = WorkspaceManager()
        self.pm: Optional[PipelineManager] = None
        self.hotkey_mgr: Optional[HotkeyManager] = None
        self._capturing = False  # prevent double-trigger from hotkey + menu click

        if self.wm.workspace_dir:
            self.pm = PipelineManager(self.wm.workspace_dir)

        self._build_menu()
        self._start_hotkeys()
        self._update_title()

    # --- Menu construction ---

    def _build_menu(self) -> None:
        self.menu.clear()
        self.menu = [
            rumps.MenuItem("Start Workspace", callback=self._on_start_workspace),
            rumps.MenuItem("Open Workspace", callback=self._on_open_workspace),
            rumps.MenuItem("End Workspace", callback=self._on_end_workspace),
            rumps.MenuItem("📂 Open Folder", callback=self._on_open_folder),
            rumps.MenuItem("📝 Open Markdown", callback=self._on_open_markdown),
            None,  # separator
            rumps.MenuItem("Create Pipeline", callback=self._on_create_pipeline),
            None,  # separator
            rumps.MenuItem("📸 Screenshot", callback=self._on_screenshot_menu),
            rumps.MenuItem("📋 Clipboard", callback=self._on_clipboard_menu),
            None,
            rumps.MenuItem("Quit", callback=self._on_quit),
        ]
        self._rebuild_pipeline_menu()

    def _rebuild_pipeline_menu(self) -> None:
        """Rebuild pipeline switch submenu."""
        # Remove old pipeline items (between separators)
        to_remove = []
        for key, item in self.menu.items():
            if isinstance(item, rumps.MenuItem) and key.startswith("  → "):
                to_remove.append(key)
        for key in to_remove:
            del self.menu[key]

        if self.pm:
            pipelines = self.pm.list_pipelines()
            active = self.wm.get_active_pipeline()
            # Insert pipeline items after "Create Pipeline"
            for name in pipelines:
                prefix = "✓ " if name == active else "  "
                item = rumps.MenuItem(
                    f"  → {prefix}{name}",
                    callback=lambda sender, n=name: self._on_switch_pipeline(n),
                )
                self.menu.insert_after("Create Pipeline", item)

    def _update_title(self) -> None:
        if self.wm.is_active:
            pipeline = self.wm.get_active_pipeline() or "none"
            self.title = f"DS: {pipeline}"
        else:
            self.title = "DS: idle"

    # --- Workspace actions ---

    def _on_start_workspace(self, _) -> None:
        if self.wm.is_active:
            rumps.alert("Workspace Active", "Please end current workspace first.")
            return

        # Ask for title
        win = rumps.Window(
            message="Enter workspace title (or leave empty):",
            title="New Workspace",
            default_text="",
            ok="Create",
            cancel="Cancel",
        )
        resp = _run_window(win)
        if resp.clicked != 1:
            return

        title = resp.text.strip() or None

        # Choose folder
        folder = choose_folder_dialog()
        if not folder:
            return

        self.wm.create(base_path=folder, title=title)
        self.pm = PipelineManager(self.wm.workspace_dir)
        self._rebuild_pipeline_menu()
        self._update_title()
        rumps.notification("DailyStream", "Workspace created", str(self.wm.workspace_dir))

    def _on_open_workspace(self, _) -> None:
        """Open (resume) an existing workspace directory."""
        if self.wm.is_active:
            rumps.alert("Workspace Active", "Please end current workspace first.")
            return

        folder = choose_folder_dialog()
        if not folder:
            return

        # Try loading workspace_meta.json directly from selected folder,
        # or find the latest workspace subdirectory inside it
        target = Path(folder)

        # If selected folder itself has workspace_meta.json, load it
        if (target / "workspace_meta.json").exists():
            ws_dir = target
        else:
            # Look for workspace subdirectories (named like 2026-04-04_141627)
            candidates = sorted(
                [d for d in target.iterdir() if d.is_dir() and (d / "workspace_meta.json").exists()],
                reverse=True,  # most recent first
            )
            if not candidates:
                rumps.alert("Not Found", f"No workspace found in:\n{folder}")
                return
            ws_dir = candidates[0]

        if self.wm.load(ws_dir):
            # Re-activate it (mark as active again)
            self.wm.meta.ended_at = None
            self.wm.save_meta()
            set_active_workspace_path(ws_dir)
            self.pm = PipelineManager(ws_dir)
            self._rebuild_pipeline_menu()
            self._update_title()
            title = self.wm.meta.title or ws_dir.name
            rumps.notification("DailyStream", "Workspace opened", title)
        else:
            rumps.alert("Error", f"Failed to load workspace from:\n{ws_dir}")

    def _on_end_workspace(self, _) -> None:
        if not self.wm.is_active:
            rumps.alert("No Workspace", "No active workspace to end.")
            return
        report = self.wm.end(config=self.config)
        self.pm = None
        self._rebuild_pipeline_menu()
        self._update_title()
        msg = f"Report: {report}" if report else "Workspace ended."
        rumps.notification("DailyStream", "Workspace ended", msg)

    def _on_open_folder(self, _) -> None:
        """Open the current workspace directory in Finder."""
        if not self.wm.is_active or not self.wm.workspace_dir:
            rumps.alert("No Workspace", "No active workspace. Start or open one first.")
            return
        import subprocess
        subprocess.Popen(["open", str(self.wm.workspace_dir)])

    def _on_open_markdown(self, _) -> None:
        """Open stream.md in VS Code."""
        if not self.wm.is_active or not self.wm.workspace_dir:
            rumps.alert("No Workspace", "No active workspace. Start or open one first.")
            return
        md_path = self.wm.workspace_dir / "stream.md"
        if not md_path.exists():
            rumps.alert("No Markdown", "stream.md has not been created yet.\nCapture something first.")
            return
        import subprocess
        # Use 'open -a' so we don't depend on 'code' being in PATH
        # (menu-bar apps don't inherit terminal PATH)
        subprocess.Popen(["open", "-a", "Visual Studio Code", str(md_path)])

    # --- Pipeline actions ---

    def _on_create_pipeline(self, _) -> None:
        if not self.wm.is_active:
            rumps.alert("No Workspace", "Start a workspace first.")
            return

        win = rumps.Window(
            message="Enter pipeline name:",
            title="New Pipeline",
            default_text="",
            ok="Create",
            cancel="Cancel",
        )
        resp = _run_window(win)
        if resp.clicked != 1 or not resp.text.strip():
            return

        name = resp.text.strip()
        self.pm.create(name)
        self.wm.add_pipeline(name)

        # Always activate the newly created pipeline
        self.wm.activate_pipeline(name)

        self._rebuild_pipeline_menu()
        self._update_title()

    def _on_switch_pipeline(self, name: str) -> None:
        if self.wm.activate_pipeline(name):
            self._rebuild_pipeline_menu()
            self._update_title()

    # --- Capture actions ---

    def _on_screenshot_menu(self, _) -> None:
        self._do_screenshot()

    def _on_clipboard_menu(self, _) -> None:
        self._do_clipboard()

    def _do_screenshot(self) -> None:
        if self._capturing:
            return  # already in progress, ignore duplicate trigger
        if not self.wm.is_active or not self.wm.get_active_pipeline():
            rumps.notification("DailyStream", "Error", "No active pipeline. Create and activate one first.")
            return

        self._capturing = True
        pipeline = self.wm.get_active_pipeline()
        save_dir = self.pm.get_screenshots_dir(pipeline)

        def _capture():
            try:
                path = take_screenshot(save_dir, mode=self.config.screenshot_mode)
                if path is None:
                    return  # user cancelled screencapture

                def _show_dialog():
                    try:
                        win = rumps.Window(
                            message=f"Screenshot: {path.name}\nAdd a description:",
                            title=f"[Screenshot] → {pipeline}",
                            default_text="",
                            ok="Save",
                            cancel="Cancel",
                        )
                        resp = _run_window(win)
                        if resp.clicked != 1:
                            # User cancelled — remove the screenshot file
                            try:
                                path.unlink(missing_ok=True)
                            except Exception:
                                pass
                            return
                        desc = resp.text.strip()
                        entry = self.pm.add_entry(pipeline, "image", str(path), desc)
                        self._sync_entry(pipeline, entry)
                        rumps.notification("DailyStream", f"Saved to {pipeline}", desc or path.name)
                    except Exception:
                        import traceback
                        traceback.print_exc()

                try:
                    import AppKit
                    AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_show_dialog)
                except Exception:
                    _show_dialog()
            finally:
                self._capturing = False

        threading.Thread(target=_capture, daemon=True).start()

    def _do_clipboard(self) -> None:
        if not self.wm.is_active or not self.wm.get_active_pipeline():
            rumps.notification("DailyStream", "Error", "No active pipeline. Create and activate one first.")
            return

        pipeline = self.wm.get_active_pipeline()
        content, content_type = grab_clipboard()

        if content is None:
            rumps.notification("DailyStream", "Clipboard Empty", "Nothing to capture.")
            return

        # If clipboard has image, save it
        actual_content = content
        if content == CLIPBOARD_IMAGE_MARKER:
            save_dir = self.pm.get_screenshots_dir(pipeline)
            img_path = save_clipboard_image(save_dir)
            if img_path:
                actual_content = str(img_path)
                content_type = "image"
            else:
                rumps.notification("DailyStream", "Error", "Failed to save clipboard image.")
                return

        def _show_dialog():
            try:
                preview = actual_content[:80] + "..." if len(actual_content) > 80 else actual_content
                win = rumps.Window(
                    message=f"Clipboard ({content_type}): {preview}",
                    title=f"[Clipboard] → {pipeline}",
                    default_text="",
                    ok="Save",
                    cancel="Cancel",
                )
                resp = _run_window(win)
                if resp.clicked != 1:
                    return

                desc = resp.text.strip()
                entry = self.pm.add_entry(pipeline, content_type, actual_content, desc)
                self._sync_entry(pipeline, entry)
                rumps.notification("DailyStream", f"Saved to {pipeline}", desc or preview)
            except Exception:
                import traceback
                traceback.print_exc()

        # Hotkey callbacks run on a background thread,
        # but rumps UI (Window.run) must execute on the main thread.
        try:
            import AppKit
            AppKit.NSOperationQueue.mainQueue().addOperationWithBlock_(_show_dialog)
        except Exception:
            _show_dialog()

    # --- Note sync ---

    def _sync_entry(self, pipeline_name: str, entry) -> None:
        """Fire-and-forget sync to local Markdown (and optionally Obsidian)."""
        try:
            from .note_sync import NoteSyncManager
            syncer = NoteSyncManager(self.config, workspace_dir=self.wm.workspace_dir)
            syncer.sync_entry(
                workspace_meta=self.wm.meta,
                pipeline_name=pipeline_name,
                entry=entry,
            )
            # Mark the last entry in this pipeline as synced so _sync_all_on_end
            # won't duplicate it.
            if self.pm:
                entries = self.pm.get_entries(pipeline_name)
                if entries:
                    self.pm.mark_entry_synced(pipeline_name, len(entries) - 1)
        except Exception:
            import traceback
            traceback.print_exc()  # fire-and-forget, but log errors

    # --- Hotkeys ---

    def _start_hotkeys(self) -> None:
        try:
            self.hotkey_mgr = HotkeyManager(
                on_screenshot=self._do_screenshot,
                on_clipboard=self._do_clipboard,
                hotkey_screenshot=self.config.hotkey_screenshot,
                hotkey_clipboard=self.config.hotkey_clipboard,
            )
            self.hotkey_mgr.start()
        except Exception:
            pass  # hotkeys may fail without accessibility permission

    def _on_quit(self, _) -> None:
        if self.hotkey_mgr:
            self.hotkey_mgr.stop()
        rumps.quit_application()


def _patch_rumps_delegate():
    """Patch rumps' internal NSApp delegate class.

    rumps.App.run() replaces any delegate we set via setDelegate_(),
    so we monkey-patch the class itself before run() is called.
    This prevents the app from quitting when the last dialog is closed.
    """
    try:
        import objc
        from rumps.rumps import NSApp as RumpsNSApp

        def _should_terminate(self, sender):
            return False

        _should_terminate = objc.selector(
            _should_terminate,
            selector=b"applicationShouldTerminateAfterLastWindowClosed:",
            signature=b"Z@:@",
        )
        RumpsNSApp.applicationShouldTerminateAfterLastWindowClosed_ = _should_terminate
    except Exception:
        pass


def run_app() -> None:
    """Entry point to run the menu bar app."""
    try:
        import AppKit
        ns_app = AppKit.NSApplication.sharedApplication()
        ns_app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    except Exception:
        pass

    _patch_rumps_delegate()
    DailyStreamApp().run()
