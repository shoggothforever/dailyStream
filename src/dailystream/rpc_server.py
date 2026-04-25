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
        # Background interval-capture workers keyed by "mode_id/preset_id".
        # Each entry is a dict {"thread": Thread, "stop": Event}.
        self._interval_workers: dict[str, dict] = {}
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

    def stop_all_interval_workers(self) -> None:
        """Signal all running interval-capture workers to stop and join."""
        for key, entry in list(self._interval_workers.items()):
            stop_event = entry.get("stop")
            thread = entry.get("thread")
            if stop_event is not None:
                stop_event.set()
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
            self._interval_workers.pop(key, None)

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
        state.stop_all_interval_workers()
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
    def _open(path: str, force: bool = False) -> dict:
        if state.wm.is_active:
            if force:
                # Auto-end the current workspace before opening a new one.
                state.wm.end(config=state.config,
                             analysis_queue=state.analysis_queue)
                state.shutdown_analysis_queue()
                d.event_bus.publish("workspace.changed", {"is_active": False})
            else:
                raise StateConflict(
                    "Workspace already active",
                    data={"workspace_dir": str(state.wm.workspace_dir)},
                )
        target = Path(path)
        # Accept either the workspace dir itself or a parent containing it
        if not (target / "workspace_meta.json").exists():
            raise NotFound(f"No workspace_meta.json in {target}")
        if not state.wm.load(target):
            raise NotFound(f"Failed to load workspace from {target}")
        # Re-activate the workspace (it may have been ended previously).
        # This mirrors the old rumps behaviour in app.py _on_open_workspace.
        if state.wm.meta and state.wm.meta.ended_at is not None:
            state.wm.meta.ended_at = None
            state.wm.save_meta()
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

    def _require_workspace_loaded() -> None:
        """Like _require_active_workspace but allows ended workspaces.
        The edit/delete operations work on disk files and don't need
        the workspace to be "active" (ended_at == None).
        """
        if state.wm.workspace_dir is None or state.pm is None:
            raise StateConflict("No workspace loaded")

    @d.method("feed.delete")
    def _delete(pipeline: str, entry_index: int,
                delete_file: bool = False) -> dict:
        """Delete an entry from a pipeline and regenerate stream.md."""
        _require_workspace_loaded()

        removed = state.pm.delete_entry(pipeline, entry_index)
        if removed is None:
            raise NotFound(f"Entry {entry_index} not found in {pipeline}")

        # Optionally delete the screenshot file
        if delete_file and removed.get("input_type") == "image":
            img_path = Path(removed.get("input_content", ""))
            if img_path.exists():
                try:
                    img_path.unlink()
                except Exception:  # noqa: BLE001
                    logger.warning("Failed to delete image: %s", img_path)

        # Regenerate stream.md from scratch
        _regenerate_stream_md(state)

        d.event_bus.publish("feed.entry_deleted", {
            "pipeline": pipeline,
            "entry_index": entry_index,
        })
        return {"deleted": True, "pipeline": pipeline,
                "entry_index": entry_index}

    @d.method("feed.update")
    def _update(pipeline: str, entry_index: int,
                description: str) -> dict:
        """Update an entry's description and regenerate stream.md."""
        _require_workspace_loaded()

        updated = state.pm.update_entry(pipeline, entry_index, description)
        if updated is None:
            raise NotFound(f"Entry {entry_index} not found in {pipeline}")

        # Regenerate stream.md
        _regenerate_stream_md(state)

        d.event_bus.publish("feed.entry_updated", {
            "pipeline": pipeline,
            "entry_index": entry_index,
            "entry": updated,
        })
        return {"updated": True, "entry": updated}


def _regenerate_stream_md(state: _ServerState) -> None:
    """Rebuild stream.md from all pipeline entries."""
    from .note_sync import LocalMarkdownSyncer

    assert state.wm.workspace_dir is not None
    assert state.wm.meta is not None
    assert state.pm is not None

    ws_title = state.wm.meta.title or state.wm.meta.workspace_id
    md_path = state.wm.workspace_dir / "stream.md"

    syncer = LocalMarkdownSyncer(state.wm.workspace_dir, state.config)
    # Delete and rebuild
    if md_path.exists():
        md_path.unlink()

    for pname in state.pm.list_pipelines():
        pmeta = state.pm.get_pipeline_meta(pname)
        for entry in state.pm.get_entries(pname):
            image_path = None
            if entry.get("input_type") == "image":
                image_path = entry.get("input_content")
            syncer.sync_entry(
                workspace_title=ws_title,
                pipeline_name=pname,
                timestamp=entry.get("timestamp", ""),
                input_type=entry.get("input_type", ""),
                description=entry.get("description", ""),
                content=entry.get("input_content", ""),
                image_path=image_path,
                pipeline_meta=pmeta,
            )


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
        """Return a structured JSON representation of the workspace
        timeline for the Swift Daily Review window.
        """
        _require_active_workspace(state)
        from .timeline import generate_structured

        data = generate_structured(
            state.wm.workspace_dir, state.wm.meta, config=state.config,
        )
        if data is None:
            return {"workspace": _meta_to_dict(state), "entries": [],
                    "stats": {}, "pipeline_summaries": [],
                    "daily_summary": None}
        return data


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


