"""Integration tests for workspace + pipeline modules.

Covers: WorkspaceManager create/load/end, PipelineManager create/add_entry/
        get_entries, pipeline switching, workspace metadata persistence.
"""

from pathlib import Path

import pytest

from dailystream.config import read_json
from dailystream.workspace import WorkspaceManager, WorkspaceMeta
from dailystream.pipeline import PipelineManager, PipelineEntry


# ── WorkspaceManager ──────────────────────────────────────────────────

class TestWorkspaceCreate:
    def test_create_basic(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace, title="My Session")

        assert ws_dir.exists()
        assert wm.is_active
        assert wm.meta.title == "My Session"
        assert wm.meta.ended_at is None
        # workspace_meta.json should exist
        assert (ws_dir / "workspace_meta.json").exists()

    def test_create_without_title(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace)

        assert ws_dir.exists()
        # Title falls back to workspace_id
        assert wm.meta.title == wm.meta.workspace_id

    def test_create_chinese_title(self, tmp_workspace, tmp_config_dir):
        """Chinese characters should be preserved in the directory name."""
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace, title="清明节")

        assert ws_dir.exists()
        assert "清明节" in ws_dir.name
        assert wm.meta.title == "清明节"

    def test_create_duplicate_title_gets_suffix(self, tmp_workspace, tmp_config_dir):
        """If the same title already exists today, a suffix is added."""
        wm1 = WorkspaceManager()
        dir1 = wm1.create(base_path=tmp_workspace, title="dup")

        wm2 = WorkspaceManager()
        dir2 = wm2.create(base_path=tmp_workspace, title="dup")

        assert dir1 != dir2
        assert dir2.exists()

    def test_create_sanitises_dangerous_chars(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace, title='a/b\\c:d*e?"f')
        assert ws_dir.exists()
        # All dangerous chars should be replaced
        assert "/" not in ws_dir.name
        assert "\\" not in ws_dir.name


