"""Integration tests for dailystream.rpc_server.

Uses the shared ``tmp_config_dir`` fixture to sandbox all filesystem writes,
so these tests don't touch the real user home.
"""

import io
import json
import threading

import pytest

from dailystream.rpc_server import build_dispatcher, serve


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rpc(method: str, params=None, req_id=1):
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params or {},
    }


# ---------------------------------------------------------------------------
# Dispatcher (end-to-end on a real temp workspace)
# ---------------------------------------------------------------------------


class TestAppMethods:
    def test_ping(self, tmp_config_dir):
        d, _, _ = build_dispatcher()
        resp = d.handle(_rpc("app.ping"))
        assert resp["result"] == "pong"

    def test_version(self, tmp_config_dir):
        d, _, _ = build_dispatcher()
        resp = d.handle(_rpc("app.version"))
        assert "rpc_version" in resp["result"]
        assert "python_version" in resp["result"]

    def test_shutdown_sets_flag(self, tmp_config_dir):
        flag = threading.Event()
        d, _, _ = build_dispatcher(shutdown_flag=flag)
        resp = d.handle(_rpc("app.shutdown"))
        assert resp["result"] == "ok"
        assert flag.is_set()


class TestWorkspaceLifecycle:
    def test_status_no_workspace(self, tmp_config_dir):
        d, _, _ = build_dispatcher()
        resp = d.handle(_rpc("workspace.status"))
        assert resp["result"]["is_active"] is False

    def test_create_open_end_flow(self, tmp_config_dir, tmp_path):
        d, _, _ = build_dispatcher()

        r = d.handle(_rpc("workspace.create", {
            "path": str(tmp_path), "title": "test-ws", "ai_mode": "off",
        }))
        assert r["result"]["ai_mode"] == "off"
        ws_dir = r["result"]["workspace_dir"]

        r = d.handle(_rpc("workspace.status"))
        assert r["result"]["is_active"] is True
        assert r["result"]["title"] == "test-ws"

        r = d.handle(_rpc("workspace.end"))
        # No entries → timeline_report will be None; still succeeds.
        assert "timeline_report" in r["result"]

        # Re-open — the workspace has already been ended; open must
        # re-activate it (clear ended_at) just like the old rumps flow.
        r = d.handle(_rpc("workspace.open", {"path": ws_dir}))
        assert r["result"]["title"] == "test-ws"
        assert r["result"]["ended_at"] is None
        assert r["result"]["is_active"] is True

    def test_create_fails_when_already_active(self, tmp_config_dir, tmp_path):
        d, _, _ = build_dispatcher()
        d.handle(_rpc("workspace.create", {"path": str(tmp_path)}))
        r = d.handle(_rpc("workspace.create", {"path": str(tmp_path)}))
        assert r["error"]["code"] == -32001  # StateConflict

    def test_list_recent_empty(self, tmp_config_dir):
        d, _, _ = build_dispatcher()
        r = d.handle(_rpc("workspace.list_recent"))
        assert r["result"] == []


class TestPipelineMethods:
    def test_pipeline_create_list_switch(self, tmp_config_dir, tmp_path):
        d, _, _ = build_dispatcher()
        d.handle(_rpc("workspace.create", {"path": str(tmp_path)}))

        r = d.handle(_rpc("pipeline.create", {
            "name": "p1", "description": "desc", "goal": "goal",
        }))
        assert r["result"]["name"] == "p1"

        d.handle(_rpc("pipeline.create", {"name": "p2"}))

        r = d.handle(_rpc("pipeline.list"))
        assert set(r["result"]["pipelines"]) == {"p1", "p2"}
        assert r["result"]["active"] == "p2"

        r = d.handle(_rpc("pipeline.switch", {"name": "p1"}))
        assert r["result"]["active"] == "p1"

    def test_pipeline_switch_missing(self, tmp_config_dir, tmp_path):
        d, _, _ = build_dispatcher()
        d.handle(_rpc("workspace.create", {"path": str(tmp_path)}))
        r = d.handle(_rpc("pipeline.switch", {"name": "missing"}))
        assert r["error"]["code"] == -32002  # NotFound

    def test_pipeline_rename_and_delete(self, tmp_config_dir, tmp_path):
        d, _, _ = build_dispatcher()
        d.handle(_rpc("workspace.create", {"path": str(tmp_path)}))
        d.handle(_rpc("pipeline.create", {"name": "old"}))

        r = d.handle(_rpc("pipeline.rename", {"old": "old", "new": "new"}))
        assert r["result"] == {"old": "old", "new": "new"}

        r = d.handle(_rpc("pipeline.list"))
        assert "new" in r["result"]["pipelines"]
        assert r["result"]["active"] == "new"

        r = d.handle(_rpc("pipeline.delete", {"name": "new"}))
        assert r["result"]["deleted"] == "new"

        r = d.handle(_rpc("pipeline.list"))
        assert r["result"]["pipelines"] == []

    def test_pipeline_rename_conflict(self, tmp_config_dir, tmp_path):
        d, _, _ = build_dispatcher()
        d.handle(_rpc("workspace.create", {"path": str(tmp_path)}))
        d.handle(_rpc("pipeline.create", {"name": "a"}))
        d.handle(_rpc("pipeline.create", {"name": "b"}))
        r = d.handle(_rpc("pipeline.rename", {"old": "a", "new": "b"}))
        assert r["error"]["code"] == -32001


