"""Tests covering the workspace-portability refactor.

The goals being verified here:

1. ``PipelineManager.add_entry`` writes *workspace-relative* paths into
   ``context.json`` for image entries that live inside the workspace.
2. Image entries pointing *outside* the workspace stay absolute (so the
   user-configurable ``screenshot_save_path`` keeps working when it is
   set to e.g. an iCloud-shared folder).
3. ``resolve_entry_path`` round-trips both absolute (legacy) and
   relative (new) values into a usable absolute Path.
4. A workspace can be moved on disk and entries still resolve, *as long
   as they were written with the new code* — this is the property the
   whole refactor exists for.
"""

from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────────


def _make_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")  # tiny valid PNG header
    return path


# ── unit ─────────────────────────────────────────────────────────────


class TestResolveEntryPath:
    def test_absolute_path_returned_as_is(self, tmp_path):
        from dailystream.pipeline import resolve_entry_path

        ws = tmp_path / "ws"
        abs_p = "/tmp/somewhere/foo.png"
        result = resolve_entry_path(ws, abs_p)
        assert result == Path(abs_p)
        assert result.is_absolute()

    def test_relative_path_joined_with_workspace(self, tmp_path):
        from dailystream.pipeline import resolve_entry_path

        ws = tmp_path / "ws"
        ws.mkdir()
        result = resolve_entry_path(ws, "screenshots/foo.png")
        assert result == ws / "screenshots" / "foo.png"
        assert result.is_absolute()

    def test_empty_input_safe(self, tmp_path):
        from dailystream.pipeline import resolve_entry_path

        # Empty content (e.g. text entries) → still returns *something*
        # callable; consumers always check ``.exists()`` so a non-existing
        # path is fine.
        result = resolve_entry_path(tmp_path, "")
        assert result == tmp_path / ""


# ── integration: writing ─────────────────────────────────────────────


class TestAddEntryRelativePaths:
    def test_image_inside_workspace_stored_relative(self, workspace_with_pipeline):
        wm, pm, pipe = workspace_with_pipeline
        ws_dir = wm.workspace_dir

        # The screenshot lives under <ws>/screenshots/, which is the
        # default behaviour of take_screenshot().
        img = _make_png(ws_dir / "screenshots" / "shot.png")

        entry = pm.add_entry(pipe, "image", str(img), "shot 1")

        # The dataclass returned to the caller carries the normalised
        # relative path — *not* whatever was passed in.
        assert entry.input_content == "screenshots/shot.png"

        # And the persisted JSON matches.
        stored = pm.get_entries(pipe)[0]
        assert stored["input_content"] == "screenshots/shot.png"

    def test_image_outside_workspace_stays_absolute(self, workspace_with_pipeline, tmp_path):
        wm, pm, pipe = workspace_with_pipeline

        # User pointed screenshot_save_path to an external folder — the
        # entry should preserve the absolute path so the link still works.
        external = tmp_path / "external" / "shared.png"
        _make_png(external)

        entry = pm.add_entry(pipe, "image", str(external), "external")

        assert entry.input_content == str(external)
        assert Path(entry.input_content).is_absolute()

    def test_text_entry_passthrough(self, workspace_with_pipeline):
        wm, pm, pipe = workspace_with_pipeline

        entry = pm.add_entry(pipe, "text", "just some thoughts", "diary")
        assert entry.input_content == "just some thoughts"

    def test_url_entry_passthrough(self, workspace_with_pipeline):
        wm, pm, pipe = workspace_with_pipeline

        url = "https://example.com/article"
        entry = pm.add_entry(pipe, "url", url, "ref")
        assert entry.input_content == url

    def test_already_relative_path_preserved(self, workspace_with_pipeline):
        """A caller that already passes a relative path must not get it
        mangled (e.g. double-prefixed or absolutised)."""
        wm, pm, pipe = workspace_with_pipeline

        entry = pm.add_entry(pipe, "image", "screenshots/x.png", "")
        assert entry.input_content == "screenshots/x.png"


# ── integration: backward-compat read path ────────────────────────────


class TestLegacyAbsolutePathRead:
    """Older context.json files contain absolute ``input_content`` values.
    Loaders must keep working without any migration step."""

    def test_resolve_handles_legacy_absolute(self, workspace_with_pipeline):
        from dailystream.pipeline import resolve_entry_path

        wm, pm, pipe = workspace_with_pipeline
        ws_dir = wm.workspace_dir

        # Simulate a legacy absolute entry by writing context.json by
        # hand (bypassing add_entry's normalisation).
        from dailystream.config import read_json, write_json

        ctx_path = ws_dir / "pipelines" / pipe / "context.json"
        ctx = read_json(ctx_path)
        legacy_abs = str(ws_dir / "screenshots" / "legacy.png")
        _make_png(Path(legacy_abs))
        ctx.setdefault("entries", []).append({
            "timestamp": "2026-04-01T10:00:00+08:00",
            "input_type": "image",
            "input_content": legacy_abs,
            "description": "legacy",
            "synced": False,
        })
        write_json(ctx_path, ctx)

        # The resolver must accept the absolute path verbatim.
        entry = pm.get_entries(pipe)[0]
        resolved = resolve_entry_path(ws_dir, entry["input_content"])
        assert resolved.exists()
        assert resolved == Path(legacy_abs)


# ── workspace portability ─────────────────────────────────────────────


class TestWorkspacePortability:
    """The headline property: a workspace written with the new code can
    be moved anywhere and entries still resolve."""

    def test_move_workspace_then_resolve(self, workspace_with_pipeline, tmp_path):
        from dailystream.pipeline import resolve_entry_path

        wm, pm, pipe = workspace_with_pipeline
        old_ws = wm.workspace_dir

        img = _make_png(old_ws / "screenshots" / "p.png")
        pm.add_entry(pipe, "image", str(img), "")

        # Simulate a user-initiated move (e.g. Finder drag).
        new_ws = tmp_path / "moved" / "workspace"
        new_ws.parent.mkdir(parents=True)
        old_ws.rename(new_ws)

        # Re-load from the new location.
        from dailystream.workspace import WorkspaceManager
        from dailystream.pipeline import PipelineManager

        wm2 = WorkspaceManager()
        assert wm2.load(new_ws)
        pm2 = PipelineManager(new_ws)

        entry = pm2.get_entries(pipe)[0]
        resolved = resolve_entry_path(new_ws, entry["input_content"])
        assert resolved.exists(), \
            "image must resolve under the new workspace location"
        assert resolved == new_ws / "screenshots" / "p.png"
