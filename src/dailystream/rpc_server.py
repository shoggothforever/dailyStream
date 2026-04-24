"""JSON-RPC 2.0 stdio server for DailyStream.

This module is the single entry point for the Swift UI shell.  It reads
newline-delimited JSON-RPC messages from ``stdin`` and writes responses /
notifications to ``stdout``.

Critical contract
-----------------
* ``stdout`` **only** carries RPC messages — any stray ``print`` call
  would corrupt the protocol.  All logging goes to ``stderr`` / a file.
* Python buffering is disabled on ``stdout`` (line-buffered) so Swift
  never waits forever for a response.
* One background consumer thread runs the dispatcher; writes are
  serialised through ``_write_lock`` so event notifications and
  responses cannot interleave mid-line.

Usage
-----
From the command line::

    python -m dailystream.rpc_server

From code (e.g. tests)::

    from dailystream.rpc_server import serve
    serve(stdin=..., stdout=..., stderr=...)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import IO, Any, Optional

from .config import (
    Config,
    DEFAULT_WORKSPACE_ROOT,
    get_active_workspace_path,
    read_json,
    set_active_workspace_path,
)
from .rpc_dispatcher import (
    Dispatcher,
    InvalidParams,
    NotFound,
    RPCError,
    StateConflict,
)
from .rpc_events import EventBus

logger = logging.getLogger(__name__)

__version__ = "0.3.0-dev"  # bumped when RPC protocol evolves


# ---------------------------------------------------------------------------
# Logging setup — stderr + rotating file, never stdout.
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    """Send logs to stderr + ``~/Library/Logs/DailyStream/core.log``.

    Safe to call multiple times — repeated calls are no-ops.
    """
    root = logging.getLogger()
    if getattr(root, "_dailystream_configured", False):
        return

    # stderr
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    root.addHandler(sh)

    # file (best-effort; tests may run without write access)
    try:
        log_dir = Path.home() / "Library" / "Logs" / "DailyStream"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "core.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))
        root.addHandler(fh)
    except Exception:  # noqa: BLE001
        pass

    root.setLevel(logging.INFO)
    root._dailystream_configured = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Server-wide shared state
# ---------------------------------------------------------------------------


class _ServerState:
    """Long-lived state shared by every handler for one ``serve()`` call.

    Each ``serve()`` invocation builds its own ``_ServerState``; tests
    can therefore spin up multiple independent servers in one process
    without cross-talk.
    """

    def __init__(self) -> None:
        # Lazily-imported heavy modules live in ``self._ctx`` to keep
        # import costs low during module load (e.g. unit tests that
        # never touch these modules).
        from .workspace import WorkspaceManager  # local import

        self.wm = WorkspaceManager()
        self.config = Config.load()
        # AnalysisQueue is created on-demand — realtime mode only.
        self._analysis_queue = None
        # Pipeline manager is built lazily and rebuilt whenever the
        # active workspace changes (via _refresh_pipeline_manager).
        self._pm = None
        if self.wm.is_active and self.wm.workspace_dir is not None:
            self._refresh_pipeline_manager()
            if (self.wm.meta and
                    getattr(self.wm.meta, "ai_mode", "off") == "realtime"):
                self._init_analysis_queue()

    # -- helpers ---------------------------------------------------------

    def _refresh_pipeline_manager(self) -> None:
        from .pipeline import PipelineManager

        assert self.wm.workspace_dir is not None
        self._pm = PipelineManager(
            self.wm.workspace_dir,
            screenshot_save_path=self.config.screenshot_save_path,
        )

    def _init_analysis_queue(self) -> None:
        """Spin up an AnalysisQueue iff realtime mode is active."""
        if self._analysis_queue is not None:
            return
        try:
            from .ai_analyzer import AnalysisQueue
        except ImportError:
            return
        if self.wm.workspace_dir is None:
            return
        self._analysis_queue = AnalysisQueue(
            self.config, self.wm.workspace_dir
        )

    def shutdown_analysis_queue(self) -> None:
        if self._analysis_queue is not None:
            try:
                self._analysis_queue.shutdown()
            except Exception:  # noqa: BLE001
                logger.exception("Failed to shutdown analysis queue")
            self._analysis_queue = None

    @property
    def pm(self):
        """Return the current ``PipelineManager``; None if no workspace."""
        if self.wm.workspace_dir is None:
            return None
        if self._pm is None:
            self._refresh_pipeline_manager()
        return self._pm

    @property
    def analysis_queue(self):
        return self._analysis_queue


# ---------------------------------------------------------------------------
# Handler registration — each namespace lives in its own function.
# ---------------------------------------------------------------------------


def _require_active_workspace(state: _ServerState) -> None:
    if not state.wm.is_active:
        raise StateConflict("No active workspace")


def _meta_to_dict(state: _ServerState) -> dict:
    """Serialise workspace metadata into a plain dict suitable for JSON."""
    m = state.wm.meta
    if m is None:
        return {"is_active": False}
    from dataclasses import asdict
    d = asdict(m)
    d["is_active"] = state.wm.is_active
    d["workspace_dir"] = str(state.wm.workspace_dir) if state.wm.workspace_dir else None
    return d


def _register_app_methods(d: Dispatcher, state: _ServerState,
                          shutdown_flag: threading.Event) -> None:
    """Lifecycle methods: ping / version / shutdown."""

    @d.method("app.ping")
    def _ping() -> str:
        return "pong"

    @d.method("app.version")
    def _version() -> dict:
        return {
            "rpc_version": __version__,
            "python_version": sys.version.split()[0],
        }

    @d.method("app.shutdown")
    def _shutdown() -> str:
        shutdown_flag.set()
        state.shutdown_analysis_queue()
        return "ok"


def _register_workspace_methods(d: Dispatcher, state: _ServerState) -> None:

    @d.method("workspace.create")
    def _create(
        path: Optional[str] = None,
        title: Optional[str] = None,
        ai_mode: str = "off",
    ) -> dict:
        if state.wm.is_active:
            raise StateConflict(
                "Workspace already active",
                data={"workspace_dir": str(state.wm.workspace_dir)},
            )
        if ai_mode not in ("off", "realtime", "daily_report"):
            raise InvalidParams(f"Unknown ai_mode: {ai_mode}")
        base = Path(path) if path else DEFAULT_WORKSPACE_ROOT
        ws_dir = state.wm.create(base_path=base, title=title, ai_mode=ai_mode)
        state._refresh_pipeline_manager()
        if ai_mode == "realtime":
            state._init_analysis_queue()
        state.wm.event_bus = d.event_bus  # hint for future wiring
        d.event_bus.publish("workspace.changed", _meta_to_dict(state))
        return {
            "workspace_dir": str(ws_dir),
            "workspace_id": state.wm.meta.workspace_id,
            "ai_mode": ai_mode,
        }

    @d.method("workspace.open")
    def _open(path: str) -> dict:
        if state.wm.is_active:
            raise StateConflict("Workspace already active")
        target = Path(path)
        # Accept either the workspace dir itself or a parent containing it
        if not (target / "workspace_meta.json").exists():
            raise NotFound(f"No workspace_meta.json in {target}")
        if not state.wm.load(target):
            raise NotFound(f"Failed to load workspace from {target}")
        set_active_workspace_path(target)
        state._refresh_pipeline_manager()
        if state.wm.meta and getattr(state.wm.meta, "ai_mode", "off") == "realtime":
            state._init_analysis_queue()
        d.event_bus.publish("workspace.changed", _meta_to_dict(state))
        return _meta_to_dict(state)

    @d.method("workspace.end")
    def _end() -> dict:
        _require_active_workspace(state)
        report = state.wm.end(
            config=state.config,
            analysis_queue=state.analysis_queue,
        )
        state.shutdown_analysis_queue()
        d.event_bus.publish("workspace.changed", {"is_active": False})
        return {"timeline_report": report}

    @d.method("workspace.status")
    def _status() -> dict:
        info = _meta_to_dict(state)
        if state.wm.is_active and state.pm is not None:
            active = state.wm.get_active_pipeline()
            if active:
                info["active_pipeline_entry_count"] = len(
                    state.pm.get_entries(active)
                )
        return info

    @d.method("workspace.list_recent")
    def _list_recent(limit: int = 10) -> list:
        root = DEFAULT_WORKSPACE_ROOT
        if not root.exists():
            return []
        results: list[dict] = []
        # Layout: <root>/<yymmdd>/<name>/workspace_meta.json
        for date_dir in sorted(root.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            for ws_dir in sorted(date_dir.iterdir(), reverse=True):
                meta_file = ws_dir / "workspace_meta.json"
                if not meta_file.exists():
                    continue
                try:
                    meta = read_json(meta_file)
                except Exception:  # noqa: BLE001
                    continue
                results.append({
                    "workspace_id": meta.get("workspace_id", ""),
                    "title": meta.get("title") or meta.get("workspace_id", ""),
                    "workspace_path": str(ws_dir),
                    "created_at": meta.get("created_at", ""),
                    "ended_at": meta.get("ended_at"),
                    "ai_mode": meta.get("ai_mode", "off"),
                })
                if len(results) >= limit:
                    return results
        return results


def _register_pipeline_methods(d: Dispatcher, state: _ServerState) -> None:

    @d.method("pipeline.create")
    def _create(name: str, description: str = "", goal: str = "") -> dict:
        _require_active_workspace(state)
        assert state.pm is not None
        pipeline_dir = state.pm.create(name, description=description, goal=goal)
        state.wm.add_pipeline(name)
        # Activate the freshly created pipeline (matches CLI behaviour).
        state.wm.activate_pipeline(name)
        d.event_bus.publish("workspace.changed", _meta_to_dict(state))
        return {
            "name": name,
            "pipeline_dir": str(pipeline_dir),
            "active": True,
        }

    @d.method("pipeline.switch")
    def _switch(name: str) -> dict:
        _require_active_workspace(state)
        if not state.wm.activate_pipeline(name):
            raise NotFound(f"Pipeline not found: {name}")
        d.event_bus.publish("workspace.changed", _meta_to_dict(state))
        return {"active": name}

    @d.method("pipeline.list")
    def _list() -> dict:
        _require_active_workspace(state)
        assert state.pm is not None
        return {
            "pipelines": state.pm.list_pipelines(),
            "active": state.wm.get_active_pipeline(),
        }

    @d.method("pipeline.rename")
    def _rename(old: str, new: str) -> dict:
        _require_active_workspace(state)
        assert state.pm is not None
        import shutil

        if not new or not new.strip():
            raise InvalidParams("New name must not be empty")
        pipelines_dir = state.wm.workspace_dir / "pipelines"
        src = pipelines_dir / old
        dst = pipelines_dir / new
        if not src.exists():
            raise NotFound(f"Pipeline not found: {old}")
        if dst.exists():
            raise StateConflict(f"Target pipeline already exists: {new}")
        shutil.move(str(src), str(dst))
        # Update context.json name
        ctx_path = dst / "context.json"
        if ctx_path.exists():
            try:
                data = read_json(ctx_path)
                data["name"] = new
                from .config import write_json
                write_json(ctx_path, data)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to rewrite context.json on rename")
        # Update workspace meta
        if state.wm.meta:
            state.wm.meta.pipelines = [
                new if p == old else p for p in state.wm.meta.pipelines
            ]
            if state.wm.meta.active_pipeline == old:
                state.wm.meta.active_pipeline = new
            state.wm.save_meta()
        d.event_bus.publish("workspace.changed", _meta_to_dict(state))
        return {"old": old, "new": new}

    @d.method("pipeline.delete")
    def _delete(name: str) -> dict:
        _require_active_workspace(state)
        assert state.pm is not None
        import shutil

        pipelines_dir = state.wm.workspace_dir / "pipelines"
        target = pipelines_dir / name
        if not target.exists():
            raise NotFound(f"Pipeline not found: {name}")
        shutil.rmtree(target)
        if state.wm.meta:
            state.wm.meta.pipelines = [
                p for p in state.wm.meta.pipelines if p != name
            ]
            if state.wm.meta.active_pipeline == name:
                state.wm.meta.active_pipeline = (
                    state.wm.meta.pipelines[0]
                    if state.wm.meta.pipelines else None
                )
            state.wm.save_meta()
        d.event_bus.publish("workspace.changed", _meta_to_dict(state))
        return {"deleted": name}


def _register_capture_methods(d: Dispatcher, state: _ServerState) -> None:

    @d.method("capture.screenshot")
    def _screenshot(mode: str = "interactive",
                    region: Optional[str] = None) -> dict:
        _require_active_workspace(state)
        assert state.pm is not None
        from .capture import take_screenshot

        save_dir = state.pm.get_screenshots_dir()
        path = take_screenshot(save_dir, mode=mode, region=region)
        if path is None:
            raise StateConflict("Screenshot cancelled or failed")
        return {"path": str(path)}

    @d.method("capture.select_region")
    def _select_region() -> dict:
        """Open the drag-to-select overlay and return the region string
        (``"x,y,w,h"``) without taking an actual screenshot.

        This powers the "Create preset" flow where we only want the
        coordinates.  ``None`` from the underlying PyObjC helper means
        the user pressed Esc — we surface it as StateConflict so the
        Swift side can treat it as silent cancel (same code path as
        screenshot cancel).
        """
        from .capture import capture_screen_region

        region = capture_screen_region()
        if not region:
            raise StateConflict("Region selection cancelled")
        return {"region": region}

    @d.method("capture.clipboard.grab")
    def _clipboard_grab() -> dict:
        from .capture import grab_clipboard

        content, kind = grab_clipboard()
        return {"content": content, "type": kind}

    @d.method("capture.clipboard.save_image")
    def _clipboard_save_image() -> dict:
        _require_active_workspace(state)
        assert state.pm is not None
        from .capture import save_clipboard_image

        save_dir = state.pm.get_screenshots_dir()
        path = save_clipboard_image(save_dir)
        if path is None:
            raise NotFound("No image in clipboard")
        return {"path": str(path)}


def _register_feed_methods(d: Dispatcher, state: _ServerState) -> None:
    """feed.* methods mirror the CLI ``feed`` command.

    Each feed operation: add_entry → sync_entry → (optional) realtime AI.
    """

    def _do_feed(input_type: str, content: str,
                 description: str = "",
                 pipeline: Optional[str] = None) -> dict:
        _require_active_workspace(state)
        assert state.pm is not None
        from dataclasses import asdict as _asdict
        from .note_sync import NoteSyncManager

        pipeline_name = pipeline or state.wm.get_active_pipeline()
        if not pipeline_name:
            raise StateConflict("No active pipeline")

        entry = state.pm.add_entry(pipeline_name, input_type,
                                   content, description)
        entry_dict = _asdict(entry)

        # sync to stream.md / Obsidian / etc.
        try:
            syncer = NoteSyncManager(state.config, workspace_dir=state.wm.workspace_dir)
            pmeta = state.pm.get_pipeline_meta(pipeline_name)
            syncer.sync_entry(state.wm.meta, pipeline_name, entry,
                              pipeline_meta=pmeta)
        except Exception:  # noqa: BLE001
            logger.exception("sync_entry failed")

        entry_index = max(0, len(state.pm.get_entries(pipeline_name)) - 1)

        # realtime AI analysis
        ai_mode = getattr(state.wm.meta, "ai_mode", "off") or "off"
        if ai_mode == "realtime" and input_type in ("image", "url"):
            if state.analysis_queue is not None:
                state.analysis_queue.enqueue(
                    pipeline_name, entry_index, entry_dict
                )

        d.event_bus.publish("feed.entry_added", {
            "pipeline": pipeline_name,
            "entry_index": entry_index,
            "entry": entry_dict,
        })
        return {
            "pipeline": pipeline_name,
            "entry_index": entry_index,
            "entry": entry_dict,
        }

    @d.method("feed.text")
    def _text(content: str, description: str = "",
              pipeline: Optional[str] = None) -> dict:
        return _do_feed("text", content, description, pipeline)

    @d.method("feed.url")
    def _url(content: str, description: str = "",
             pipeline: Optional[str] = None) -> dict:
        return _do_feed("url", content, description, pipeline)

    @d.method("feed.image")
    def _image(path: str, description: str = "",
               pipeline: Optional[str] = None) -> dict:
        if not Path(path).exists():
            raise NotFound(f"Image not found: {path}")
        return _do_feed("image", path, description, pipeline)


def _register_timeline_methods(d: Dispatcher, state: _ServerState) -> None:

    @d.method("timeline.generate")
    def _generate() -> dict:
        _require_active_workspace(state)
        from .timeline import generate_timeline

        report = generate_timeline(
            state.wm.workspace_dir, state.wm.meta, config=state.config,
        )
        return {"path": str(report) if report else None}

    @d.method("timeline.export_structured")
    def _export_structured() -> dict:
        """Placeholder for Daily Review (M4). Returns a minimal structure.

        Real implementation will extend timeline.py with
        ``generate_structured()`` returning full JSON.
        """
        _require_active_workspace(state)
        assert state.pm is not None
        entries = state.pm.get_all_entries()
        return {
            "workspace": _meta_to_dict(state),
            "entries": entries,
            "generated_by": "rpc_server placeholder v0",
        }


def _register_ai_methods(d: Dispatcher, state: _ServerState) -> None:

    @d.method("ai.status")
    def _status() -> dict:
        from .ai_analyzer import _ANTHROPIC_AVAILABLE, get_ai_api_key

        key = get_ai_api_key(state.config)
        return {
            "sdk_available": _ANTHROPIC_AVAILABLE,
            "has_api_key": bool(key),
            "model": state.config.ai_model,
        }

    @d.method("ai.analyze_entry")
    def _analyze_entry(pipeline: str, entry_index: int,
                       force: bool = False) -> dict:
        _require_active_workspace(state)
        assert state.pm is not None
        from dataclasses import asdict as _asdict
        from .ai_analyzer import (
            AnalysisResult,
            AnalysisStore,
            ImageAnalyzer,
        )

        entries = state.pm.get_entries(pipeline)
        if not (0 <= entry_index < len(entries)):
            raise NotFound(f"entry_index {entry_index} out of range")
        entry = entries[entry_index]

        store_path = state.pm.get_ai_analyses_path(pipeline)
        store = AnalysisStore(store_path, pipeline_name=pipeline)
        ts = entry.get("timestamp", "")
        if not force and store.has_analysis(entry_index, ts):
            existing = store.get_by_entry_index(entry_index)
            return {"status": "skipped", "result": existing}

        analyzer = ImageAnalyzer(state.config)
        if not analyzer.available:
            raise StateConflict("AI analyzer not available")

        itype = entry.get("input_type", "")
        content = entry.get("input_content", "")
        hint = entry.get("description", "")
        result = None
        if itype == "image" and Path(content).exists():
            result = analyzer.analyze_image(Path(content), user_hint=hint)
        elif itype == "url":
            result = analyzer.analyze_url(content, user_hint=hint)
        else:
            raise InvalidParams(
                f"Entry type '{itype}' is not analysable"
            )

        if result is None:
            result = AnalysisResult.failed(
                entry_index, ts, itype, "Analysis returned None"
            )
        else:
            result.entry_index = entry_index
            result.timestamp = ts
        store.set_model(analyzer._model)
        store.append(result)
        return {"status": "ok", "result": _asdict(result)}

    @d.method("ai.batch_analyze")
    def _batch_analyze() -> dict:
        _require_active_workspace(state)
        from .ai_analyzer import batch_analyze_workspace

        produced = batch_analyze_workspace(
            state.config, state.wm.workspace_dir, state.wm.meta
        )
        return {"produced_new": bool(produced)}


def _register_config_methods(d: Dispatcher, state: _ServerState) -> None:
    from dataclasses import asdict, fields

    @d.method("config.get")
    def _get(key: Optional[str] = None) -> Any:
        data = asdict(state.config)
        if key is None:
            # hide API key unless explicitly requested via env
            data.pop("ai_api_key", None)
            return data
        if not hasattr(state.config, key):
            raise NotFound(f"Unknown config key: {key}")
        return {"key": key, "value": getattr(state.config, key)}

    @d.method("config.set")
    def _set(key: str, value: Any) -> dict:
        if not hasattr(state.config, key):
            raise NotFound(f"Unknown config key: {key}")
        setattr(state.config, key, value)
        state.config.save()
        return {"key": key, "value": value}

    @d.method("config.get_schema")
    def _schema() -> dict:
        return {
            "fields": [
                {
                    "name": f.name,
                    "type": str(f.type),
                    "default": (
                        f.default
                        if f.default is not None and not callable(f.default)
                        else None
                    ),
                }
                for f in fields(state.config)
                if f.name != "ai_api_key"  # never leak key schema details
            ]
        }


def _register_preset_methods(d: Dispatcher, state: _ServerState) -> None:

    @d.method("preset.list")
    def _list() -> dict:
        return {"presets": state.config.screenshot_presets or []}

    @d.method("preset.create")
    def _create(name: str, region: str,
                hotkey: Optional[str] = None) -> dict:
        parts = region.split(",")
        if len(parts) != 4:
            raise InvalidParams("region must be 'x,y,w,h'")
        try:
            [int(p) for p in parts]
        except ValueError:
            raise InvalidParams("region values must be integers")
        if state.config.screenshot_presets is None:
            state.config.screenshot_presets = []
        entry: dict = {"name": name, "region": region}
        if hotkey:
            entry["hotkey"] = hotkey
        state.config.screenshot_presets.append(entry)
        state.config.save()
        return {"preset": entry}

    @d.method("preset.delete")
    def _delete(name: str) -> dict:
        presets = state.config.screenshot_presets or []
        new_list = [p for p in presets if p.get("name") != name]
        if len(new_list) == len(presets):
            raise NotFound(f"Preset not found: {name}")
        state.config.screenshot_presets = new_list or None
        state.config.save()
        return {"deleted": name}

    @d.method("preset.update")
    def _update(name: str,
                region: Optional[str] = None,
                hotkey: Optional[str] = None,
                new_name: Optional[str] = None) -> dict:
        presets = state.config.screenshot_presets or []
        found = None
        for p in presets:
            if p.get("name") == name:
                found = p
                break
        if found is None:
            raise NotFound(f"Preset not found: {name}")
        if region is not None:
            parts = region.split(",")
            if len(parts) != 4:
                raise InvalidParams("region must be 'x,y,w,h'")
            found["region"] = region
        if hotkey is not None:
            if hotkey:
                found["hotkey"] = hotkey
            else:
                found.pop("hotkey", None)
        if new_name is not None:
            found["name"] = new_name
        state.config.save()
        return {"preset": found}


# ---------------------------------------------------------------------------
# Server-level assembly
# ---------------------------------------------------------------------------


def build_dispatcher(
    state: Optional[_ServerState] = None,
    shutdown_flag: Optional[threading.Event] = None,
) -> tuple[Dispatcher, _ServerState, threading.Event]:
    """Build a fully-wired dispatcher.

    Exposed for tests — the main ``serve()`` function uses this under the
    hood.  Pass ``state=...`` to inject a mock / pre-populated state.
    """
    if state is None:
        state = _ServerState()
    if shutdown_flag is None:
        shutdown_flag = threading.Event()
    d = Dispatcher()
    _register_app_methods(d, state, shutdown_flag)
    _register_workspace_methods(d, state)
    _register_pipeline_methods(d, state)
    _register_capture_methods(d, state)
    _register_feed_methods(d, state)
    _register_timeline_methods(d, state)
    _register_ai_methods(d, state)
    _register_config_methods(d, state)
    _register_preset_methods(d, state)
    return d, state, shutdown_flag


# ---------------------------------------------------------------------------
# Main stdio loop
# ---------------------------------------------------------------------------


def serve(
    stdin: Optional[IO[str]] = None,
    stdout: Optional[IO[str]] = None,
    stderr: Optional[IO[str]] = None,
) -> None:
    """Run the newline-delimited JSON-RPC server on stdio.

    Blocks until stdin closes or an ``app.shutdown`` request arrives.
    """
    _configure_logging()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    # stderr is only used for logging, which we've already routed.

    # Force line-buffered stdout — Swift reads by line.
    try:
        stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    write_lock = threading.Lock()
    shutdown_flag = threading.Event()

    dispatcher, state, shutdown_flag = build_dispatcher(
        shutdown_flag=shutdown_flag
    )

    def _write(payload: dict) -> None:
        line = json.dumps(payload, ensure_ascii=False)
        with write_lock:
            stdout.write(line + "\n")
            try:
                stdout.flush()
            except Exception:  # noqa: BLE001
                pass

    # Wire every published event into a JSON-RPC notification on stdout.
    def _forward_event(method: str, params: dict) -> None:
        _write({"jsonrpc": "2.0", "method": method, "params": params})

    dispatcher.event_bus.subscribe(_forward_event)

    logger.info("dailystream-core RPC server v%s starting", __version__)

    for raw in stdin:
        if shutdown_flag.is_set():
            break
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _write({
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Parse error: invalid JSON line",
                },
            })
            continue

        response = dispatcher.handle(request)
        if response is not None:
            _write(response)
        if shutdown_flag.is_set():
            break

    state.shutdown_analysis_queue()
    logger.info("dailystream-core RPC server stopped")


def main() -> int:
    """Console-script entry point: ``dailystream-core``."""
    try:
        serve()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:  # noqa: BLE001
        logger.exception("Fatal error in RPC server")
        return 1


if __name__ == "__main__":
    sys.exit(main())