class TestWorkspaceLoadEnd:
    def test_load_existing(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace, title="Reload")

        wm2 = WorkspaceManager()
        assert wm2.load(ws_dir)
        assert wm2.meta.title == "Reload"

    def test_load_nonexistent_returns_false(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        assert wm.load(tmp_workspace / "no_such") is False

    def test_end_sets_ended_at(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        wm.create(base_path=tmp_workspace, title="End me")
        assert wm.is_active

        wm.end()
        assert not wm.is_active
        assert wm.meta.ended_at is not None

    def test_end_persists_to_disk(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace, title="Persist")
        wm.end()

        data = read_json(ws_dir / "workspace_meta.json")
        assert data["ended_at"] is not None


# ── WorkspaceManager.move ─────────────────────────────────────────────

class TestWorkspaceMove:
    """Cover the same-volume rename + cross-volume copy paths plus the
    metadata/path normalisation that follows a successful move."""

    def _seed_image_entry(
        self, ws_dir, pipeline: str, *,
        absolute: bool = False, exists: bool = True,
    ) -> str:
        """Write a fake screenshot under the workspace and register it
        as an image entry on ``pipeline``.

        Returns the entry's recorded ``input_content`` so the test can
        assert how the path was rewritten after the move.
        """
        from dailystream.pipeline import PipelineManager
        pm = PipelineManager(ws_dir)
        if pipeline not in (ws_dir / "pipelines").iterdir() if (
            ws_dir / "pipelines"
        ).exists() else []:
            pm.create(pipeline)
        shot_dir = ws_dir / "screenshots"
        shot_dir.mkdir(exist_ok=True)
        shot = shot_dir / "shot.png"
        if exists:
            shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        # Bypass add_entry's auto-relativisation so we can deliberately
        # inject an absolute path that the move() routine should clean up.
        ctx_path = ws_dir / "pipelines" / pipeline / "context.json"
        ctx = read_json(ctx_path)
        recorded = str(shot) if absolute else "screenshots/shot.png"
        ctx.setdefault("entries", []).append({
            "timestamp": "2026-04-04T10:00:00+00:00",
            "input_type": "image",
            "input_content": recorded,
            "description": "",
            "synced": False,
        })
        from dailystream.config import write_json as _wj
        _wj(ctx_path, ctx)
        return recorded

    def test_same_volume_rename_succeeds(
        self, tmp_workspace, tmp_config_dir,
    ):
        from dailystream.pipeline import PipelineManager
        wm = WorkspaceManager()
        src_ws = wm.create(base_path=tmp_workspace, title="Movable")
        PipelineManager(src_ws).create("alpha")
        wm.end()

        new_parent = tmp_workspace / "archive"
        new_parent.mkdir()

        new_dir = wm.move(new_parent)

        assert new_dir.exists()
        assert not src_ws.exists()
        assert (new_dir / "workspace_meta.json").exists()
        assert wm.workspace_dir == new_dir
        # Layout preserved: archive/<yymmdd>/<title>
        assert new_dir.parent.parent == new_parent
        # Meta updated to the new absolute path.
        meta = read_json(new_dir / "workspace_meta.json")
        assert meta["workspace_path"] == str(new_dir)

    def test_move_relativises_absolute_input_content(
        self, tmp_workspace, tmp_config_dir,
    ):
        """Absolute paths inside the workspace should become relative."""
        wm = WorkspaceManager()
        src_ws = wm.create(base_path=tmp_workspace, title="Relativise")
        recorded = self._seed_image_entry(src_ws, "alpha", absolute=True)
        assert Path(recorded).is_absolute()
        wm.end()

        new_parent = tmp_workspace / "archive"
        new_parent.mkdir()
        new_dir = wm.move(new_parent)

        ctx = read_json(new_dir / "pipelines" / "alpha" / "context.json")
        rewritten = ctx["entries"][-1]["input_content"]
        assert rewritten == "screenshots/shot.png"
        # The actual file resolves under the new workspace root.
        assert (new_dir / rewritten).exists()

    def test_move_leaves_external_paths_alone(
        self, tmp_workspace, tmp_config_dir,
    ):
        """Absolute paths *outside* the workspace must NOT be rewritten."""
        wm = WorkspaceManager()
        src_ws = wm.create(base_path=tmp_workspace, title="ExternalKept")
        # External screenshot somewhere else on disk
        external = tmp_workspace / "external_shots"
        external.mkdir()
        ext_shot = external / "outside.png"
        ext_shot.write_bytes(b"\x89PNG\r\n\x1a\n")
        from dailystream.pipeline import PipelineManager
        PipelineManager(src_ws).create("alpha")
        ctx_path = src_ws / "pipelines" / "alpha" / "context.json"
        ctx = read_json(ctx_path)
        ctx.setdefault("entries", []).append({
            "timestamp": "2026-04-04T10:00:00+00:00",
            "input_type": "image",
            "input_content": str(ext_shot),
            "description": "",
            "synced": False,
        })
        from dailystream.config import write_json as _wj
        _wj(ctx_path, ctx)
        wm.end()

        new_parent = tmp_workspace / "archive"
        new_parent.mkdir()
        new_dir = wm.move(new_parent)

        ctx_after = read_json(new_dir / "pipelines" / "alpha" / "context.json")
        kept = ctx_after["entries"][-1]["input_content"]
        # External absolute paths preserved as-is.
        assert kept == str(ext_shot)

    def test_cross_volume_falls_back_to_copy(
        self, tmp_workspace, tmp_config_dir, monkeypatch,
    ):
        """Simulate EXDEV — rename should fall back to copy + verify
        + delete-original."""
        import errno
        wm = WorkspaceManager()
        src_ws = wm.create(base_path=tmp_workspace, title="CrossVol")
        from dailystream.pipeline import PipelineManager
        PipelineManager(src_ws).create("alpha")
        self._seed_image_entry(src_ws, "alpha", absolute=False)
        wm.end()

        # Patch Path.rename to always raise EXDEV.
        original_rename = Path.rename

        def fake_rename(self, target):
            raise OSError(errno.EXDEV, "Cross-device link")

        monkeypatch.setattr(Path, "rename", fake_rename)

        new_parent = tmp_workspace / "archive"
        new_parent.mkdir()
        new_dir = wm.move(new_parent)

        # Restore so other tests aren't affected (monkeypatch will
        # also undo this on teardown).
        monkeypatch.setattr(Path, "rename", original_rename)

        assert new_dir.exists()
        assert (new_dir / "screenshots" / "shot.png").exists()
        assert not src_ws.exists()  # original removed after verify

    def test_cross_volume_verify_failure_keeps_original(
        self, tmp_workspace, tmp_config_dir, monkeypatch,
    ):
        """If the post-copy verification finds missing images, the
        partial destination must be cleaned up and the original kept."""
        import errno

        wm = WorkspaceManager()
        src_ws = wm.create(base_path=tmp_workspace, title="VerifyFail")
        from dailystream.pipeline import PipelineManager
        PipelineManager(src_ws).create("alpha")
        # Register an image whose file *does not exist on disk* so the
        # verify step reports it as missing in the destination.
        ctx_path = src_ws / "pipelines" / "alpha" / "context.json"
        ctx = read_json(ctx_path)
        ctx.setdefault("entries", []).append({
            "timestamp": "2026-04-04T10:00:00+00:00",
            "input_type": "image",
            "input_content": "screenshots/ghost.png",
            "description": "",
            "synced": False,
        })
        from dailystream.config import write_json as _wj
        _wj(ctx_path, ctx)
        wm.end()

        def fake_rename(self, target):
            raise OSError(errno.EXDEV, "Cross-device link")
        monkeypatch.setattr(Path, "rename", fake_rename)

        new_parent = tmp_workspace / "archive"
        new_parent.mkdir()
        with pytest.raises(RuntimeError, match="verification failed"):
            wm.move(new_parent)

        # Original preserved, partial copy cleaned up.
        assert src_ws.exists()
        assert not list(new_parent.iterdir())  # nothing left behind

    def test_move_refuses_active(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        wm.create(base_path=tmp_workspace, title="ActiveBlocked")
        # Did NOT call end() — workspace is active.
        new_parent = tmp_workspace / "archive"
        new_parent.mkdir()
        with pytest.raises(RuntimeError, match="end\\(\\) before moving"):
            wm.move(new_parent)

    def test_move_refuses_existing_destination(
        self, tmp_workspace, tmp_config_dir,
    ):
        wm = WorkspaceManager()
        src = wm.create(base_path=tmp_workspace, title="Collide")
        wm.end()

        new_parent = tmp_workspace / "archive"
        # Pre-create the *exact* destination dir so move detects it.
        date_folder = src.parent.name
        title_folder = src.name
        (new_parent / date_folder / title_folder).mkdir(parents=True)

        with pytest.raises(FileExistsError):
            wm.move(new_parent)

    def test_move_refuses_missing_target_parent(
        self, tmp_workspace, tmp_config_dir,
    ):
        wm = WorkspaceManager()
        wm.create(base_path=tmp_workspace, title="NoParent")
        wm.end()
        with pytest.raises(NotADirectoryError):
            wm.move(tmp_workspace / "does-not-exist")

# ── Pipeline management inside workspace ──────────────────────────────

class TestPipelineInWorkspace:
    def test_add_and_activate(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace)
        pm = PipelineManager(ws_dir)

        pm.create("alpha")
        wm.add_pipeline("alpha")
        wm.activate_pipeline("alpha")

        assert wm.get_active_pipeline() == "alpha"
        assert "alpha" in wm.meta.pipelines

    def test_switch_pipeline(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace)
        pm = PipelineManager(ws_dir)

        for name in ("p1", "p2"):
            pm.create(name)
            wm.add_pipeline(name)

        wm.activate_pipeline("p1")
        assert wm.get_active_pipeline() == "p1"

        wm.activate_pipeline("p2")
        assert wm.get_active_pipeline() == "p2"

    def test_activate_nonexistent_returns_false(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        wm.create(base_path=tmp_workspace)
        assert wm.activate_pipeline("ghost") is False

    def test_duplicate_add_pipeline_ignored(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace)
        pm = PipelineManager(ws_dir)

        pm.create("dup")
        wm.add_pipeline("dup")
        wm.add_pipeline("dup")  # should not duplicate
        assert wm.meta.pipelines.count("dup") == 1


# ── PipelineManager entries ───────────────────────────────────────────

class TestPipelineEntries:
    def test_add_and_get_entries(self, workspace_with_pipeline):
        wm, pm, pipe = workspace_with_pipeline

        entry = pm.add_entry(pipe, "text", "Hello world", "first entry")
        assert isinstance(entry, PipelineEntry)
        assert entry.input_type == "text"

        entries = pm.get_entries(pipe)
        assert len(entries) == 1
        assert entries[0]["input_content"] == "Hello world"

    def test_multiple_entries(self, workspace_with_pipeline):
        wm, pm, pipe = workspace_with_pipeline

        pm.add_entry(pipe, "text", "One", "d1")
        pm.add_entry(pipe, "url", "https://example.com", "d2")
        pm.add_entry(pipe, "image", "/path/to/img.png", "d3")

        entries = pm.get_entries(pipe)
        assert len(entries) == 3
        assert [e["input_type"] for e in entries] == ["text", "url", "image"]

    def test_mark_entry_synced(self, workspace_with_pipeline):
        wm, pm, pipe = workspace_with_pipeline

        pm.add_entry(pipe, "text", "data", "desc")
        entries = pm.get_entries(pipe)
        assert entries[0].get("synced") is False

        pm.mark_entry_synced(pipe, 0)
        entries = pm.get_entries(pipe)
        assert entries[0]["synced"] is True

    def test_get_all_entries_across_pipelines(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace)
        pm = PipelineManager(ws_dir)

        for name in ("alpha", "beta"):
            pm.create(name)
            wm.add_pipeline(name)

        pm.add_entry("alpha", "text", "A1", "desc-a")
        pm.add_entry("beta", "text", "B1", "desc-b")

        all_entries = pm.get_all_entries()
        assert len(all_entries) == 2
        # Each entry should have a 'pipeline' key without mutating originals
        pipelines_in_result = {e["pipeline"] for e in all_entries}
        assert pipelines_in_result == {"alpha", "beta"}

    def test_get_all_entries_does_not_mutate_originals(self, workspace_with_pipeline):
        """get_all_entries must use dict unpacking, not mutate stored dicts."""
        wm, pm, pipe = workspace_with_pipeline
        pm.add_entry(pipe, "text", "data", "desc")

        _ = pm.get_all_entries()

        # Re-read from disk: should NOT have 'pipeline' key
        original_entries = pm.get_entries(pipe)
        assert "pipeline" not in original_entries[0]

    def test_list_pipelines(self, tmp_workspace, tmp_config_dir):
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace)
        pm = PipelineManager(ws_dir)

        pm.create("beta")
        pm.create("alpha")

        names = pm.list_pipelines()
        assert names == ["alpha", "beta"]  # sorted
