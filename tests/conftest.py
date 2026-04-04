"""Shared fixtures for DailyStream integration tests."""

import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def tmp_workspace(tmp_path):
    """Provide a clean temporary directory for workspace operations."""
    return tmp_path


@pytest.fixture()
def tmp_config_dir(tmp_path, monkeypatch):
    """Redirect all config/state files to a temp directory.

    This patches CONFIG_DIR, CONFIG_FILE, STATE_FILE and
    DEFAULT_WORKSPACE_ROOT so tests never touch the real user home.
    """
    config_dir = tmp_path / ".dailystream"
    config_dir.mkdir()

    monkeypatch.setattr("dailystream.config.CONFIG_DIR", config_dir)
    monkeypatch.setattr("dailystream.config.CONFIG_FILE", config_dir / "config.json")
    monkeypatch.setattr("dailystream.config.STATE_FILE", config_dir / "state.json")
    monkeypatch.setattr(
        "dailystream.config.DEFAULT_WORKSPACE_ROOT", config_dir / "workspaces"
    )
    return config_dir


@pytest.fixture()
def sample_config(tmp_config_dir):
    """Write a sample config.json and return a Config instance."""
    from dailystream.config import Config

    cfg = Config.load()  # will create default config in tmp dir
    return cfg


@pytest.fixture()
def workspace_with_pipeline(tmp_workspace, tmp_config_dir):
    """Create a full workspace with one pipeline and return (wm, pm, pipeline_name).

    This is the most commonly needed fixture: a ready-to-use workspace
    with an active pipeline so entries can be added right away.
    """
    from dailystream.workspace import WorkspaceManager
    from dailystream.pipeline import PipelineManager

    wm = WorkspaceManager()
    ws_dir = wm.create(base_path=tmp_workspace, title="测试工作区")
    pm = PipelineManager(ws_dir)

    pipeline_name = "test-pipeline"
    pm.create(pipeline_name)
    wm.add_pipeline(pipeline_name)
    wm.activate_pipeline(pipeline_name)

    return wm, pm, pipeline_name