def _register_capture_modes_methods(d: Dispatcher, state: _ServerState) -> None:
    """capture_modes.* — Mode / Preset / Attachment Designer backend.

    These methods are the single source of truth for the Swift Designer
    UI and the HotkeyManager.  Handlers keep the in-memory
    ``state.config.capture_modes`` up to date, persist on every mutation,
    and broadcast events so the Swift side can react without polling.
    """
    import threading
    from .capture_modes import (
        ATTACHMENT_CATALOG,
        Attachment,
        CaptureExecutor,
        ExecutionContext,
        Mode,
        ModesState,
        Preset,
        catalog_as_list,
        validate_attachments,
    )

    def _state_dict() -> dict:
        cm = getattr(state.config, "capture_modes", None)
        if cm is None:
            return {"modes": [], "active_mode_id": None}
        return cm.to_dict()

    def _ensure_state() -> "ModesState":
        cm = getattr(state.config, "capture_modes", None)
        if cm is None:
            from .capture_modes import default_modes
            cm = default_modes()
            state.config.capture_modes = cm
            state.config.save()
        return cm

    def _persist(publish_event: bool = True) -> dict:
        state.config.save()
        payload = _state_dict()
        if publish_event:
            d.event_bus.publish("capture_modes.changed", payload)
        return payload

    # -- read methods ---------------------------------------------------

    @d.method("capture_modes.list_modes")
    def _list_modes() -> dict:
        _ensure_state()
        return _state_dict()

    @d.method("capture_modes.get_active")
    def _get_active() -> dict:
        cm = _ensure_state()
        active = cm.get_active()
        return {
            "active_mode_id": cm.active_mode_id,
            "mode": active.to_dict() if active else None,
        }

    @d.method("capture_modes.list_attachment_catalog")
    def _list_catalog() -> dict:
        return {"catalog": catalog_as_list()}

    # -- write methods --------------------------------------------------

    @d.method("capture_modes.switch_active_mode")
    def _switch_active(mode_id: str) -> dict:
        cm = _ensure_state()
        if not any(m.id == mode_id for m in cm.modes):
            raise NotFound(f"Unknown mode: {mode_id}")
        state.stop_all_interval_workers()
        cm.active_mode_id = mode_id
        _persist()
        return {"active_mode_id": mode_id}

    @d.method("capture_modes.save_mode")
    def _save_mode(mode: dict) -> dict:
        """Create or replace a Mode by id.

        The payload must match :meth:`Mode.to_dict`.  Attachment IDs are
        validated against the catalog; bad payloads raise ``InvalidParams``.
        """
        if not isinstance(mode, dict):
            raise InvalidParams("'mode' must be a JSON object")
        parsed = Mode.from_dict(mode)
        if parsed is None or not parsed.id:
            raise InvalidParams("Invalid mode payload")

        # Validate every preset's attachments.
        for p in parsed.presets:
            errs = validate_attachments(p.attachments)
            if errs:
                raise InvalidParams(
                    f"Preset '{p.name}' has invalid attachments: {'; '.join(errs)}"
                )

        cm = _ensure_state()
        replaced = False
        for idx, existing in enumerate(cm.modes):
            if existing.id == parsed.id:
                cm.modes[idx] = parsed
                replaced = True
                break
        if not replaced:
            cm.modes.append(parsed)

        # If the newly saved mode is the active one we restart interval
        # workers later via switch — for now just invalidate.
        if cm.active_mode_id is None:
            cm.active_mode_id = parsed.id

        state.stop_all_interval_workers()
        _persist()
        return {"mode": parsed.to_dict(), "created": not replaced}

    @d.method("capture_modes.delete_mode")
    def _delete_mode(mode_id: str) -> dict:
        cm = _ensure_state()
        idx = next((i for i, m in enumerate(cm.modes) if m.id == mode_id), -1)
        if idx < 0:
            raise NotFound(f"Unknown mode: {mode_id}")
        if len(cm.modes) == 1:
            raise StateConflict("Cannot delete the last remaining mode")
        removed = cm.modes.pop(idx)
        if cm.active_mode_id == mode_id:
            cm.active_mode_id = cm.modes[0].id
            state.stop_all_interval_workers()
        _persist()
        return {"deleted": removed.id, "active_mode_id": cm.active_mode_id}

    @d.method("capture_modes.save_preset")
    def _save_preset(mode_id: str, preset: dict) -> dict:
        """Insert or replace a single preset inside an existing Mode."""
        if not isinstance(preset, dict):
            raise InvalidParams("'preset' must be a JSON object")
        parsed = Preset.from_dict(preset)
        if parsed is None or not parsed.id:
            raise InvalidParams("Invalid preset payload")
        errs = validate_attachments(parsed.attachments)
        if errs:
            raise InvalidParams(f"Invalid attachments: {'; '.join(errs)}")
        cm = _ensure_state()
        mode = next((m for m in cm.modes if m.id == mode_id), None)
        if mode is None:
            raise NotFound(f"Unknown mode: {mode_id}")
        replaced = False
        for idx, existing in enumerate(mode.presets):
            if existing.id == parsed.id:
                mode.presets[idx] = parsed
                replaced = True
                break
        if not replaced:
            mode.presets.append(parsed)
        _persist()
        return {"preset": parsed.to_dict(), "created": not replaced}

    @d.method("capture_modes.delete_preset")
    def _delete_preset(mode_id: str, preset_id: str) -> dict:
        cm = _ensure_state()
        mode = next((m for m in cm.modes if m.id == mode_id), None)
        if mode is None:
            raise NotFound(f"Unknown mode: {mode_id}")
        idx = next(
            (i for i, p in enumerate(mode.presets) if p.id == preset_id),
            -1,
        )
        if idx < 0:
            raise NotFound(f"Unknown preset: {preset_id}")
        removed = mode.presets.pop(idx)
        _persist()
        return {"deleted": removed.id}

    # -- execution ------------------------------------------------------

    def _make_context(mode_id: str, silent: bool = False,
                      preset: Optional[Preset] = None) -> ExecutionContext:
        cm = _ensure_state()
        mode = next((m for m in cm.modes if m.id == mode_id), None)
        return ExecutionContext(
            wm=state.wm,
            pm=state.pm,
            publish_event=d.event_bus.publish,
            mode_id=mode_id,
            mode_name=mode.name if mode else "",
            preset_id=preset.id if preset else "",
            preset_name=preset.name if preset else "",
            config=state.config,
            silent=silent,
        )

    def _deliver_frames(report, preset: Preset) -> None:
        """Feed captured frames into the active pipeline (or not).

        Honours the ``current_pipeline`` DELIVERY attachment (default)
        vs. absence of any delivery attachment.  If ``silent_save`` is
        active we skip the HUD round-trip by pushing the feed directly;
        otherwise the Swift side will pick up
        ``capture.mode_preset_executed`` and prompt for descriptions.
        """
        has_delivery = any(
            a.id == "current_pipeline" for a in preset.attachments
        )
        if not has_delivery:
            return
        if not state.wm.is_active or state.pm is None:
            return
        pipeline = state.wm.get_active_pipeline()
        if not pipeline:
            return
        if not report.silent:
            # Non-silent path: Swift will open HUDs for each frame.
            return

        from dataclasses import asdict as _asdict
        from .note_sync import NoteSyncManager

        for frame in report.frames:
            if frame.path is None or frame.skipped:
                continue
            # Sanity-check the file is actually on disk before we commit
            # an entry pointing at it.  Silent path never had this check
            # historically, so it could produce "phantom" entries when
            # screencapture silently failed (macOS permission gotcha,
            # ESC-during-interactive, etc.).
            try:
                frame_path = Path(str(frame.path))
                if not frame_path.exists() or frame_path.stat().st_size == 0:
                    logger.warning(
                        "Silent delivery skipped: file missing or empty at %s",
                        frame.path,
                    )
                    continue
            except OSError:
                logger.exception("Silent delivery: cannot stat %s",
                                 frame.path)
                continue
            description = ""
            ocr = frame.post_artifacts.get("ocr_text")
            if ocr:
                description = str(ocr)[:400]
            try:
                entry = state.pm.add_entry(
                    pipeline, "image", str(frame.path), description,
                )
                entry_dict = _asdict(entry)
                syncer = NoteSyncManager(
                    state.config, workspace_dir=state.wm.workspace_dir,
                )
                pmeta = state.pm.get_pipeline_meta(pipeline)
                syncer.sync_entry(state.wm.meta, pipeline, entry,
                                  pipeline_meta=pmeta)
                entry_index = max(0, len(state.pm.get_entries(pipeline)) - 1)
                d.event_bus.publish("feed.entry_added", {
                    "pipeline": pipeline,
                    "entry_index": entry_index,
                    "entry": entry_dict,
                })
            except Exception:  # noqa: BLE001
                logger.exception("Silent delivery failed for %s", frame.path)

    @d.method("capture_modes.execute_preset")
    def _execute_preset(mode_id: str, preset_id: str,
                        silent: bool = False) -> dict:
        """Run a Preset end-to-end once (respects its strategy).

        For ``interval`` strategy this captures a single frame and
        returns — use ``capture_modes.start_interval`` to launch the
        background loop.
        """
        _require_active_workspace(state)
        cm = _ensure_state()
        preset = cm.find_preset(mode_id, preset_id)
        if preset is None:
            raise NotFound(f"Preset not found: {mode_id}/{preset_id}")
        ctx = _make_context(mode_id, silent=silent, preset=preset)
        executor = CaptureExecutor()
        report = executor.execute(preset, ctx)
        _deliver_frames(report, preset)
        payload = report.to_dict()
        d.event_bus.publish("capture.mode_preset_executed", payload)
        return payload

    # -- interval long-running tasks -----------------------------------

    def _interval_key(mode_id: str, preset_id: str) -> str:
        return f"{mode_id}/{preset_id}"

    @d.method("capture_modes.start_interval")
    def _start_interval(mode_id: str, preset_id: str) -> dict:
        _require_active_workspace(state)
        cm = _ensure_state()
        preset = cm.find_preset(mode_id, preset_id)
        if preset is None:
            raise NotFound(f"Preset not found: {mode_id}/{preset_id}")
        strategy = next(
            (a for a in preset.attachments if a.id == "interval"), None,
        )
        if strategy is None:
            raise InvalidParams("Preset has no 'interval' strategy")

        key = _interval_key(mode_id, preset_id)
        if key in state._interval_workers:
            raise StateConflict(f"Interval already running for {key}")

        seconds = max(1, int(strategy.params.get("seconds", 60)))
        max_count = int(strategy.params.get("max_count", 0))
        stop_event = threading.Event()

        def _loop() -> None:
            executor = CaptureExecutor()
            count = 0
            logger.info(
                "interval worker %s started (every %ss, max=%s)",
                key, seconds, max_count,
            )
            while not stop_event.is_set():
                try:
                    ctx = _make_context(mode_id, silent=True, preset=preset)
                    report = executor.execute(preset, ctx)
                    _deliver_frames(report, preset)
                    d.event_bus.publish("capture.mode_preset_executed",
                                        report.to_dict())
                except Exception:  # noqa: BLE001
                    logger.exception("interval worker %s frame failed", key)
                count += 1
                if max_count and count >= max_count:
                    break
                stop_event.wait(timeout=float(seconds))
            state._interval_workers.pop(key, None)
            d.event_bus.publish("capture_modes.interval_stopped", {
                "mode_id": mode_id,
                "preset_id": preset_id,
                "captured": count,
            })

        thread = threading.Thread(
            target=_loop, name=f"interval-{key}", daemon=True,
        )
        state._interval_workers[key] = {"thread": thread, "stop": stop_event}
        thread.start()
        d.event_bus.publish("capture_modes.interval_started", {
            "mode_id": mode_id,
            "preset_id": preset_id,
            "seconds": seconds,
            "max_count": max_count,
        })
        return {"running": True, "mode_id": mode_id,
                "preset_id": preset_id, "seconds": seconds}

    @d.method("capture_modes.stop_interval")
    def _stop_interval(mode_id: str, preset_id: str) -> dict:
        key = _interval_key(mode_id, preset_id)
        entry = state._interval_workers.get(key)
        if entry is None:
            return {"running": False}
        entry["stop"].set()
        th = entry.get("thread")
        if th is not None and th.is_alive():
            th.join(timeout=2.0)
        state._interval_workers.pop(key, None)
        return {"running": False, "mode_id": mode_id, "preset_id": preset_id}

    @d.method("capture_modes.list_running_intervals")
    def _list_running() -> dict:
        return {"running": [
            {"key": k, "alive": bool(v["thread"].is_alive())}
            for k, v in state._interval_workers.items()
        ]}

    # -- templates ------------------------------------------------------

    @d.method("capture_modes.list_templates")
    def _list_templates() -> dict:
        from .capture_modes.templates import list_templates
        return {"templates": [t.to_dict() for t in list_templates()]}

    @d.method("capture_modes.install_template")
    def _install_template(
        template_id: Optional[str] = None,
        template: Optional[dict] = None,
        replace_existing: bool = False,
        mode_id_override: Optional[str] = None,
    ) -> dict:
        """Install a template as a Mode.

        Either ``template_id`` (lookup from built-in + user library) or
        ``template`` (raw ModeTemplate JSON, for remote / pasted
        imports) must be supplied.

        ``replace_existing=False`` (default) → if a Mode with the same
        id already exists, the new one is suffixed with ``-1`` / ``-2``
        etc. so user customisations aren't clobbered.

        ``mode_id_override`` lets the UI rename on install (nice for
        letting users pick their own slug).
        """
        from .capture_modes.templates import ModeTemplate, get_template

        tpl: Optional[ModeTemplate]
        if template_id is not None:
            tpl = get_template(template_id)
            if tpl is None:
                raise NotFound(f"Unknown template: {template_id}")
        elif template is not None:
            tpl = ModeTemplate.from_dict(template)
            if tpl is None:
                raise InvalidParams("Invalid template payload")
        else:
            raise InvalidParams(
                "Provide either 'template_id' or 'template'")

        cm = _ensure_state()
        mode_payload = dict(tpl.mode)
        new_id = mode_id_override or mode_payload.get("id") or tpl.template_id

        if not replace_existing:
            existing_ids = {m.id for m in cm.modes}
            base = new_id
            suffix = 1
            while new_id in existing_ids:
                new_id = f"{base}-{suffix}"
                suffix += 1
        mode_payload["id"] = new_id

        parsed = Mode.from_dict(mode_payload)
        if parsed is None:
            raise InvalidParams("Template produced an invalid Mode payload")

        # Validate every preset's attachments against the current catalog.
        for p in parsed.presets:
            errs = validate_attachments(p.attachments)
            if errs:
                raise InvalidParams(
                    f"Preset '{p.name}' has invalid attachments: "
                    f"{'; '.join(errs)}"
                )

        # Replace or append.
        replaced = False
        for i, existing in enumerate(cm.modes):
            if existing.id == parsed.id:
                cm.modes[i] = parsed
                replaced = True
                break
        if not replaced:
            cm.modes.append(parsed)
        if cm.active_mode_id is None:
            cm.active_mode_id = parsed.id

        state.stop_all_interval_workers()
        _persist()
        return {
            "mode_id": parsed.id,
            "template_id": tpl.template_id,
            "replaced": replaced,
        }

    @d.method("capture_modes.export_mode")
    def _export_mode(
        mode_id: str,
        title: Optional[str] = None,
        description: str = "",
        author: str = "user",
    ) -> dict:
        """Wrap an existing Mode into a ModeTemplate payload.

        Returned payload is ready to be written to disk / shared.  The
        client can persist it locally via :py:meth:`save_user_template`
        or drop it on a gist for others to import.
        """
        from .capture_modes.templates import export_mode_as_template

        cm = _ensure_state()
        mode = next((m for m in cm.modes if m.id == mode_id), None)
        if mode is None:
            raise NotFound(f"Unknown mode: {mode_id}")
        tpl = export_mode_as_template(
            mode, title=title, description=description, author=author,
        )
        return {"template": tpl.to_dict()}

    @d.method("capture_modes.save_user_template")
    def _save_user_template(template: dict) -> dict:
        """Persist a template JSON under ``~/.dailystream/templates/``.

        Used by the Designer's "Save as Template…" action.  Returns the
        file path so the UI can reveal it in Finder.
        """
        from .capture_modes.templates import (
            ModeTemplate,
            save_user_template,
        )
        tpl = ModeTemplate.from_dict(template)
        if tpl is None:
            raise InvalidParams("Invalid template payload")
        path = save_user_template(tpl)
        return {"path": str(path), "template_id": tpl.template_id}


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
    _register_capture_modes_methods(d, state)
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

    state.stop_all_interval_workers()
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
