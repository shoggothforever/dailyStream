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
recipe.  For ``interval`` and ``hold_to_repeat`` strategies the caller
is expected to wrap the call in a background thread or to use the
dedicated RPC methods (``capture.start_interval`` /
``capture.stop_interval``) implemented elsewhere.
"""

from __future__ import annotations

import logging
import subprocess
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
    return groups


# -- window_ctrl handlers ---------------------------------------------------


class _WindowCtrlHandler:
    """Applies and later restores WINDOW_CTRL attachments.

    Each method returns an opaque "restore token" that is consumed by
    :meth:`teardown`.  Handlers that can't run on this platform should
    degrade to a no-op instead of raising.
    """

    def __init__(self, atts: list[Attachment]) -> None:
        self.atts = atts
        self._restore: list[Callable[[], None]] = []

    def setup(self) -> None:
        for att in self.atts:
            try:
                if att.id == "hide_cursor":
                    self._setup_hide_cursor()
                elif att.id == "hide_dock":
                    self._setup_hide_dock()
                elif att.id == "bring_to_front":
                    self._setup_bring_to_front(att.params)
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

    def _setup_hide_cursor(self) -> None:
        try:
            import AppKit  # type: ignore[import]
        except Exception:  # noqa: BLE001
            return
        AppKit.NSCursor.hide()
        self._restore.append(lambda: AppKit.NSCursor.unhide())

    def _setup_hide_dock(self) -> None:
        try:
            import AppKit  # type: ignore[import]
        except Exception:  # noqa: BLE001
            return
        app = AppKit.NSApp
        prev = app.presentationOptions()
        new = prev | AppKit.NSApplicationPresentationAutoHideDock
        app.setPresentationOptions_(new)
        self._restore.append(lambda: app.setPresentationOptions_(prev))

    def _setup_bring_to_front(self, params: dict[str, Any]) -> None:
        # Without a specific bundle id we simply activate whatever app
        # owns the frontmost window — same behaviour as clicking on it.
        try:
            import AppKit  # type: ignore[import]
        except Exception:  # noqa: BLE001
            return
        ws = AppKit.NSWorkspace.sharedWorkspace()
        front = ws.frontmostApplication()
        if front is not None:
            front.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)


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
        elif att.id == "open_in_editor":
            editor = str(att.params.get("editor", "default"))
            _open_in_editor(frame.path, editor)
            frame.post_artifacts["opened_editor"] = editor
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


def _open_in_editor(path: Path, editor: str) -> None:
    if editor == "preview":
        subprocess.Popen(["open", "-a", "Preview", str(path)])
    elif editor == "vscode":
        subprocess.Popen(["open", "-a", "Visual Studio Code", str(path)])
    else:
        subprocess.Popen(["open", str(path)])


# -- source handlers --------------------------------------------------------


def _acquire_source(source: Source, ctx: ExecutionContext) -> Optional[Path]:
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
        return take_screenshot(save_dir, region=source.region)
    if source.kind == SourceKind.FULLSCREEN:
        return take_screenshot(save_dir, mode="fullscreen")
    # WINDOW falls back to interactive until we ship a dedicated
    # window-picker overlay.
    return take_screenshot(save_dir, mode="interactive")


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
        * Returns after one frame for ``single`` and ``hold_to_repeat``
          (the latter is driven by the Swift key-up event).
        """
        report = ExecutionReport(
            mode_id=ctx.mode_id,
            preset_id=preset.id,
            preset_name=preset.name,
        )
        groups = _group_attachments(preset.attachments)

        # Determine silent mode: executor-level override OR silent_save
        # attachment OR strategy where prompting per-frame makes no sense
        # (burst / interval / hold).
        strategy_id = groups.strategy.id if groups.strategy else "single"
        silent_att = any(a.id == "silent_save" for a in groups.feedback)
        multishot = strategy_id in ("burst", "interval", "hold_to_repeat")
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
            elif strategy_id == "hold_to_repeat":
                # Same rationale as interval — Swift side drives the
                # repeat; here we take a single frame.
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
            path = _acquire_source(preset.source, ctx)
        except Exception as e:  # noqa: BLE001
            logger.exception("Source acquisition failed")
            frame.error = str(e)
            return frame

        if path is None:
            frame.skipped = True
            frame.error = "source returned no file (cancelled or unavailable)"
            return frame

        frame.path = path

        # FEEDBACK: runs after the shot so the user knows it happened.
        _apply_feedback(groups.feedback, ctx)

        # POST: per-frame post-processing.
        for post_att in groups.post:
            _run_post(post_att, frame, ctx)

        return frame
