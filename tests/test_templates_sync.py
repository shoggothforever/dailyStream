"""Integration tests for templates + note_sync modules.

Covers: build_context, render_entry, LocalMarkdownSyncer,
        ObsidianSyncer, NoteSyncManager.
"""

from pathlib import Path

import pytest

from dailystream.config import Config, now_iso
from dailystream.templates import (
    EntryContext,
    build_context,
    render_entry,
    get_entry_templates,
    get_obsidian_templates,
    get_timeline_templates,
    _FALLBACK_TEMPLATE,
)
from dailystream.note_sync import LocalMarkdownSyncer, NoteSyncManager
from dailystream.pipeline import PipelineEntry


# ── build_context ─────────────────────────────────────────────────────

class TestBuildContext:
    def test_text_entry(self):
        ctx = build_context(
            timestamp="2026-04-04T14:30:25+08:00",
            input_type="text",
            description="一些笔记",
            content="Hello world, this is a text note.",
            pipeline="main",
        )
        assert ctx.time == "14:30:25"
        assert ctx.type == "text"
        assert ctx.description == "一些笔记"
        assert ctx.pipeline == "main"
        assert ctx.quote.startswith("> ")

    def test_url_entry(self):
        ctx = build_context(
            timestamp="2026-04-04T15:00:00+08:00",
            input_type="url",
            description="参考链接",
            content="https://example.com",
        )
        assert "[https://example.com]" in ctx.link
        assert ctx.image == ""

    def test_image_entry_with_workspace_dir(self, tmp_path):
        # Create a fake image file
        screenshots = tmp_path / "screenshots"
        screenshots.mkdir()
        img = screenshots / "shot.png"
        img.write_bytes(b"PNG")

        ctx = build_context(
            timestamp="2026-04-04T16:00:00+08:00",
            input_type="image",
            description="截图",
            content=str(img),
            image_path=str(img),
            workspace_dir=tmp_path,
        )
        # Without explicit image_base_dir, paths stay workspace-relative
        # (preserves backward-compat behaviour).
        assert "screenshots/shot.png" in ctx.image
        assert ctx.image.startswith("![screenshot]")

    def test_image_entry_with_pipeline_base_dir(self, tmp_path):
        """When rendering into ``pipelines/<n>/stream.md`` the image path
        must traverse up two directories."""
        screenshots = tmp_path / "screenshots"
        screenshots.mkdir()
        img = screenshots / "shot.png"
        img.write_bytes(b"PNG")

        pipeline_dir = tmp_path / "pipelines" / "main"
        pipeline_dir.mkdir(parents=True)

        ctx = build_context(
            timestamp="2026-04-04T16:00:00+08:00",
            input_type="image",
            description="x",
            content=str(img),
            image_path=str(img),
            workspace_dir=tmp_path,
            image_base_dir=pipeline_dir,
        )
        assert "../../screenshots/shot.png" in ctx.image

    def test_text_same_as_desc_no_quote(self):
        """When content equals description, no block-quote is generated."""
        ctx = build_context(
            timestamp="2026-04-04T17:00:00+08:00",
            input_type="text",
            description="Same text",
            content="Same text",
        )
        assert ctx.quote == ""


# ── render_entry ──────────────────────────────────────────────────────

