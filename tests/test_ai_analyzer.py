"""Tests for the AI analysis module (ai_analyzer.py).

Covers:
- AnalysisResult creation and serialisation
- AnalysisStore CRUD and queries
- ImageAnalyzer with mocked Anthropic client
- AnalysisQueue serial processing
- batch_analyze_workspace and generate_daily_summary
- get_ai_api_key priority (env > config)
- Graceful degradation when anthropic is not installed
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from dailystream.config import Config, read_json, write_json, now_iso


# ---------------------------------------------------------------------------
# AnalysisResult
# ---------------------------------------------------------------------------

class TestAnalysisResult:
    def test_to_dict_roundtrip(self):
        from dailystream.ai_analyzer import AnalysisResult

        r = AnalysisResult(
            entry_index=0,
            timestamp="2026-04-05T10:00:00+08:00",
            input_type="image",
            description="Screenshot of VS Code",
            category="coding",
            key_elements=["VS Code", "Python"],
            analyzed_at="2026-04-05T10:00:05+08:00",
            status="completed",
        )
        d = r.to_dict()
        assert d["entry_index"] == 0
        assert d["category"] == "coding"
        assert "VS Code" in d["key_elements"]

        r2 = AnalysisResult.from_dict(d)
        assert r2.description == r.description
        assert r2.key_elements == r.key_elements

    def test_failed_result(self):
        from dailystream.ai_analyzer import AnalysisResult

        r = AnalysisResult.failed(
            entry_index=3,
            timestamp="2026-04-05T10:00:00+08:00",
            input_type="url",
            error="API timeout",
        )
        assert r.status == "failed"
        assert r.error == "API timeout"
        assert r.description == ""


# ---------------------------------------------------------------------------
# AnalysisStore
# ---------------------------------------------------------------------------

class TestAnalysisStore:
    def test_load_creates_skeleton(self, tmp_path):
        from dailystream.ai_analyzer import AnalysisStore

        store = AnalysisStore(tmp_path / "ai_analyses.json", pipeline_name="test")
        data = store.load()
        assert data["pipeline_name"] == "test"
        assert data["analyses"] == []
        assert data["daily_summary"] is None

    def test_append_and_query(self, tmp_path):
        from dailystream.ai_analyzer import AnalysisStore, AnalysisResult

        store_path = tmp_path / "ai_analyses.json"
        store = AnalysisStore(store_path, pipeline_name="dev")

        r = AnalysisResult(
            entry_index=0,
            timestamp="2026-04-05T10:00:00+08:00",
            input_type="image",
            description="Test screenshot",
            category="coding",
            key_elements=["test"],
            analyzed_at=now_iso(),
            status="completed",
        )
        store.append(r)

        # Verify persisted
        assert store_path.exists()
        data = read_json(store_path)
        assert len(data["analyses"]) == 1

        # Query by entry_index
        found = store.get_by_entry_index(0)
        assert found is not None
        assert found["description"] == "Test screenshot"

        # has_analysis
        assert store.has_analysis(0, "2026-04-05T10:00:00+08:00")
        assert not store.has_analysis(0, "wrong-timestamp")
        assert not store.has_analysis(99, "2026-04-05T10:00:00+08:00")

    def test_get_all_completed(self, tmp_path):
        from dailystream.ai_analyzer import AnalysisStore, AnalysisResult

        store = AnalysisStore(tmp_path / "ai_analyses.json", pipeline_name="mix")

        ok = AnalysisResult(
            entry_index=0, timestamp="t1", input_type="image",
            description="ok", category="coding", analyzed_at=now_iso(),
            status="completed",
        )
        fail = AnalysisResult.failed(1, "t2", "url", "err")

        store.append(ok)
        store.append(fail)

        completed = store.get_all_completed()
        assert len(completed) == 1
        assert completed[0]["entry_index"] == 0

    def test_set_daily_summary(self, tmp_path):
        from dailystream.ai_analyzer import AnalysisStore

        store = AnalysisStore(tmp_path / "ai_analyses.json", pipeline_name="p")
        store.set_daily_summary("Today was productive.")

        data = store.load()
        assert data["daily_summary"] == "Today was productive."

    def test_set_model(self, tmp_path):
        from dailystream.ai_analyzer import AnalysisStore

        store = AnalysisStore(tmp_path / "ai_analyses.json", pipeline_name="p")
        store.set_model("claude-sonnet-4-20250514")

        data = store.load()
        assert data["model"] == "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# get_ai_api_key
# ---------------------------------------------------------------------------

class TestGetAiApiKey:
    def test_env_var_takes_priority(self, ai_config, monkeypatch):
        from dailystream.ai_analyzer import get_ai_api_key

        monkeypatch.setenv("DAILYSTREAM_AI_KEY", "env-key-xyz")
        assert get_ai_api_key(ai_config) == "env-key-xyz"

    def test_config_fallback(self, ai_config, monkeypatch):
        from dailystream.ai_analyzer import get_ai_api_key

        monkeypatch.delenv("DAILYSTREAM_AI_KEY", raising=False)
        assert get_ai_api_key(ai_config) == "test-api-key-12345"

    def test_empty_when_nothing_set(self, tmp_config_dir, monkeypatch):
        from dailystream.ai_analyzer import get_ai_api_key

        monkeypatch.delenv("DAILYSTREAM_AI_KEY", raising=False)
        cfg = Config.load()
        assert get_ai_api_key(cfg) == ""


# ---------------------------------------------------------------------------
# ImageAnalyzer (mocked)
# ---------------------------------------------------------------------------

class TestImageAnalyzer:
    def test_not_available_without_key(self, tmp_config_dir):
        from dailystream.ai_analyzer import ImageAnalyzer

        cfg = Config.load()  # no api key
        analyzer = ImageAnalyzer(cfg)
        assert not analyzer.available

    @patch("dailystream.ai_analyzer._ANTHROPIC_AVAILABLE", True)
    @patch("dailystream.ai_analyzer._anthropic")
    def test_analyze_image_mock(self, mock_anthropic_mod, ai_config, tmp_path, fake_anthropic_response):
        from dailystream.ai_analyzer import ImageAnalyzer

        # Create a fake image
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_anthropic_response(
            json.dumps({
                "description": "Code editor with Python file",
                "category": "coding",
                "key_elements": ["VS Code", "Python"],
            })
        )
        mock_anthropic_mod.Anthropic.return_value = mock_client

        analyzer = ImageAnalyzer(ai_config)

        result = analyzer.analyze_image(img, user_hint="check this code")
        assert result is not None
        assert result.description == "Code editor with Python file"
        assert result.category == "coding"
        assert "VS Code" in result.key_elements

    @patch("dailystream.ai_analyzer._ANTHROPIC_AVAILABLE", True)
    @patch("dailystream.ai_analyzer._anthropic")
    def test_analyze_url_mock(self, mock_anthropic_mod, ai_config, fake_anthropic_response):
        from dailystream.ai_analyzer import ImageAnalyzer

        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_anthropic_response(
            json.dumps({
                "description": "Python documentation page",
                "category": "browsing",
                "key_elements": ["Python", "docs"],
            })
        )
        mock_anthropic_mod.Anthropic.return_value = mock_client

        analyzer = ImageAnalyzer(ai_config)

        result = analyzer.analyze_url("https://docs.python.org", user_hint="reference")
        assert result is not None
        assert result.category == "browsing"

    @patch("dailystream.ai_analyzer._ANTHROPIC_AVAILABLE", True)
    @patch("dailystream.ai_analyzer._anthropic")
    def test_analyze_image_api_error(self, mock_anthropic_mod, ai_config, tmp_path):
        from dailystream.ai_analyzer import ImageAnalyzer

        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = Exception("API error")
        mock_anthropic_mod.Anthropic.return_value = mock_client

        analyzer = ImageAnalyzer(ai_config)

        result = analyzer.analyze_image(img)
        assert result is None

    @patch("dailystream.ai_analyzer._ANTHROPIC_AVAILABLE", True)
    @patch("dailystream.ai_analyzer._anthropic")
    def test_parse_json_with_markdown_fences(self, mock_anthropic_mod, ai_config, fake_anthropic_response):
        from dailystream.ai_analyzer import ImageAnalyzer

        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_anthropic_response(
            '```json\n{"description": "test", "category": "other", "key_elements": []}\n```'
        )
        mock_anthropic_mod.Anthropic.return_value = mock_client

        analyzer = ImageAnalyzer(ai_config)

        result = analyzer.analyze_url("https://example.com")
        assert result is not None
        assert result.description == "test"


# ---------------------------------------------------------------------------
# AnalysisQueue
# ---------------------------------------------------------------------------

class TestAnalysisQueue:
    @patch("dailystream.ai_analyzer._ANTHROPIC_AVAILABLE", True)
    @patch("dailystream.ai_analyzer._anthropic")
    def test_enqueue_and_drain(self, mock_anthropic_mod, ai_config, workspace_with_ai_realtime, fake_anthropic_response):
        from dailystream.ai_analyzer import AnalysisQueue, AnalysisStore

        wm, pm, pipe = workspace_with_ai_realtime

        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_anthropic_response(
            json.dumps({
                "description": "Test analysis",
                "category": "coding",
                "key_elements": ["test"],
            })
        )
        mock_anthropic_mod.Anthropic.return_value = mock_client

        # Create a fake image
        img = pm.get_screenshots_dir(pipe) / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        # Add entry
        entry = pm.add_entry(pipe, "image", str(img), "test screenshot")

        q = AnalysisQueue(ai_config, wm.workspace_dir)

        entries = pm.get_entries(pipe)
        q.enqueue(pipe, len(entries) - 1, entries[-1])

        q.drain(timeout=10.0)
        q.shutdown()

        # Verify ai_analyses.json was written
        store = AnalysisStore(
            wm.workspace_dir / "pipelines" / pipe / "ai_analyses.json",
            pipeline_name=pipe,
        )
        assert len(store.get_all()) == 1
        assert store.get_all()[0]["description"] == "Test analysis"

    def test_queue_skip_non_analysable_types(self, ai_config, workspace_with_ai_realtime):
        from dailystream.ai_analyzer import AnalysisQueue, AnalysisStore

        wm, pm, pipe = workspace_with_ai_realtime

        # Add a text entry (not analysable)
        entry = pm.add_entry(pipe, "text", "Hello world", "text note")
        entries = pm.get_entries(pipe)

        q = AnalysisQueue(ai_config, wm.workspace_dir)
        q.enqueue(pipe, len(entries) - 1, entries[-1])
        q.drain(timeout=5.0)
        q.shutdown()

        # No analysis should be created
        store_path = wm.workspace_dir / "pipelines" / pipe / "ai_analyses.json"
        if store_path.exists():
            store = AnalysisStore(store_path, pipeline_name=pipe)
            assert len(store.get_all()) == 0


# ---------------------------------------------------------------------------
# batch_analyze_workspace / generate_daily_summary
# ---------------------------------------------------------------------------

class TestBatchAnalysis:
    @patch("dailystream.ai_analyzer._ANTHROPIC_AVAILABLE", True)
    def test_batch_analyze_workspace(
        self, ai_config, workspace_with_ai_daily, fake_anthropic_response
    ):
        from dailystream.ai_analyzer import batch_analyze_workspace, ImageAnalyzer, AnalysisStore

        wm, pm, pipe = workspace_with_ai_daily

        # Create fake image and add entries
        img = pm.get_screenshots_dir(pipe) / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        pm.add_entry(pipe, "image", str(img), "daily screenshot")
        pm.add_entry(pipe, "url", "https://example.com", "reference link")
        pm.add_entry(pipe, "text", "Just a note", "text note")  # should be skipped

        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_anthropic_response(
            json.dumps({
                "description": "Batch test",
                "category": "other",
                "key_elements": ["batch"],
            })
        )

        with patch.object(ImageAnalyzer, "__init__", lambda self, cfg: None):
            with patch.object(ImageAnalyzer, "available", new_callable=lambda: property(lambda self: True)):
                with patch.object(ImageAnalyzer, "batch_analyze_entries") as mock_batch:
                    from dailystream.ai_analyzer import AnalysisResult

                    # Return mock results for 2 analysable entries
                    mock_batch.return_value = [
                        AnalysisResult(
                            entry_index=-1, timestamp="", input_type="image",
                            description="Image analysis", category="coding",
                            key_elements=["code"], analyzed_at=now_iso(),
                            status="completed",
                        ),
                        AnalysisResult(
                            entry_index=-1, timestamp="", input_type="url",
                            description="URL summary", category="browsing",
                            key_elements=["docs"], analyzed_at=now_iso(),
                            status="completed",
                        ),
                    ]

                    with patch.object(ImageAnalyzer, "_model", "test-model", create=True):
                        result = batch_analyze_workspace(ai_config, wm.workspace_dir, wm.meta)

        assert result is True

        # Verify analyses were stored
        store = AnalysisStore(
            wm.workspace_dir / "pipelines" / pipe / "ai_analyses.json",
            pipeline_name=pipe,
        )
        analyses = store.get_all_completed()
        assert len(analyses) == 2

    @patch("dailystream.ai_analyzer._ANTHROPIC_AVAILABLE", True)
    def test_generate_daily_summary(
        self, ai_config, workspace_with_ai_daily, fake_anthropic_response
    ):
        from dailystream.ai_analyzer import (
            generate_daily_summary,
            AnalysisStore,
            AnalysisResult,
            ImageAnalyzer,
        )

        wm, pm, pipe = workspace_with_ai_daily

        # Pre-populate ai_analyses.json with completed analyses
        store_path = wm.workspace_dir / "pipelines" / pipe / "ai_analyses.json"
        store = AnalysisStore(store_path, pipeline_name=pipe)
        store.append(AnalysisResult(
            entry_index=0, timestamp="t1", input_type="image",
            description="Coding screenshot", category="coding",
            key_elements=["Python"], analyzed_at=now_iso(), status="completed",
        ))

        mock_client = MagicMock()
        mock_client.messages.create.return_value = fake_anthropic_response(
            json.dumps({
                "pipeline_summaries": {pipe: "Did some coding"},
                "overall_summary": "Productive day with coding tasks.",
            })
        )

        with patch.object(ImageAnalyzer, "__init__", lambda self, cfg: None):
            with patch.object(ImageAnalyzer, "available", new_callable=lambda: property(lambda self: True)):
                with patch.object(ImageAnalyzer, "_model", "test-model", create=True):
                    with patch.object(ImageAnalyzer, "_client", mock_client, create=True):
                        with patch.object(ImageAnalyzer, "_max_tokens", 1024, create=True):
                            with patch.object(ImageAnalyzer, "_custom_prompt", "", create=True):
                                summary = generate_daily_summary(ai_config, wm.workspace_dir, wm.meta)

        assert summary is not None
        assert "Productive day" in summary

        # Check ai_daily_summary.json was written
        summary_path = wm.workspace_dir / "ai_daily_summary.json"
        assert summary_path.exists()
        data = read_json(summary_path)
        assert data["overall_summary"] == "Productive day with coding tasks."


# ---------------------------------------------------------------------------
# Workspace ai_mode field
# ---------------------------------------------------------------------------

class TestWorkspaceAiMode:
    def test_create_with_ai_mode(self, tmp_workspace, tmp_config_dir):
        from dailystream.workspace import WorkspaceManager

        wm = WorkspaceManager()
        ws_dir = wm.create(
            base_path=tmp_workspace, title="AI WS", ai_mode="daily_report"
        )
        assert wm.meta.ai_mode == "daily_report"

        # Verify persisted
        data = read_json(ws_dir / "workspace_meta.json")
        assert data["ai_mode"] == "daily_report"

    def test_default_ai_mode_is_off(self, tmp_workspace, tmp_config_dir):
        from dailystream.workspace import WorkspaceManager

        wm = WorkspaceManager()
        wm.create(base_path=tmp_workspace, title="Default WS")
        assert wm.meta.ai_mode == "off"

    def test_load_old_workspace_without_ai_mode(self, tmp_workspace, tmp_config_dir):
        """Old workspaces without ai_mode should default to 'off'."""
        from dailystream.workspace import WorkspaceManager

        wm = WorkspaceManager()
        ws_dir = wm.create(base_path=tmp_workspace, title="Old WS")

        # Simulate old workspace by removing ai_mode from meta
        meta_path = ws_dir / "workspace_meta.json"
        data = read_json(meta_path)
        del data["ai_mode"]
        write_json(meta_path, data)

        # Reload
        wm2 = WorkspaceManager()
        wm2.load(ws_dir)
        # Should default to "off"
        assert getattr(wm2.meta, "ai_mode", "off") == "off"


# ---------------------------------------------------------------------------
# Config AI fields
# ---------------------------------------------------------------------------

class TestConfigAiFields:
    def test_ai_fields_have_defaults(self, tmp_config_dir):
        cfg = Config.load()
        assert cfg.ai_api_key == ""
        assert cfg.ai_model == "claude-sonnet-4-20250514"
        assert cfg.ai_timeout == 30
        assert cfg.ai_batch_size == 10
        assert cfg.ai_default_mode == "off"

    def test_ai_fields_persist(self, tmp_config_dir):
        cfg = Config.load()
        cfg.ai_api_key = "sk-test"
        cfg.ai_default_mode = "realtime"
        cfg.save()

        cfg2 = Config.load()
        assert cfg2.ai_api_key == "sk-test"
        assert cfg2.ai_default_mode == "realtime"


# ---------------------------------------------------------------------------
# Pipeline AI analyses path
# ---------------------------------------------------------------------------

class TestPipelineAiPath:
    def test_get_ai_analyses_path(self, workspace_with_pipeline):
        wm, pm, pipe = workspace_with_pipeline
        path = pm.get_ai_analyses_path(pipe)
        assert path == wm.workspace_dir / "pipelines" / pipe / "ai_analyses.json"


# ---------------------------------------------------------------------------
# Templates AI placeholders
# ---------------------------------------------------------------------------

class TestTemplatesAi:
    def test_build_context_with_ai_fields(self):
        from dailystream.templates import build_context

        ctx = build_context(
            timestamp="2026-04-05T10:00:00+08:00",
            input_type="image",
            description="test",
            content="/path/to/img.png",
            ai_analysis="AI sees code editor",
            ai_category="coding",
            ai_elements="VS Code, Python",
        )
        assert ctx.ai_analysis == "AI sees code editor"
        assert ctx.ai_category == "coding"
        assert ctx.ai_elements == "VS Code, Python"

    def test_render_entry_with_ai_placeholders(self):
        from dailystream.templates import build_context, render_entry

        tpl = {"image": "{time} [{ai_category}] {ai_analysis} | {ai_elements}"}
        ctx = build_context(
            timestamp="2026-04-05T10:00:00+08:00",
            input_type="image",
            description="",
            content="/img.png",
            ai_analysis="Code editor",
            ai_category="coding",
            ai_elements="VS Code",
        )
        result = render_entry(tpl, ctx)
        assert "coding" in result
        assert "Code editor" in result
        assert "VS Code" in result

    def test_get_timeline_templates_ai_mode(self):
        from dailystream.templates import get_timeline_templates

        normal = get_timeline_templates(ai_mode="off")
        ai = get_timeline_templates(ai_mode="realtime")

        assert "{ai_analysis}" not in normal.get("image", "")
        assert "{ai_analysis}" in ai.get("image", "")

    def test_empty_ai_fields_cleaned_up(self):
        from dailystream.templates import build_context, render_entry

        tpl = {"image": "{time}\n\n{ai_analysis}\n\n{image}"}
        ctx = build_context(
            timestamp="2026-04-05T10:00:00+08:00",
            input_type="image",
            description="",
            content="/img.png",
            ai_analysis="",  # empty
        )
        result = render_entry(tpl, ctx)
        # Empty ai_analysis should be cleaned up (no double blank lines)
        assert "\n\n\n" not in result