class TestFeedMethods:
    def test_feed_text(self, tmp_config_dir, tmp_path):
        d, _, _ = build_dispatcher()
        d.handle(_rpc("workspace.create", {"path": str(tmp_path)}))
        d.handle(_rpc("pipeline.create", {"name": "p"}))
        r = d.handle(_rpc("feed.text", {
            "content": "hello", "description": "greeting",
        }))
        assert r["result"]["entry"]["input_type"] == "text"
        assert r["result"]["entry_index"] == 0

    def test_feed_url(self, tmp_config_dir, tmp_path):
        d, _, _ = build_dispatcher()
        d.handle(_rpc("workspace.create", {"path": str(tmp_path)}))
        d.handle(_rpc("pipeline.create", {"name": "p"}))
        r = d.handle(_rpc("feed.url", {
            "content": "https://example.com",
            "description": "ref",
        }))
        assert r["result"]["entry"]["input_type"] == "url"

    def test_feed_image_missing_file(self, tmp_config_dir, tmp_path):
        d, _, _ = build_dispatcher()
        d.handle(_rpc("workspace.create", {"path": str(tmp_path)}))
        d.handle(_rpc("pipeline.create", {"name": "p"}))
        r = d.handle(_rpc("feed.image", {
            "path": str(tmp_path / "nope.png"),
        }))
        assert r["error"]["code"] == -32002

    def test_feed_publishes_event(self, tmp_config_dir, tmp_path):
        d, _, _ = build_dispatcher()
        d.handle(_rpc("workspace.create", {"path": str(tmp_path)}))
        d.handle(_rpc("pipeline.create", {"name": "p"}))

        received = []
        d.event_bus.subscribe(lambda m, p: received.append((m, p)))

        d.handle(_rpc("feed.text", {"content": "hi"}))
        methods = [m for m, _ in received]
        assert "feed.entry_added" in methods


class TestConfigMethods:
    def test_get_whole_config_hides_key(self, tmp_config_dir):
        d, _, _ = build_dispatcher()
        r = d.handle(_rpc("config.get"))
        assert "ai_api_key" not in r["result"]

    def test_get_and_set_single(self, tmp_config_dir):
        d, state, _ = build_dispatcher()
        r = d.handle(_rpc("config.set", {
            "key": "ai_default_mode", "value": "realtime",
        }))
        assert r["result"]["value"] == "realtime"
        r = d.handle(_rpc("config.get", {"key": "ai_default_mode"}))
        assert r["result"]["value"] == "realtime"

    def test_set_unknown_key(self, tmp_config_dir):
        d, _, _ = build_dispatcher()
        r = d.handle(_rpc("config.set", {"key": "nonexistent", "value": 1}))
        assert r["error"]["code"] == -32002


class TestPresetMethods:
    def test_create_list_delete(self, tmp_config_dir):
        d, _, _ = build_dispatcher()
        d.handle(_rpc("preset.create", {
            "name": "leftHalf", "region": "0,0,960,1080",
        }))
        r = d.handle(_rpc("preset.list"))
        assert r["result"]["presets"][0]["name"] == "leftHalf"
        r = d.handle(_rpc("preset.delete", {"name": "leftHalf"}))
        assert r["result"]["deleted"] == "leftHalf"

    def test_create_invalid_region(self, tmp_config_dir):
        d, _, _ = build_dispatcher()
        r = d.handle(_rpc("preset.create", {
            "name": "bad", "region": "not,a,region",
        }))
        assert r["error"]["code"] == -32602

    def test_update_region(self, tmp_config_dir):
        d, _, _ = build_dispatcher()
        d.handle(_rpc("preset.create", {
            "name": "p", "region": "0,0,100,100",
        }))
        r = d.handle(_rpc("preset.update", {
            "name": "p", "region": "10,10,200,200",
        }))
        assert r["result"]["preset"]["region"] == "10,10,200,200"


class TestAIMethods:
    def test_ai_status_when_no_key(self, tmp_config_dir):
        d, _, _ = build_dispatcher()
        r = d.handle(_rpc("ai.status"))
        # Even without the SDK the call must succeed.
        assert "sdk_available" in r["result"]
        assert r["result"]["has_api_key"] is False


# ---------------------------------------------------------------------------
# serve() end-to-end via in-memory pipes
# ---------------------------------------------------------------------------


class TestServeMainLoop:
    def test_ping_then_shutdown(self, tmp_config_dir):
        stdin = io.StringIO(
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "app.ping"}) + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "app.shutdown"}) + "\n"
        )
        stdout = io.StringIO()
        serve(stdin=stdin, stdout=stdout)
        lines = [l for l in stdout.getvalue().split("\n") if l]
        assert len(lines) == 2
        r1 = json.loads(lines[0])
        r2 = json.loads(lines[1])
        assert r1["result"] == "pong"
        assert r2["result"] == "ok"

    def test_malformed_line_is_parse_error(self, tmp_config_dir):
        stdin = io.StringIO(
            "{not valid json}\n"
            + json.dumps({"jsonrpc": "2.0", "id": 1, "method": "app.shutdown"}) + "\n"
        )
        stdout = io.StringIO()
        serve(stdin=stdin, stdout=stdout)
        lines = [l for l in stdout.getvalue().split("\n") if l]
        assert len(lines) == 2
        err = json.loads(lines[0])
        assert err["error"]["code"] == -32700

    def test_notification_produces_no_output(self, tmp_config_dir):
        stdin = io.StringIO(
            # notification (no id) — no response expected
            json.dumps({"jsonrpc": "2.0", "method": "app.ping"}) + "\n"
            # actual request
            + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "app.shutdown"}) + "\n"
        )
        stdout = io.StringIO()
        serve(stdin=stdin, stdout=stdout)
        lines = [l for l in stdout.getvalue().split("\n") if l]
        assert len(lines) == 1  # only shutdown response
        assert json.loads(lines[0])["id"] == 9