class TestRenderEntry:
    def test_default_text_template(self):
        templates = get_entry_templates()
        ctx = build_context(
            timestamp="2026-04-04T10:00:00+08:00",
            input_type="text",
            description="描述",
            content="内容文本",
        )
        result = render_entry(templates, ctx)
        assert "10:00:00" in result
        assert "描述" in result
        assert "---" in result

    def test_custom_template_override(self):
        custom = {"text": "📌 [{time}] {description}"}
        templates = get_entry_templates(custom)
        ctx = build_context(
            timestamp="2026-04-04T11:00:00+08:00",
            input_type="text",
            description="Test",
            content="Body",
        )
        result = render_entry(templates, ctx)
        assert result == "📌 [11:00:00] Test"

    def test_unknown_type_uses_fallback(self):
        templates = get_entry_templates()
        ctx = EntryContext(time="12:00:00", type="unknown", description="x", content="y")
        result = render_entry(templates, ctx)
        assert "12:00:00" in result
        assert "---" in result

    def test_empty_description_cleaned_up(self):
        templates = get_entry_templates()
        ctx = build_context(
            timestamp="2026-04-04T13:00:00+08:00",
            input_type="text",
            description="",
            content="Only content",
        )
        result = render_entry(templates, ctx)
        # No triple blank lines
        assert "\n\n\n" not in result


# ── Template set merging ──────────────────────────────────────────────

class TestTemplateAccessors:
    def test_entry_templates_default_keys(self):
        t = get_entry_templates()
        assert set(t.keys()) == {"image", "url", "text"}

    def test_obsidian_templates_default_keys(self):
        t = get_obsidian_templates()
        assert set(t.keys()) == {"image", "url", "text"}

    def test_timeline_templates_include_pipeline(self):
        t = get_timeline_templates()
        for tpl in t.values():
            assert "{pipeline}" in tpl

    def test_merge_preserves_defaults(self):
        custom = {"image": "CUSTOM {time}"}
        t = get_entry_templates(custom)
        assert t["image"] == "CUSTOM {time}"
        # url and text should still be default
        assert "{link}" in t["url"]


# ── LocalMarkdownSyncer ──────────────────────────────────────────────

class TestLocalMarkdownSyncer:
    def _make_syncer(self, workspace_dir):
        return LocalMarkdownSyncer(workspace_dir)

    def test_first_entry_creates_pipeline_file(self, tmp_path):
        syncer = self._make_syncer(tmp_path)
        syncer.sync_entry(
            workspace_title="Test WS",
            pipeline_name="main",
            timestamp="2026-04-04T10:00:00+08:00",
            input_type="text",
            description="第一条",
            content="内容",
        )
        md = tmp_path / "pipelines" / "main" / "stream.md"
        assert md.exists(), "pipeline-level stream.md must be created"
        text = md.read_text(encoding="utf-8")
        # Pipeline file's own heading is the pipeline name (not WS title).
        assert "# main" in text
        assert "第一条" in text
        # Top-level stream.md should NOT be auto-written by the per-pipeline
        # syncer — that's now the WorkspaceIndexSyncer's job.
        assert not (tmp_path / "stream.md").exists()

    def test_entries_grouped_by_pipeline_across_files(self, tmp_path):
        syncer = self._make_syncer(tmp_path)

        syncer.sync_entry(
            workspace_title="WS",
            pipeline_name="alpha",
            timestamp="2026-04-04T10:00:00+08:00",
            input_type="text",
            description="A1",
            content="content-a1",
        )
        syncer.sync_entry(
            workspace_title="WS",
            pipeline_name="beta",
            timestamp="2026-04-04T10:05:00+08:00",
            input_type="text",
            description="B1",
            content="content-b1",
        )
        syncer.sync_entry(
            workspace_title="WS",
            pipeline_name="alpha",
            timestamp="2026-04-04T10:10:00+08:00",
            input_type="text",
            description="A2",
            content="content-a2",
        )

        alpha_md = (tmp_path / "pipelines" / "alpha" / "stream.md").read_text(encoding="utf-8")
        beta_md = (tmp_path / "pipelines" / "beta" / "stream.md").read_text(encoding="utf-8")

        assert "A1" in alpha_md and "A2" in alpha_md
        assert "B1" in beta_md
        # Each file must only contain its own entries.
        assert "B1" not in alpha_md
        assert "A2" not in beta_md

    def test_image_entry_uses_relative_path_up_two_levels(self, tmp_path):
        """Per-pipeline file lives two levels deep, so image paths must
        be ``../../screenshots/foo.png``."""
        syncer = self._make_syncer(tmp_path)
        # Create the file so the resolved path doesn't change the rel calc
        shots = tmp_path / "screenshots"
        shots.mkdir()
        (shots / "shot.png").write_bytes(b"PNG")

        syncer.sync_entry(
            workspace_title="WS",
            pipeline_name="main",
            timestamp="2026-04-04T12:00:00+08:00",
            input_type="image",
            description="截图",
            content=str(tmp_path / "screenshots" / "shot.png"),
            image_path=str(tmp_path / "screenshots" / "shot.png"),
        )
        md = (tmp_path / "pipelines" / "main" / "stream.md").read_text(encoding="utf-8")
        assert "![screenshot]" in md
        assert "../../screenshots/shot.png" in md

    def test_url_entry_has_link(self, tmp_path):
        syncer = self._make_syncer(tmp_path)
        syncer.sync_entry(
            workspace_title="WS",
            pipeline_name="main",
            timestamp="2026-04-04T13:00:00+08:00",
            input_type="url",
            description="链接",
            content="https://example.com/page",
        )
        md = (tmp_path / "pipelines" / "main" / "stream.md").read_text(encoding="utf-8")
        assert "https://example.com/page" in md

    def test_pipeline_meta_renders_in_header(self, tmp_path):
        syncer = self._make_syncer(tmp_path)
        syncer.sync_entry(
            workspace_title="WS",
            pipeline_name="main",
            timestamp="2026-04-04T10:00:00+08:00",
            input_type="text",
            description="x",
            content="y",
            pipeline_meta={"description": "做笔记", "goal": "理解概念"},
        )
        md = (tmp_path / "pipelines" / "main" / "stream.md").read_text(encoding="utf-8")
        assert "📝 Description" in md and "做笔记" in md
        assert "🎯 Goal" in md and "理解概念" in md


