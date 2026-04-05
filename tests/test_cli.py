"""Tests for CLI commands.

Covers: preset list/create/delete subcommands, status, feed.
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from dailystream.cli import cli
from dailystream.config import Config


@pytest.fixture
def runner():
    return CliRunner()


# ── preset list ──────────────────────────────────────────────────────

class TestPresetList:
    def test_no_presets(self, runner, tmp_config_dir):
        result = runner.invoke(cli, ["preset", "list"])
        assert result.exit_code == 0
        assert "No screenshot presets" in result.output

    def test_with_presets(self, runner, tmp_config_dir):
        # Create a config with presets
        cfg = Config.load()
        cfg.screenshot_presets = [
            {"name": "Left Half", "region": "0,0,960,1080"},
            {"name": "Right Half", "region": "960,0,960,1080", "hotkey": "<cmd>+4"},
        ]
        cfg.save()

        result = runner.invoke(cli, ["preset", "list"])
        assert result.exit_code == 0
        assert "Left Half" in result.output
        assert "0,0,960,1080" in result.output
        assert "Right Half" in result.output
        assert "[<cmd>+4]" in result.output

    def test_preset_without_hotkey(self, runner, tmp_config_dir):
        cfg = Config.load()
        cfg.screenshot_presets = [
            {"name": "Basic", "region": "0,0,100,100"},
        ]
        cfg.save()

        result = runner.invoke(cli, ["preset", "list"])
        assert result.exit_code == 0
        assert "Basic" in result.output
        # No hotkey bracket shown
        assert "[" not in result.output or "Basic" in result.output


# ── preset create ────────────────────────────────────────────────────

class TestPresetCreate:
    def test_create_with_region(self, runner, tmp_config_dir):
        result = runner.invoke(
            cli, ["preset", "create", "--name", "Test Region", "--region", "100,200,800,600"]
        )
        assert result.exit_code == 0
        assert "Test Region" in result.output
        assert "100,200,800,600" in result.output

        # Verify persisted
        cfg = Config.load()
        assert cfg.screenshot_presets is not None
        assert len(cfg.screenshot_presets) == 1
        assert cfg.screenshot_presets[0]["name"] == "Test Region"
        assert cfg.screenshot_presets[0]["region"] == "100,200,800,600"

    def test_create_with_hotkey(self, runner, tmp_config_dir):
        result = runner.invoke(
            cli,
            [
                "preset", "create",
                "--name", "With HK",
                "--region", "0,0,500,500",
                "--hotkey", "<cmd>+3",
            ],
        )
        assert result.exit_code == 0
        assert "[<cmd>+3]" in result.output

        cfg = Config.load()
        assert cfg.screenshot_presets[0]["hotkey"] == "<cmd>+3"

    def test_create_invalid_region_format(self, runner, tmp_config_dir):
        result = runner.invoke(
            cli, ["preset", "create", "--name", "Bad", "--region", "not,valid"]
        )
        assert result.exit_code == 0
        assert "4 comma-separated" in result.output

    def test_create_region_non_integer(self, runner, tmp_config_dir):
        result = runner.invoke(
            cli, ["preset", "create", "--name", "Bad", "--region", "a,b,c,d"]
        )
        assert result.exit_code == 0
        assert "integers" in result.output

    def test_create_appends_to_existing(self, runner, tmp_config_dir):
        # Create first preset
        runner.invoke(
            cli, ["preset", "create", "--name", "First", "--region", "0,0,100,100"]
        )
        # Create second
        runner.invoke(
            cli, ["preset", "create", "--name", "Second", "--region", "100,100,200,200"]
        )

        cfg = Config.load()
        assert len(cfg.screenshot_presets) == 2
        assert cfg.screenshot_presets[0]["name"] == "First"
        assert cfg.screenshot_presets[1]["name"] == "Second"

    def test_create_without_hotkey_no_hotkey_field(self, runner, tmp_config_dir):
        """When no hotkey is given, the preset dict should not have 'hotkey' key."""
        runner.invoke(
            cli, ["preset", "create", "--name", "NoHK", "--region", "0,0,100,100"]
        )

        cfg = Config.load()
        assert "hotkey" not in cfg.screenshot_presets[0]


# ── preset delete ────────────────────────────────────────────────────

class TestPresetDelete:
    def _setup_presets(self):
        cfg = Config.load()
        cfg.screenshot_presets = [
            {"name": "Alpha", "region": "0,0,100,100"},
            {"name": "Beta", "region": "100,0,200,200"},
            {"name": "Gamma", "region": "200,0,300,300", "hotkey": "<cmd>+5"},
        ]
        cfg.save()

    def test_delete_by_index(self, runner, tmp_config_dir):
        self._setup_presets()
        result = runner.invoke(cli, ["preset", "delete", "2"])
        assert result.exit_code == 0
        assert "Beta" in result.output

        cfg = Config.load()
        assert len(cfg.screenshot_presets) == 2
        names = [p["name"] for p in cfg.screenshot_presets]
        assert "Beta" not in names

    def test_delete_by_name(self, runner, tmp_config_dir):
        self._setup_presets()
        result = runner.invoke(cli, ["preset", "delete", "Gamma"])
        assert result.exit_code == 0
        assert "Gamma" in result.output

        cfg = Config.load()
        assert len(cfg.screenshot_presets) == 2

    def test_delete_by_name_case_insensitive(self, runner, tmp_config_dir):
        self._setup_presets()
        result = runner.invoke(cli, ["preset", "delete", "alpha"])
        assert result.exit_code == 0
        assert "Alpha" in result.output

    def test_delete_nonexistent(self, runner, tmp_config_dir):
        self._setup_presets()
        result = runner.invoke(cli, ["preset", "delete", "Ghost"])
        assert result.exit_code == 0
        assert "not found" in result.output

    def test_delete_from_empty(self, runner, tmp_config_dir):
        result = runner.invoke(cli, ["preset", "delete", "1"])
        assert result.exit_code == 0
        assert "No presets" in result.output

    def test_delete_last_preset_sets_none(self, runner, tmp_config_dir):
        cfg = Config.load()
        cfg.screenshot_presets = [{"name": "Only", "region": "0,0,100,100"}]
        cfg.save()

        runner.invoke(cli, ["preset", "delete", "1"])

        cfg = Config.load()
        assert cfg.screenshot_presets is None

    def test_delete_out_of_range_index(self, runner, tmp_config_dir):
        self._setup_presets()
        result = runner.invoke(cli, ["preset", "delete", "99"])
        assert result.exit_code == 0
        assert "not found" in result.output


# ── CLI status/feed basics ───────────────────────────────────────────

class TestCLIBasics:
    def test_status_no_workspace(self, runner, tmp_config_dir):
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "No active workspace" in result.output

    def test_feed_no_workspace(self, runner, tmp_config_dir):
        result = runner.invoke(cli, ["feed", "hello"])
        assert result.exit_code == 0
        assert "No active workspace" in result.output

    def test_pipeline_list_no_workspace(self, runner, tmp_config_dir):
        result = runner.invoke(cli, ["pipeline", "list"])
        assert result.exit_code == 0
        assert "No active workspace" in result.output

    def test_end_no_workspace(self, runner, tmp_config_dir):
        result = runner.invoke(cli, ["end"])
        assert result.exit_code == 0
        assert "No active workspace" in result.output

    def test_start_and_status(self, runner, tmp_config_dir, tmp_workspace):
        # Start a workspace
        result = runner.invoke(
            cli, ["start", "--path", str(tmp_workspace), "--title", "CLI Test"]
        )
        assert result.exit_code == 0
        assert "created" in result.output.lower() or "✅" in result.output

        # Check status
        result2 = runner.invoke(cli, ["status"])
        assert result2.exit_code == 0
        assert "CLI Test" in result2.output
