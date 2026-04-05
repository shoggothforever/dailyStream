"""DailyStream menu bar tray application."""

import threading
from pathlib import Path
from typing import Optional

import rumps

from .config import Config, set_active_workspace_path, CLIPBOARD_IMAGE_MARKER
from .workspace import WorkspaceManager, choose_folder_dialog
from .pipeline import PipelineManager
from .capture import take_screenshot, grab_clipboard, save_clipboard_image, capture_screen_region
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
            self.pm = PipelineManager(
                self.wm.workspace_dir,
                screenshot_save_path=self.config.screenshot_save_path,
            )

        self._build_menu()
        self._start_hotkeys()
        self._register_preset_hotkeys()
        self._update_title()

    # --- Menu construction ---

    def _build_menu(self) -> None:
        self.menu.clear()

        # Build the screenshot submenu
        screenshot_menu = rumps.MenuItem("📸 Screenshot")
        self._populate_screenshot_submenu(screenshot_menu)

        self.menu = [
            rumps.MenuItem("Start Workspace", callback=self._on_start_workspace),
            rumps.MenuItem("Open Workspace", callback=self._on_open_workspace),
            rumps.MenuItem("End Workspace", callback=self._on_end_workspace),
            rumps.MenuItem("📂 Open Folder", callback=self._on_open_folder),
            rumps.MenuItem("📝 Open Markdown", callback=self._on_open_markdown),
            None,  # separator
            rumps.MenuItem("Create Pipeline", callback=self._on_create_pipeline),
            None,  # separator
            screenshot_menu,
            rumps.MenuItem("📋 Clipboard", callback=self._on_clipboard_menu),
            None,
            rumps.MenuItem("Quit", callback=self._on_quit),
        ]
        self._rebuild_pipeline_menu()

    def _populate_screenshot_submenu(self, parent: rumps.MenuItem) -> None:
        """Build screenshot submenu items: presets + free selection + management."""
        # Clear existing items
        for key in list(parent.keys()):
            del parent[key]

        presets = self.config.screenshot_presets or []

        # Add each preset as a menu item
        for i, p in enumerate(presets):
            name = p.get("name", f"Preset {i + 1}")
            region = p.get("region", "")
            hotkey = p.get("hotkey", "")
            label = f"📐 {name}"
            if hotkey:
                label += f"  [{hotkey}]"
            item = rumps.MenuItem(
                label,
                callback=lambda sender, r=region: self._do_screenshot(region=r),
            )
            parent.add(item)

        # Separator if there are presets
        if presets:
            parent.add(None)

        # Free selection / default mode
        parent.add(rumps.MenuItem(
            "✂️ Free Selection",
            callback=lambda sender: self._do_screenshot(region=None),
        ))

        parent.add(None)  # separator

        # Preset management
        parent.add(rumps.MenuItem(
            "➕ Create Preset...",
            callback=self._on_create_preset,
        ))
        if presets:
            # Build delete submenu
            delete_menu = rumps.MenuItem("🗑 Delete Preset")
            for i, p in enumerate(presets):
                name = p.get("name", f"Preset {i + 1}")
                delete_menu.add(rumps.MenuItem(
                    name,
                    callback=lambda sender, idx=i: self._on_delete_preset(idx),
                ))
            parent.add(delete_menu)

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
        self.pm = PipelineManager(
            self.wm.workspace_dir,
            screenshot_save_path=self.config.screenshot_save_path,
        )
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
            self.pm = PipelineManager(
                ws_dir,
                screenshot_save_path=self.config.screenshot_save_path,
            )
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

        # Step 1: Pipeline name
        win = rumps.Window(
            message="Enter pipeline name:",
            title="New Pipeline (1/3)",
            default_text="",
            ok="Next",
            cancel="Cancel",
        )
        resp = _run_window(win)
        if resp.clicked != 1 or not resp.text.strip():
            return
        name = resp.text.strip()

        # Step 2: Description (work content)
        win2 = rumps.Window(
            message=f"Pipeline: {name}\n\nDescribe the work content (optional):",
            title="New Pipeline (2/3) — Description",
            default_text="",
            ok="Next",
            cancel="Skip",
        )
        resp2 = _run_window(win2)
        description = resp2.text.strip() if resp2.clicked == 1 else ""

        # Step 3: Goal
        win3 = rumps.Window(
            message=f"Pipeline: {name}\n\nWhat is the goal of this pipeline? (optional):",
            title="New Pipeline (3/3) — Goal",
            default_text="",
            ok="Create",
            cancel="Skip",
        )
        resp3 = _run_window(win3)
        goal = resp3.text.strip() if resp3.clicked == 1 else ""

        self.pm.create(name, description=description, goal=goal)
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

    def _on_clipboard_menu(self, _) -> None:
        self._do_clipboard()

    # -- Preset management --

    def _on_create_preset(self, _) -> None:
        """Let the user drag-select a screen region and save it as a preset."""
        rumps.notification(
            "DailyStream",
            "Create Screenshot Preset",
            "Drag to select a region on screen. Press Esc to cancel.",
        )
        # Small delay so the notification is visible before the overlay appears
        import time
        time.sleep(0.5)

        region = capture_screen_region()
        if not region:
            return  # user cancelled

        # Ask for a name
        win = rumps.Window(
            message=f"Region captured: {region}\n\nGive this preset a name:",
            title="Name Your Preset",
            default_text="",
            ok="Next",
            cancel="Cancel",
        )
        resp = _run_window(win)
        if resp.clicked != 1 or not resp.text.strip():
            return

        name = resp.text.strip()

        # Ask for an optional hotkey
        win2 = rumps.Window(
            message=(
                f"Preset: {name}\n\n"
                "Assign a global hotkey (optional):\n\n"
                "Format: <modifier>+<key>\n"
                "Examples: <cmd>+3, <ctrl>+<shift>+a, <alt>+f1\n\n"
                "Leave empty to skip."
            ),
            title="Hotkey Binding",
            default_text="",
            ok="Save",
            cancel="Skip",
        )
        resp2 = _run_window(win2)
        hotkey = ""
        if resp2.clicked == 1 and resp2.text.strip():
            hotkey = resp2.text.strip()

        # Save to config
        if self.config.screenshot_presets is None:
            self.config.screenshot_presets = []
        preset_entry: dict[str, str] = {"name": name, "region": region}
        if hotkey:
            preset_entry["hotkey"] = hotkey
        self.config.screenshot_presets.append(preset_entry)
        self.config.save()

        # Rebuild screenshot submenu and refresh hotkeys
        self._refresh_screenshot_submenu()
        self._register_preset_hotkeys()

        msg = f"'{name}' → {region}"
        if hotkey:
            msg += f"  [{hotkey}]"
        rumps.notification("DailyStream", "Preset saved", msg)

    def _on_delete_preset(self, idx: int) -> None:
        """Delete a preset by index."""
        presets = self.config.screenshot_presets or []
        if 0 <= idx < len(presets):
            removed = presets.pop(idx)
            self.config.screenshot_presets = presets if presets else None
            self.config.save()
            self._refresh_screenshot_submenu()
            self._register_preset_hotkeys()
            rumps.notification("DailyStream", "Preset deleted", removed.get("name", ""))

    def _refresh_screenshot_submenu(self) -> None:
        """Rebuild the screenshot submenu after preset changes."""
        screenshot_item = self.menu.get("📸 Screenshot")
        if screenshot_item:
            self._populate_screenshot_submenu(screenshot_item)

    def _register_preset_hotkeys(self) -> None:
        """Register global hotkeys for all presets that have a 'hotkey' field.

        Clears previous preset hotkeys first, so this is safe to call
        repeatedly (e.g. after creating / deleting a preset).
        """
        if not self.hotkey_mgr:
            return

        self.hotkey_mgr.clear_extras()

        presets = self.config.screenshot_presets or []
        for i, p in enumerate(presets):
            hotkey = p.get("hotkey", "")
            region = p.get("region", "")
            if hotkey and region:
                label = f"preset_{i}_{p.get('name', '')}"
                self.hotkey_mgr.register_extra(
                    label,
                    hotkey,
                    lambda r=region: self._do_screenshot(region=r),
                )

    # -- Screenshot capture --

    def _do_screenshot(self, region: Optional[str] = None) -> None:
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
                path = take_screenshot(
                    save_dir,
                    mode=self.config.screenshot_mode,
                    region=region,
                )
                if path is None:
                    return  # user cancelled screencapture

                def _show_dialog():
                    try:
                        preset_hint = f"  (preset region: {region})" if region else ""
                        win = rumps.Window(
                            message=f"Screenshot: {path.name}{preset_hint}\nAdd a description:",
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
            # Get pipeline meta (description/goal) for first-time heading render
            pipeline_meta = None
            if self.pm:
                pipeline_meta = self.pm.get_pipeline_meta(pipeline_name)
            syncer.sync_entry(
                workspace_meta=self.wm.meta,
                pipeline_name=pipeline_name,
                entry=entry,
                pipeline_meta=pipeline_meta,
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
