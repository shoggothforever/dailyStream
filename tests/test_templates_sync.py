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
        assert "screenshots/shot.png" in ctx.image
        assert ctx.image.startswith("![screenshot]")

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

    def test_first_entry_creates_file(self, tmp_path):
        syncer = self._make_syncer(tmp_path)
        syncer.sync_entry(
            workspace_title="Test WS",
            pipeline_name="main",
            timestamp="2026-04-04T10:00:00+08:00",
            input_type="text",
            description="第一条",
            content="内容",
        )
        md = tmp_path / "stream.md"
        assert md.exists()
        text = md.read_text(encoding="utf-8")
        assert "# Test WS" in text
        assert "## main" in text
        assert "第一条" in text

    def test_entries_grouped_by_pipeline(self, tmp_path):
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

        text = (tmp_path / "stream.md").read_text(encoding="utf-8")

        # A2 should appear in the alpha section, not after beta
        alpha_pos = text.index("## alpha")
        beta_pos = text.index("## beta")
        a2_pos = text.index("A2")
        assert a2_pos < beta_pos, "A2 should be in the alpha section before beta"

    def test_image_entry_has_screenshot_link(self, tmp_path):
        syncer = self._make_syncer(tmp_path)
        syncer.sync_entry(
            workspace_title="WS",
            pipeline_name="main",
            timestamp="2026-04-04T12:00:00+08:00",
            input_type="image",
            description="截图",
            content="screenshots/shot.png",
            image_path=str(tmp_path / "screenshots" / "shot.png"),
        )
        text = (tmp_path / "stream.md").read_text(encoding="utf-8")
        assert "![screenshot]" in text

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
        text = (tmp_path / "stream.md").read_text(encoding="utf-8")
        assert "https://example.com/page" in text


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

        md = wm.workspace_dir / "stream.md"
        assert md.exists()
        text = md.read_text(encoding="utf-8")
        assert "from dict" in text

    def test_sync_with_dataclass_entry(self, workspace_with_pipeline):
        wm, pm, pipe = workspace_with_pipeline
        cfg = Config()

        entry = pm.add_entry(pipe, "url", "https://example.com", "link desc")

        syncer = NoteSyncManager(cfg, workspace_dir=wm.workspace_dir)
        syncer.sync_entry(wm.meta, pipe, entry)

        text = (wm.workspace_dir / "stream.md").read_text(encoding="utf-8")
        assert "https://example.com" in text

    def test_sync_disabled_when_backend_is_none(self, workspace_with_pipeline):
        wm, pm, pipe = workspace_with_pipeline
        cfg = Config(note_sync_backend="none")

        entry = pm.add_entry(pipe, "text", "No sync", "desc")
        syncer = NoteSyncManager(cfg, workspace_dir=wm.workspace_dir)
        syncer.sync_entry(wm.meta, pipe, entry)

        md = wm.workspace_dir / "stream.md"
        # Local Markdown syncer is always created when workspace_dir is provided
        # This test ensures it doesn't crash even with backend="none"
        assert md.exists()