# ── NoteSyncManager ──────────────────────────────────────────────────

class TestNoteSyncManager:
    def test_sync_with_dict_entry(self, workspace_with_pipeline):
        wm, pm, pipe = workspace_with_pipeline
        cfg = Config()

        entry = pm.add_entry(pipe, "text", "Dict entry test", "desc")
        # add_entry returns PipelineEntry, but NoteSyncManager also accepts dict
        entry_dict = {
            "timestamp": now_iso(),
            "input_type": "text",
            "input_content": "Dict entry test",
            "description": "from dict",
        }

        syncer = NoteSyncManager(cfg, workspace_dir=wm.workspace_dir)
        syncer.sync_entry(wm.meta, pipe, entry_dict)

        md = wm.workspace_dir / "pipelines" / pipe / "stream.md"
        assert md.exists()
        text = md.read_text(encoding="utf-8")
        assert "from dict" in text

    def test_sync_with_dataclass_entry(self, workspace_with_pipeline):
        wm, pm, pipe = workspace_with_pipeline
        cfg = Config()

        entry = pm.add_entry(pipe, "url", "https://example.com", "link desc")

        syncer = NoteSyncManager(cfg, workspace_dir=wm.workspace_dir)
        syncer.sync_entry(wm.meta, pipe, entry)

        text = (wm.workspace_dir / "pipelines" / pipe / "stream.md").read_text(encoding="utf-8")
        assert "https://example.com" in text

    def test_sync_disabled_when_backend_is_none(self, workspace_with_pipeline):
        wm, pm, pipe = workspace_with_pipeline
        cfg = Config(note_sync_backend="none")

        entry = pm.add_entry(pipe, "text", "No sync", "desc")
        syncer = NoteSyncManager(cfg, workspace_dir=wm.workspace_dir)
        syncer.sync_entry(wm.meta, pipe, entry)

        md = wm.workspace_dir / "pipelines" / pipe / "stream.md"
        # Local Markdown syncer is always created when workspace_dir is
        # provided; this test ensures it doesn't crash with backend="none".
        assert md.exists()


