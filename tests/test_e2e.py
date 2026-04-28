"""End-to-end integration tests.

Simulates the full lifecycle: create workspace → create pipeline →
add entries → sync to Markdown → end workspace → generate timeline.
"""

from pathlib import Path

import pytest

from dailystream.config import Config, read_json, now_iso
from dailystream.workspace import WorkspaceManager
from dailystream.pipeline import PipelineManager
from dailystream.note_sync import NoteSyncManager


class TestFullLifecycle:
    """Complete workspace lifecycle from creation to timeline report."""

    def test_create_add_entries_end(self, tmp_workspace, tmp_config_dir):
        """Full flow: create → add entries → sync → end → verify."""
        config = Config.load()

        # 1. Create workspace
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace, title="端到端测试")
        assert wm.is_active

        # 2. Create pipeline
        pm = PipelineManager(ws_dir)
        pm.create("research")
        wm.add_pipeline("research")
        wm.activate_pipeline("research")
        assert wm.get_active_pipeline() == "research"

        # 3. Add diverse entries
        entries = [
            ("text", "Hello world", "文本记录"),
            ("url", "https://example.com/article", "参考文章"),
            ("text", "Another text note", "第二条笔记"),
        ]
        for input_type, content, desc in entries:
            entry = pm.add_entry("research", input_type, content, desc)

            # 4. Sync each entry to Markdown
            syncer = NoteSyncManager(config, workspace_dir=ws_dir)
            syncer.sync_entry(wm.meta, "research", entry)

        # Verify per-pipeline stream.md
        md_path = ws_dir / "pipelines" / "research" / "stream.md"
        assert md_path.exists()
        md_text = md_path.read_text(encoding="utf-8")

        # Pipeline files now carry the pipeline name as their title.
        assert "# research" in md_text
        assert "文本记录" in md_text
        assert "参考文章" in md_text
        assert "https://example.com/article" in md_text
        assert "第二条笔记" in md_text

        # 5. End workspace → generate timeline
        report_path = wm.end(config=config)

        assert not wm.is_active
        assert report_path is not None
        report = Path(report_path)
        assert report.exists()

        report_text = report.read_text(encoding="utf-8")
        assert "# 端到端测试" in report_text
        assert "research" in report_text
        assert "Timeline" in report_text

    def test_multiple_pipelines_lifecycle(self, tmp_workspace, tmp_config_dir):
        """Multiple pipelines with entries, switching between them."""
        config = Config.load()
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace, title="多管道测试")
        pm = PipelineManager(ws_dir)

        # Create two pipelines
        for name in ("design", "coding"):
            pm.create(name)
            wm.add_pipeline(name)

        # Add to 'design'
        wm.activate_pipeline("design")
        entry1 = pm.add_entry("design", "text", "UI wireframe", "设计稿")
        syncer = NoteSyncManager(config, workspace_dir=ws_dir)
        syncer.sync_entry(wm.meta, "design", entry1)

        # Switch to 'coding'
        wm.activate_pipeline("coding")
        assert wm.get_active_pipeline() == "coding"
        entry2 = pm.add_entry("coding", "url", "https://docs.python.org", "Python 文档")
        syncer.sync_entry(wm.meta, "coding", entry2)

        # Switch back to 'design'
        wm.activate_pipeline("design")
        entry3 = pm.add_entry("design", "text", "Color palette", "配色方案")
        syncer.sync_entry(wm.meta, "design", entry3)

        # Verify per-pipeline markdown structure
        design_md = (ws_dir / "pipelines" / "design" / "stream.md").read_text(encoding="utf-8")
        coding_md = (ws_dir / "pipelines" / "coding" / "stream.md").read_text(encoding="utf-8")
        assert "# design" in design_md
        assert "# coding" in coding_md
        assert "设计稿" in design_md
        assert "Python 文档" in coding_md

        # Design entries grouped in their own file, not bleeding into coding.
        assert "配色方案" in design_md
        assert "配色方案" not in coding_md

        # End and verify timeline
        report_path = wm.end(config=config)
        assert report_path is not None
        report_text = Path(report_path).read_text(encoding="utf-8")
        assert "design" in report_text
        assert "coding" in report_text

    def test_workspace_reload(self, tmp_workspace, tmp_config_dir):
        """Create workspace, close, reload, and continue adding entries."""
        config = Config.load()

        # Create and add one entry
        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace, title="重载测试")
        pm = PipelineManager(ws_dir)
        pm.create("main")
        wm.add_pipeline("main")
        wm.activate_pipeline("main")

        entry1 = pm.add_entry("main", "text", "Before reload", "重载前")
        syncer = NoteSyncManager(config, workspace_dir=ws_dir)
        syncer.sync_entry(wm.meta, "main", entry1)

        # Simulate app restart: new WorkspaceManager loading from disk
        wm2 = WorkspaceManager()
        assert wm2.load(ws_dir)
        assert wm2.meta.title == "重载测试"
        assert wm2.get_active_pipeline() == "main"

        # Continue adding entries
        pm2 = PipelineManager(ws_dir)
        entry2 = pm2.add_entry("main", "text", "After reload", "重载后")
        syncer2 = NoteSyncManager(config, workspace_dir=ws_dir)
        syncer2.sync_entry(wm2.meta, "main", entry2)

        md_text = (ws_dir / "pipelines" / "main" / "stream.md").read_text(encoding="utf-8")
        assert "重载前" in md_text
        assert "重载后" in md_text

    def test_empty_workspace_end(self, tmp_workspace, tmp_config_dir):
        """Ending a workspace with no entries should not crash."""
        config = Config.load()
        wm = WorkspaceManager()
        wm.create(base_path=tmp_workspace, title="Empty WS")

        result = wm.end(config=config)
        # No entries → timeline returns None
        assert result is None
        assert not wm.is_active

    def test_entries_persisted_to_context_json(self, workspace_with_pipeline):
        """Entries added via PipelineManager are persisted in context.json."""
        wm, pm, pipe = workspace_with_pipeline

        pm.add_entry(pipe, "text", "Persistent data", "desc")

        # Read directly from disk
        ctx_path = wm.workspace_dir / "pipelines" / pipe / "context.json"
        data = read_json(ctx_path)
        assert len(data["entries"]) == 1
        assert data["entries"][0]["input_content"] == "Persistent data"
        assert data["entries"][0]["synced"] is False

    def test_mark_synced_persists(self, workspace_with_pipeline):
        """mark_entry_synced flag is persisted to disk."""
        wm, pm, pipe = workspace_with_pipeline

        pm.add_entry(pipe, "text", "To sync", "desc")
        pm.mark_entry_synced(pipe, 0)

        ctx_path = wm.workspace_dir / "pipelines" / pipe / "context.json"
        data = read_json(ctx_path)
        assert data["entries"][0]["synced"] is True

    def test_realtime_mode_end_generates_timeline(self, tmp_workspace, tmp_config_dir):
        """Realtime mode: end generates timeline with AI placeholders."""
        config = Config.load()

        wm = WorkspaceManager()
        ws_dir = wm.create(
            base_path=tmp_workspace, title="实时模式E2E", ai_mode="realtime"
        )
        pm = PipelineManager(ws_dir)
        pm.create("dev")
        wm.add_pipeline("dev")
        wm.activate_pipeline("dev")

        # Add text entry (no AI analysis for text type)
        pm.add_entry("dev", "text", "Hello world", "文本记录")

        # Manually write a fake ai_analyses.json (simulating what the queue would do)
        from dailystream.config import write_json as wj
        ai_path = ws_dir / "pipelines" / "dev" / "ai_analyses.json"
        wj(ai_path, {
            "pipeline_name": "dev",
            "model": "test-model",
            "analyses": [],
            "daily_summary": None,
        })

        report_path = wm.end(config=config)
        assert report_path is not None

        report = Path(report_path).read_text(encoding="utf-8")
        assert "实时模式E2E" in report
        assert "**AI Mode**: realtime" in report

    def test_daily_report_mode_end_includes_summary(self, tmp_workspace, tmp_config_dir):
        """Daily report mode: end generates timeline with AI summary section."""
        config = Config.load()

        wm = WorkspaceManager()
        ws_dir = wm.create(
            base_path=tmp_workspace, title="日报模式E2E", ai_mode="daily_report"
        )
        pm = PipelineManager(ws_dir)
        pm.create("task1", description="前端开发", goal="完成组件")
        wm.add_pipeline("task1")
        wm.activate_pipeline("task1")

        pm.add_entry("task1", "text", "Started coding", "开始编码")

        # Pre-populate ai_analyses.json and ai_daily_summary.json
        from dailystream.config import write_json as wj

        ai_path = ws_dir / "pipelines" / "task1" / "ai_analyses.json"
        wj(ai_path, {
            "pipeline_name": "task1",
            "model": "test-model",
            "analyses": [
                {
                    "entry_index": 0,
                    "timestamp": pm.get_entries("task1")[0]["timestamp"],
                    "input_type": "text",
                    "description": "Developer started coding",
                    "category": "coding",
                    "key_elements": ["code"],
                    "analyzed_at": now_iso(),
                    "status": "completed",
                }
            ],
            "daily_summary": "前端开发相关工作",
        })

        summary_path = ws_dir / "ai_daily_summary.json"
        wj(summary_path, {
            "generated_at": now_iso(),
            "model": "test-model",
            "pipeline_summaries": {"task1": "完成了前端组件开发"},
            "overall_summary": "今日主要完成前端开发任务。",
        })

        # End workspace (skip actual AI calls by not having anthropic)
        report_path = wm.end(config=config)
        assert report_path is not None

        report = Path(report_path).read_text(encoding="utf-8")
        assert "日报模式E2E" in report
        assert "**AI Mode**: daily_report" in report
        assert "AI Daily Summary" in report
        assert "今日主要完成前端开发任务" in report
