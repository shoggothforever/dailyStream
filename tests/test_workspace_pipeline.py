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