# ── WorkspaceIndexSyncer ──────────────────────────────────────────────

from dailystream.note_sync import (
    WorkspaceIndexSyncer,
    migrate_monolithic_stream_md,
)


class TestWorkspaceIndexSyncer:
    def test_rebuild_empty_pipelines(self, tmp_path):
        WorkspaceIndexSyncer(tmp_path).rebuild("My WS", [])
        md = (tmp_path / "stream.md").read_text(encoding="utf-8")
        assert "# My WS" in md
        assert "_No pipelines yet._" in md

    def test_rebuild_with_pipelines(self, tmp_path):
        pipelines = [
            {"name": "alpha", "entry_count": 3, "description": "做笔记", "goal": ""},
            {"name": "beta", "entry_count": 1, "description": "", "goal": "试试看"},
            {"name": "gamma with space", "entry_count": 0, "description": "", "goal": ""},
        ]
        WorkspaceIndexSyncer(tmp_path).rebuild("WS Title", pipelines)
        md = (tmp_path / "stream.md").read_text(encoding="utf-8")

        # Each pipeline gets a link to its per-pipeline stream.md
        assert "pipelines/alpha/stream.md" in md
        assert "pipelines/beta/stream.md" in md
        # Spaces url-encoded
        assert "pipelines/gamma%20with%20space/stream.md" in md
        # entry count suffix (singular/plural)
        assert "3 entries" in md
        assert "1 entry" in md
        assert "0 entries" in md
        # description / goal indented under bullet
        assert "📝 做笔记" in md
        assert "🎯 试试看" in md


class TestMigrateMonolithicStream:
    def test_migrate_splits_sections(self, tmp_path):
        # Simulate a legacy monolithic stream.md
        legacy = tmp_path / "stream.md"
        legacy.write_text(
            "# Workspace Title\n\n"
            "## alpha\n\n**10:00** · text\n\nA1 content\n\n---\n\n"
            "## beta\n\n**10:05** · image\n\nB shot\n\n"
            "![screenshot](screenshots/b1.png)\n\n---\n",
            encoding="utf-8",
        )

        did = migrate_monolithic_stream_md(tmp_path)
        assert did is True

        # Backup preserved
        assert not legacy.exists()
        assert (tmp_path / "stream.md.legacy.bak").exists()

        # Sections extracted
        alpha_md = (tmp_path / "pipelines" / "alpha" / "stream.md").read_text(encoding="utf-8")
        beta_md = (tmp_path / "pipelines" / "beta" / "stream.md").read_text(encoding="utf-8")
        assert "# alpha" in alpha_md
        assert "A1 content" in alpha_md
        assert "# beta" in beta_md
        assert "B shot" in beta_md

        # Image path rewritten from ``screenshots/`` to ``../../screenshots/``
        assert "../../screenshots/b1.png" in beta_md
        assert "(screenshots/b1.png)" not in beta_md

    def test_migrate_skipped_when_already_split(self, tmp_path):
        # Prepare a workspace that already has per-pipeline files.
        legacy = tmp_path / "stream.md"
        legacy.write_text("# WS\n\n## foo\n\nx\n", encoding="utf-8")
        (tmp_path / "pipelines" / "foo").mkdir(parents=True)
        (tmp_path / "pipelines" / "foo" / "stream.md").write_text(
            "# foo\n\nalready migrated\n", encoding="utf-8"
        )

        did = migrate_monolithic_stream_md(tmp_path)
        assert did is False
        # Legacy file untouched
        assert legacy.exists()

    def test_migrate_skipped_when_no_sections(self, tmp_path):
        # An index with no ``## `` headings must not trigger migration.
        (tmp_path / "stream.md").write_text("# WS\n\njust a note\n", encoding="utf-8")
        did = migrate_monolithic_stream_md(tmp_path)
        assert did is False
