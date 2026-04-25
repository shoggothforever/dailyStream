"""Pipeline executor — turns a Preset into a sequence of actions.

The executor walks a :class:`~.models.Preset` in a fixed order::

    window_ctrl (pre)
        └── source (acquire image/content)
              └── strategy (single / burst / interval / hold)
                    └── post (per captured frame)
                          └── delivery (per frame)
    window_ctrl (post — cleanup/restore)

It is intentionally **decoupled from the RPC layer**: the RPC server
instantiates an :class:`ExecutionContext` carrying the current
``WorkspaceManager``, ``PipelineManager`` and a ``publish_event``
callback, then calls :meth:`CaptureExecutor.execute`.  The executor
returns an :class:`ExecutionReport` describing each frame so callers can
emit the usual ``capture.mode_preset_executed`` notification.

The executor is **synchronous**: blocking for the duration of the whole
recipe.  For ``interval`` strategies the caller is expected to wrap the
call in a background thread or to use the dedicated RPC methods
(``capture_modes.start_interval`` / ``capture_modes.stop_interval``)
implemented elsewhere.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from .catalog import ATTACHMENT_CATALOG
from .models import (
    Attachment,
    AttachmentKind,
    Preset,
    Source,
    SourceKind,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Context + report shapes
# ---------------------------------------------------------------------------


class PipelineLike(Protocol):
    """Minimal interface the executor needs from ``PipelineManager``."""

    def get_screenshots_dir(self) -> Path: ...


class WorkspaceLike(Protocol):
    """Minimal interface the executor needs from ``WorkspaceManager``."""

    @property
    def is_active(self) -> bool: ...
    def get_active_pipeline(self) -> Optional[str]: ...


EventPublisher = Callable[[str, dict[str, Any]], None]


@dataclass
class ExecutionContext:
    """Side-channel objects a Preset execution needs.

    The RPC server builds one of these per invocation and passes it to
    :meth:`CaptureExecutor.execute`.
    """

    wm: WorkspaceLike
    pm: Optional[PipelineLike]
    publish_event: EventPublisher
    mode_id: str = ""
    mode_name: str = ""
    preset_id: str = ""
    preset_name: str = ""
    # Optional config reference — needed by Attachments that reach into
    # the AI layer (e.g. ``ai_analyze``).
    config: Optional[Any] = None
    # Optional pre-typed description; used as ``DAILYSTREAM_DESCRIPTION``
    # when running hooks in silent mode.  Non-silent mode leaves this
    # empty and the Swift HUD collects one from the user.
    description: str = ""
    # When True, skip the HUD / description prompt even if ``silent_save``
    # attachment is absent — used by RPC callers that handle HUDs
    # themselves.
    silent: bool = False
    # Explicit override for the pipeline that should receive the feed.
    # None → ``wm.get_active_pipeline()``.
    target_pipeline: Optional[str] = None


@dataclass
class FrameResult:
    """Record of a single captured frame."""

    path: Optional[Path]
    index: int                 # zero-based within this execution
    source_kind: str
    skipped: bool = False
    error: Optional[str] = None
    post_artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path) if self.path else None,
            "index": self.index,
            "source_kind": self.source_kind,
            "skipped": self.skipped,
            "error": self.error,
            "post_artifacts": dict(self.post_artifacts),
        }


@dataclass
class ExecutionReport:
    """Full summary of a Preset execution."""

    mode_id: str
    preset_id: str
    preset_name: str
    frames: list[FrameResult] = field(default_factory=list)
    cancelled: bool = False
    error: Optional[str] = None
    silent: bool = False

    def to_dict(self) -> dict:
        return {
            "mode_id": self.mode_id,
            "preset_id": self.preset_id,
            "preset_name": self.preset_name,
            "silent": self.silent,
            "cancelled": self.cancelled,
            "error": self.error,
            "frames": [f.to_dict() for f in self.frames],
        }


# ---------------------------------------------------------------------------
# Attachment handlers
# ---------------------------------------------------------------------------


@dataclass
class _AttGroups:
    """Attachments bucketed by kind for quick lookup inside the executor."""

    strategy: Optional[Attachment] = None
    delivery: Optional[Attachment] = None
    feedback: list[Attachment] = field(default_factory=list)
    window_ctrl: list[Attachment] = field(default_factory=list)
    post: list[Attachment] = field(default_factory=list)


def _group_attachments(atts: list[Attachment]) -> _AttGroups:
    groups = _AttGroups()
    for att in atts:
        spec = ATTACHMENT_CATALOG.get(att.id)
        if spec is None:
            logger.warning("Unknown attachment id in preset: %s", att.id)
            continue
        if spec.kind == AttachmentKind.STRATEGY:
            if groups.strategy is None:
                groups.strategy = att
        elif spec.kind == AttachmentKind.DELIVERY:
            if groups.delivery is None:
                groups.delivery = att
        elif spec.kind == AttachmentKind.FEEDBACK:
            groups.feedback.append(att)
        elif spec.kind == AttachmentKind.WINDOW_CTRL:
            groups.window_ctrl.append(att)
        elif spec.kind == AttachmentKind.POST:
            groups.post.append(att)

    # Stable-sort POST so data-producing attachments run before
    # data-consuming ones.  This lets users wire up "AI → shell script"
    # pipelines without caring about drag order in the Designer.
    #
    # Order: producers (ocr, ai_analyze, quick_tags, auto_copy_clipboard)
    # → consumer-side hooks (run_command) → archivers (nothing else today).
    _POST_ORDER = {
        "auto_ocr": 0,
        "ai_analyze": 1,
        "quick_tags": 2,
        "auto_copy_clipboard": 3,
        "run_command": 100,      # always last
    }
    groups.post.sort(key=lambda a: _POST_ORDER.get(a.id, 50))
    return groups


# -- window_ctrl handlers ---------------------------------------------------


class _WindowCtrlHandler:
    """Applies and later restores WINDOW_CTRL attachments.

    Each setup method records one or more undo callbacks in
    ``_restore``; :meth:`teardown` invokes them in reverse order.
    Handlers that can't run on this platform should degrade to a no-op
    instead of raising.

    Two supported attachments:

    * ``hide_cursor`` — exposed as the ``hide_cursor`` property; the
      source layer reads it and adds ``screencapture -C`` so the mouse
      pointer is omitted from the PNG.  (In-process ``NSCursor.hide()``
      has no effect across app boundaries and is unused.)
    * ``hide_dock`` — toggles the system-wide Dock auto-hide flag via
      AppleScript so the Dock animates out before the shot and is
      restored afterwards.
    """

    def __init__(self, atts: list[Attachment]) -> None:
        self.atts = atts
        self._restore: list[Callable[[], None]] = []
        # Observable state consulted by the source layer.
        self.hide_cursor: bool = False

    def setup(self) -> None:
        for att in self.atts:
            try:
                if att.id == "hide_cursor":
                    self.hide_cursor = True
                elif att.id == "hide_dock":
                    self._setup_hide_dock()
            except Exception:  # noqa: BLE001
                logger.exception("window_ctrl setup failed for %s", att.id)

    def teardown(self) -> None:
        # Run in reverse order so nested restores compose correctly.
        while self._restore:
            fn = self._restore.pop()
            try:
                fn()
            except Exception:  # noqa: BLE001
                logger.exception("window_ctrl teardown failed")

    # Individual setups --------------------------------------------------

    def _setup_hide_dock(self) -> None:
        """Toggle the Dock's auto-hide state via AppleScript.

        ``NSApplicationPresentationAutoHideDock`` only affects the
        calling app's presentation stack — useless for a menu-bar-only
        accessory.  System Events is the portable way to drive
        ``com.apple.dock``; we remember the previous state and restore
        it unconditionally.
        """
        try:
            prev_str = subprocess.check_output(
                [
                    "osascript", "-e",
                    'tell application "System Events" to '
                    'get the autohide of the dock preferences',
                ],
                text=True,
                timeout=3,
            ).strip().lower()
        except Exception:  # noqa: BLE001
            logger.warning("hide_dock: failed to read current dock state")
            return

        prev_enabled = (prev_str == "true")
        if prev_enabled:
            # Already hidden — nothing to do and nothing to undo.
            return

        try:
            subprocess.run(
                [
                    "osascript", "-e",
                    'tell application "System Events" to '
                    'set autohide of dock preferences to true',
                ],
                timeout=3,
                check=False,
            )
        except Exception:  # noqa: BLE001
            logger.warning("hide_dock: failed to enable auto-hide")
            return

        def _restore() -> None:
            try:
                subprocess.run(
                    [
                        "osascript", "-e",
                        'tell application "System Events" to '
                        'set autohide of dock preferences to false',
                    ],
                    timeout=3,
                    check=False,
                )
            except Exception:  # noqa: BLE001
                logger.warning("hide_dock: failed to restore auto-hide state")

        self._restore.append(_restore)


# -- feedback handlers ------------------------------------------------------


def _apply_feedback(atts: list[Attachment], ctx: ExecutionContext) -> None:
    """Fire-and-forget feedback effects that run *after* each frame.

    We delegate every user-visible effect (flash / sound / notification)
    to the Swift host via events because:

      * The Python core is a child process without an AppKit main run
        loop — ``NSSound.play()`` there is unreliable.
      * The Swift side already owns the menu-bar icon + notification
        centre and can degrade gracefully when permissions are missing.
    """
    for att in atts:
        try:
            if att.id == "flash_menubar":
                ctx.publish_event("capture.flash_menubar", {})
            elif att.id == "sound":
                volume = float(att.params.get("volume", 0.5))
                ctx.publish_event("capture.sound", {"volume": volume})
            elif att.id == "notification":
                ctx.publish_event("capture.notification", {
                    "title": "DailyStream",
                    "body": "Screenshot captured",
                })
            # silent_save has no *effect*; it's consumed by the delivery
            # stage to decide whether to prompt the user for a description.
        except Exception:  # noqa: BLE001
            logger.exception("feedback handler failed for %s", att.id)


def _play_shutter(volume: float) -> None:
    """Deprecated — kept for backwards compat with any external callers.

    Sound playback is now driven from the Swift side via
    ``capture.sound``.
    """
    try:
        import AppKit  # type: ignore[import]
    except Exception:  # noqa: BLE001
        return
    sound = AppKit.NSSound.soundNamed_("Grab")
    if sound is None:
        return
    try:
        sound.setVolume_(max(0.0, min(1.0, volume)))
    except Exception:  # noqa: BLE001
        pass
    sound.play()


# -- post handlers ----------------------------------------------------------


def _run_post(att: Attachment, frame: FrameResult,
              ctx: ExecutionContext) -> None:
    """Execute a single POST attachment on ``frame``."""
    if frame.path is None:
        return
    try:
        if att.id == "auto_ocr":
            text = _run_ocr(frame.path)
            if text:
                frame.post_artifacts["ocr_text"] = text
        elif att.id == "quick_tags":
            # The Swift side owns the UI for the 2-second tag window;
            # we only broadcast an event with the schema so it can render.
            ctx.publish_event("capture.quick_tags_prompt", {
                "path": str(frame.path),
                "window_seconds": float(att.params.get("window_seconds", 2.0)),
                "tags": list(att.params.get("tags", [])),
            })
        elif att.id == "auto_copy_clipboard":
            _copy_image_to_clipboard(frame.path)
            frame.post_artifacts["copied_to_clipboard"] = True
        elif att.id == "run_command":
            _run_command(att.params, frame, ctx)
        elif att.id == "ai_analyze":
            _run_ai_analyze(att.params, frame, ctx)
    except Exception:  # noqa: BLE001
        logger.exception("post handler failed for %s", att.id)


def _run_ocr(path: Path) -> Optional[str]:
    """Best-effort OCR using the Vision framework — returns ``None`` if unavailable."""
    try:
        import Quartz  # type: ignore[import]
        import Vision  # type: ignore[import]
    except Exception:  # noqa: BLE001
        return None
    try:
        import objc  # type: ignore[import]  # noqa: F401
    except Exception:  # noqa: BLE001
        pass

    try:
        image = Quartz.CIImage.imageWithContentsOfURL_(
            Quartz.NSURL.fileURLWithPath_(str(path))
        )
        if image is None:
            return None
        request = Vision.VNRecognizeTextRequest.alloc().init()
        request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
        handler = Vision.VNImageRequestHandler.alloc().initWithCIImage_options_(
            image, None
        )
        handler.performRequests_error_([request], None)
        observations = request.results() or []
        lines: list[str] = []
        for obs in observations:
            candidates = obs.topCandidates_(1)
            if candidates and len(candidates) > 0:
                lines.append(str(candidates[0].string()))
        return "\n".join(lines).strip() or None
    except Exception:  # noqa: BLE001
        logger.exception("Vision OCR failed")
        return None


def _copy_image_to_clipboard(path: Path) -> None:
    try:
        import AppKit  # type: ignore[import]
    except Exception:  # noqa: BLE001
        return
    image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(path))
    if image is None:
        return
    pb = AppKit.NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.writeObjects_([image])


def _run_command(params: dict[str, Any], frame: FrameResult,
                 ctx: ExecutionContext) -> None:
    """Run a user-defined shell command / script.

    The command can be either an executable script path or an inline
    shell command.  Context is passed via ``DAILYSTREAM_*`` environment
    variables so scripts stay readable.

    **Environment variables injected**

    Core context:

    * ``DAILYSTREAM_FRAME_PATH``    — absolute path of the captured PNG
    * ``DAILYSTREAM_FRAME_INDEX``   — 0-based index in a burst / interval
    * ``DAILYSTREAM_SOURCE_KIND``   — ``interactive`` / ``fullscreen`` / …
    * ``DAILYSTREAM_TIMESTAMP``     — ISO 8601 timestamp
    * ``DAILYSTREAM_WORKSPACE_DIR`` — absolute path of the workspace
    * ``DAILYSTREAM_PIPELINE``      — active pipeline name (may be empty)
    * ``DAILYSTREAM_MODE_ID`` / ``_NAME`` / ``_PRESET_ID`` / ``_PRESET_NAME``
    * ``DAILYSTREAM_DESCRIPTION``   — user-typed HUD description (silent mode only)

    Upstream-attachment artifacts — populated only when the matching
    attachment ran before ``run_command`` on the same frame:

    * ``DAILYSTREAM_OCR_TEXT``           — from ``auto_ocr``
    * ``DAILYSTREAM_AI_DESCRIPTION``     — pretty summary from ``ai_analyze``
    * ``DAILYSTREAM_AI_DESCRIPTION_RAW`` — just the AI description field
    * ``DAILYSTREAM_AI_CATEGORY``        — AI category label
    * ``DAILYSTREAM_AI_KEY_ELEMENTS``    — newline-separated tags
    * ``DAILYSTREAM_ARTIFACTS_JSON``     — JSON blob of the full
      ``frame.post_artifacts`` dict, for scripts that want structured
      access (``jq .ai_key_elements[0]``)

    ``run_command`` is always moved to the tail of the POST list so all
    upstream artifacts are available regardless of the order the user
    dragged things in the Designer.

    On failure we emit a ``capture.hook_failed`` event so the Swift side
    can surface a toast; successful stdout is attached to
    ``frame.post_artifacts['hook_stdout']`` for later consumption.
    """
    import json as _json
    import os

    cmd_raw = str(params.get("command", "")).strip()
    if not cmd_raw:
        return
    wait = bool(params.get("wait", False))
    timeout = int(params.get("timeout_seconds", 30))
    wait_for_ai = int(params.get("wait_for_ai_seconds", 0))

    # Optional grace period so scripts can consume async AI output.
    # We poll for ``ai_description`` (or the failure-path ``ai_prefill``
    # flag) in ``frame.post_artifacts``; done as cheap spin rather than
    # threading.Event so we don't have to rewire ``_run_ai_analyze``.
    if wait_for_ai > 0 and "ai_description" not in frame.post_artifacts:
        deadline = time.monotonic() + wait_for_ai
        while time.monotonic() < deadline:
            if "ai_description" in frame.post_artifacts:
                break
            time.sleep(0.1)

    # Snapshot artifacts at the moment we fire the command.  Async AI
    # runs may still be in-flight; whatever has landed by now is what
    # the script gets.
    artifacts = dict(frame.post_artifacts)

    env = os.environ.copy()
    env.update({
        "DAILYSTREAM_FRAME_PATH": str(frame.path) if frame.path else "",
        "DAILYSTREAM_FRAME_INDEX": str(frame.index),
        "DAILYSTREAM_SOURCE_KIND": frame.source_kind,
        "DAILYSTREAM_OCR_TEXT": str(artifacts.get("ocr_text", "") or ""),
        "DAILYSTREAM_PRESET_ID": ctx.preset_id,
        "DAILYSTREAM_PRESET_NAME": ctx.preset_name,
        "DAILYSTREAM_MODE_ID": ctx.mode_id,
        "DAILYSTREAM_MODE_NAME": ctx.mode_name,
        "DAILYSTREAM_PIPELINE": ctx.wm.get_active_pipeline() or "",
        "DAILYSTREAM_DESCRIPTION": ctx.description,
        "DAILYSTREAM_TIMESTAMP": _iso_now(),
        "DAILYSTREAM_WORKSPACE_DIR": _workspace_dir_for(ctx),
    })

    # --- AI-specific convenience variables ----------------------------
    ai_desc = artifacts.get("ai_description")
    if isinstance(ai_desc, str):
        env["DAILYSTREAM_AI_DESCRIPTION"] = ai_desc
    ai_desc_raw = artifacts.get("ai_description_raw")
    if isinstance(ai_desc_raw, str):
        env["DAILYSTREAM_AI_DESCRIPTION_RAW"] = ai_desc_raw
    ai_category = artifacts.get("ai_category")
    if isinstance(ai_category, str):
        env["DAILYSTREAM_AI_CATEGORY"] = ai_category
    ai_elements = artifacts.get("ai_key_elements")
    if isinstance(ai_elements, list):
        env["DAILYSTREAM_AI_KEY_ELEMENTS"] = "\n".join(
            str(x) for x in ai_elements
        )

    # --- Structured escape hatch --------------------------------------
    try:
        env["DAILYSTREAM_ARTIFACTS_JSON"] = _json.dumps(
            artifacts, ensure_ascii=False, default=str,
        )
    except Exception:  # noqa: BLE001 — never let a weird artifact kill the hook
        env["DAILYSTREAM_ARTIFACTS_JSON"] = "{}"

    # If the command looks like an existing file path, execute it
    # directly; otherwise let the shell interpret it.
    path = Path(cmd_raw)
    if path.is_file():
        argv: list[str] = [str(path)]
        use_shell = False
    else:
        argv = [cmd_raw]
        use_shell = True

    def _execute() -> None:
        try:
            proc = subprocess.run(
                argv if not use_shell else cmd_raw,
                shell=use_shell,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if proc.returncode != 0:
                ctx.publish_event("capture.hook_failed", {
                    "kind": "run_command",
                    "command": cmd_raw,
                    "returncode": proc.returncode,
                    "stderr": (proc.stderr or "").strip()[:400],
                })
            else:
                stdout = (proc.stdout or "").strip()
                if stdout:
                    # Store (inside the frame report; thread-safe because
                    # each frame has its own dict and only one handler
                    # writes to it).
                    frame.post_artifacts["hook_stdout"] = stdout[:2000]
        except subprocess.TimeoutExpired:
            ctx.publish_event("capture.hook_failed", {
                "kind": "run_command",
                "command": cmd_raw,
                "error": f"timeout after {timeout}s",
            })
        except Exception as e:  # noqa: BLE001
            logger.exception("run_command failed")
            ctx.publish_event("capture.hook_failed", {
                "kind": "run_command",
                "command": cmd_raw,
                "error": str(e),
            })

    if wait:
        _execute()
    else:
        threading.Thread(target=_execute, daemon=True,
                         name="dailystream-hook").start()


def _run_ai_analyze(params: dict[str, Any], frame: FrameResult,
                    ctx: ExecutionContext) -> None:
    """Run Claude analysis on the frame and optionally prefill the HUD.

    Uses the same ``ImageAnalyzer`` / ``AnalysisStore`` as the realtime
    AI mode, so results are compatible with the existing analysis
    viewer.
    """
    if frame.path is None or ctx.config is None:
        return

    wait = bool(params.get("wait", True))
    prefill = bool(params.get("prefill_hud", True))
    save_to_analysis = bool(params.get("save_to_analysis", True))
    user_hint = str(params.get("user_hint", "")).strip()

    def _do_analyse() -> None:
        try:
            from ..ai_analyzer import (
                AnalysisResult,
                AnalysisStore,
                ImageAnalyzer,
            )
        except ImportError:
            return

        analyzer = ImageAnalyzer(ctx.config)
        if not analyzer.available:
            ctx.publish_event("capture.hook_failed", {
                "kind": "ai_analyze",
                "error": "AI analyzer is not available "
                         "(missing API key or anthropic SDK)",
            })
            return

        result = analyzer.analyze_image(frame.path, user_hint=user_hint)
        if result is None:
            ctx.publish_event("capture.hook_failed", {
                "kind": "ai_analyze",
                "error": "Analysis returned no result",
            })
            return

        # Build a single-line summary the HUD can pre-fill.
        description_parts: list[str] = []
        if result.description:
            description_parts.append(result.description.strip())
        if result.category:
            description_parts.append(f"[{result.category}]")
        if result.key_elements:
            tag_line = " ".join(f"#{t}" for t in result.key_elements[:5])
            if tag_line:
                description_parts.append(tag_line)
        summary = " ".join(description_parts).strip()[:400]

        if summary:
            frame.post_artifacts["ai_description"] = summary
            if prefill:
                # Same key as OCR — the HUD prefers ai_description over
                # ocr_text when both are present (handled Swift-side).
                frame.post_artifacts.setdefault("ocr_text", summary)
                frame.post_artifacts["ai_prefill"] = True

        # Atomic artifacts so downstream handlers (notably ``run_command``)
        # can grab individual fields without parsing the ``ai_description``
        # string.  ``ai_description`` stays the "pretty" summary, these are
        # raw values.
        if result.description:
            frame.post_artifacts["ai_description_raw"] = result.description.strip()
        if result.category:
            frame.post_artifacts["ai_category"] = result.category
        if result.key_elements:
            frame.post_artifacts["ai_key_elements"] = list(result.key_elements)

        # Persist into ai_analyses.json so the existing viewer picks it up.
        if save_to_analysis and ctx.pm is not None and ctx.wm.is_active:
            try:
                pipeline = ctx.wm.get_active_pipeline()
                if pipeline and hasattr(ctx.pm, "get_ai_analyses_path"):
                    store_path = ctx.pm.get_ai_analyses_path(pipeline)  # type: ignore[attr-defined]
                    store = AnalysisStore(store_path, pipeline_name=pipeline)
                    result.entry_index = -1
                    result.timestamp = _iso_now()
                    store.set_model(analyzer._model)
                    store.append(result)
            except Exception:  # noqa: BLE001
                logger.exception("ai_analyze persistence failed")

    if wait:
        _do_analyse()
    else:
        threading.Thread(target=_do_analyse, daemon=True,
                         name="dailystream-ai").start()


# --- Small helpers shared by hook handlers --------------------------------

def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _workspace_dir_for(ctx: ExecutionContext) -> str:
    ws_dir = getattr(ctx.wm, "workspace_dir", None)
    return str(ws_dir) if ws_dir else ""


# -- source handlers --------------------------------------------------------


def _acquire_source(source: Source, ctx: ExecutionContext,
                    hide_cursor: bool = False) -> Optional[Path]:
    """Grab one frame according to the Preset's source definition."""
    assert ctx.pm is not None, "ExecutionContext.pm required for source acquisition"
    save_dir = ctx.pm.get_screenshots_dir()
    save_dir.mkdir(parents=True, exist_ok=True)

    if source.kind == SourceKind.CLIPBOARD:
        from ..capture import save_clipboard_image
        return save_clipboard_image(save_dir)

    # Screen capture family
    from ..capture import take_screenshot
    if source.kind == SourceKind.REGION:
        if not source.region:
            logger.warning("REGION source missing coords")
            return None
        return take_screenshot(
            save_dir, region=source.region, no_cursor=hide_cursor,
        )
    if source.kind == SourceKind.FULLSCREEN:
        return take_screenshot(
            save_dir, mode="fullscreen", no_cursor=hide_cursor,
        )
    # WINDOW falls back to interactive until we ship a dedicated
    # window-picker overlay.
    return take_screenshot(
        save_dir, mode="interactive", no_cursor=hide_cursor,
    )


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class CaptureExecutor:
    """Stateless (per-call) executor for a single :class:`Preset`.

    The executor owns *no* global state — construct a fresh one per
    call.  This makes it trivially thread-safe and lets tests stub out
    individual handlers by subclassing.
    """

    def execute(self, preset: Preset, ctx: ExecutionContext) -> ExecutionReport:
        """Run the full Preset end-to-end.

        * Returns immediately for ``interval`` strategy (delegates to
          the long-running RPC path).
        * Returns after N frames for ``burst``.
        * Returns after one frame for ``single``.
        """
        report = ExecutionReport(
            mode_id=ctx.mode_id,
            preset_id=preset.id,
            preset_name=preset.name,
        )
        groups = _group_attachments(preset.attachments)

        # Determine silent mode: executor-level override OR silent_save
        # attachment OR multi-frame strategies where prompting per-frame
        # makes no sense (burst / interval).
        strategy_id = groups.strategy.id if groups.strategy else "single"
        silent_att = any(a.id == "silent_save" for a in groups.feedback)
        multishot = strategy_id in ("burst", "interval")
        report.silent = ctx.silent or silent_att or multishot

        # WINDOW_CTRL setup (restored after source acquisition).
        wctl = _WindowCtrlHandler(groups.window_ctrl)
        wctl.setup()

        try:
            if strategy_id == "single":
                self._run_single(preset, groups, report, ctx)
            elif strategy_id == "burst":
                self._run_burst(preset, groups, report, ctx)
            elif strategy_id == "interval":
                # Interval handled by the dedicated start/stop RPCs —
                # treat a direct execute() as "take one frame now and
                # let the caller schedule the rest".
                self._run_single(preset, groups, report, ctx)
            else:
                self._run_single(preset, groups, report, ctx)
        except Exception as e:  # noqa: BLE001
            logger.exception("Preset execution failed")
            report.error = str(e)
        finally:
            wctl.teardown()

        return report

    # -- strategies -----------------------------------------------------

    def _run_single(self, preset: Preset, groups: _AttGroups,
                    report: ExecutionReport, ctx: ExecutionContext) -> None:
        frame = self._capture_one_frame(preset, groups, ctx, index=0)
        report.frames.append(frame)

    def _run_burst(self, preset: Preset, groups: _AttGroups,
                   report: ExecutionReport, ctx: ExecutionContext) -> None:
        params = groups.strategy.params if groups.strategy else {}
        count = max(1, int(params.get("count", 3)))
        interval_ms = max(0, int(params.get("interval_ms", 200)))
        for i in range(count):
            frame = self._capture_one_frame(preset, groups, ctx, index=i)
            report.frames.append(frame)
            if i < count - 1:
                time.sleep(interval_ms / 1000.0)

    # -- per-frame plumbing --------------------------------------------

    def _capture_one_frame(
        self,
        preset: Preset,
        groups: _AttGroups,
        ctx: ExecutionContext,
        index: int,
    ) -> FrameResult:
        frame = FrameResult(
            path=None,
            index=index,
            source_kind=preset.source.kind.value,
        )

        try:
            hide_cursor = any(
                a.id == "hide_cursor" for a in groups.window_ctrl
            )
            path = _acquire_source(
                preset.source, ctx, hide_cursor=hide_cursor,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("Source acquisition failed")
            frame.error = str(e)
            return frame

        if path is None:
            frame.skipped = True
            frame.error = "source returned no file (cancelled or unavailable)"
            return frame

        # Defensive: the source helper already does save_path.exists(),
        # but some edge cases (permission prompt, user ESC after file
        # creation) leave behind a zero-byte stub or the path vanishes
        # before we get here.  Fail fast with a clear reason so the
        # Swift HUD doesn't get opened on a broken frame.
        try:
            if not path.exists() or path.stat().st_size == 0:
                frame.skipped = True
                frame.error = (
                    "screencapture returned without a valid image — "
                    "was the selection cancelled, or is Screen "
                    "Recording permission missing for the Python "
                    "helper?  See ~/Library/Logs/DailyStream/core.log "
                    "for the raw screencapture exit code."
                )
                return frame
        except OSError as e:
            frame.skipped = True
            frame.error = f"cannot stat captured file: {e}"
            return frame

        frame.path = path

        # FEEDBACK: runs after the shot so the user knows it happened.
        _apply_feedback(groups.feedback, ctx)

        # POST: per-frame post-processing.
        for post_att in groups.post:
            _run_post(post_att, frame, ctx)

        return frame
