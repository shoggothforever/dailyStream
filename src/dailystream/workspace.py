"""Workspace management for DailyStream."""

import re
import subprocess
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import (
    Config,
    DEFAULT_WORKSPACE_ROOT,
    read_json,
    write_json,
    now_iso,
    get_active_workspace_path,
    set_active_workspace_path,
)


@dataclass
class WorkspaceMeta:
    """Workspace metadata."""

    workspace_id: str
    workspace_path: str
    created_at: str
    ended_at: Optional[str] = None
    title: Optional[str] = None
    active_pipeline: Optional[str] = None
    pipelines: list[str] = field(default_factory=list)
    ai_mode: str = "off"  # "off" | "realtime" | "daily_report"


class WorkspaceManager:
    """Manages workspace lifecycle."""

    def __init__(self) -> None:
        self._meta: Optional[WorkspaceMeta] = None
        self._workspace_dir: Optional[Path] = None
        # Try to load active workspace on init
        active = get_active_workspace_path()
        if active:
            self.load(active)

    @property
    def meta(self) -> Optional[WorkspaceMeta]:
        return self._meta

    @property
    def workspace_dir(self) -> Optional[Path]:
        return self._workspace_dir

    @property
    def is_active(self) -> bool:
        return self._meta is not None and self._meta.ended_at is None

    def _meta_path(self) -> Path:
        assert self._workspace_dir is not None
        return self._workspace_dir / "workspace_meta.json"

    def save_meta(self) -> None:
        if self._meta:
            write_json(self._meta_path(), asdict(self._meta))

    def load(self, workspace_dir: Path) -> bool:
        """Load workspace from directory. Returns True if successful."""
        meta_path = workspace_dir / "workspace_meta.json"
        if not meta_path.exists():
            return False
        data = read_json(meta_path)
        try:
            self._meta = WorkspaceMeta(**{
                k: v for k, v in data.items()
                if k in WorkspaceMeta.__dataclass_fields__
            })
            self._workspace_dir = workspace_dir
            return True
        except TypeError:
            return False

    @staticmethod
    def _safe_dirname(name: str) -> str:
        """Sanitize a workspace title for use as part of a directory name.

        Removes / replaces characters that are illegal or awkward in
        file-system paths while keeping the name readable.
        """
        import re
        # Replace path-separator and other problematic chars with underscore
        s = re.sub(r'[/\\:*?"<>|\n\r\t]', '_', name)
        # Collapse runs of underscores / spaces and strip
        s = re.sub(r'[_\s]+', '_', s).strip('_. ')
        return s[:64] if s else ""

    def create(
        self,
        base_path: Optional[Path] = None,
        title: Optional[str] = None,
        ai_mode: str = "off",
    ) -> Path:
        """Create a new workspace. Returns workspace directory path.

        Directory layout::

            <base_path>/
              260404/                ← date folder (yymmdd)
                my_workspace/       ← workspace name
                  workspace_meta.json
                  stream.md
                  screenshots/
                  pipelines/
        """
        if base_path is None:
            base_path = DEFAULT_WORKSPACE_ROOT

        base_path = Path(base_path)
        now = datetime.now()
        workspace_id = now.strftime("%Y-%m-%d_%H%M%S")

        # Two-level layout: yymmdd / workspace_name
        date_folder = now.strftime("%y%m%d")
        safe_title = self._safe_dirname(title) if title else workspace_id
        workspace_dir = base_path / date_folder / safe_title
        # If same name already exists today, append a short timestamp suffix
        if workspace_dir.exists():
            suffix = now.strftime("%H%M%S")
            workspace_dir = base_path / date_folder / f"{safe_title}_{suffix}"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        self._workspace_dir = workspace_dir
        self._meta = WorkspaceMeta(
            workspace_id=workspace_id,
            workspace_path=str(workspace_dir),
            created_at=now_iso(),
            title=title or workspace_id,
            ai_mode=ai_mode,
        )
        self.save_meta()
        set_active_workspace_path(workspace_dir)
        return workspace_dir

    def end(self, config: Optional["Config"] = None, analysis_queue=None) -> Optional[str]:
        """End the current workspace. Returns path to timeline report or None.

        Parameters
        ----------
        config
            Application config (needed for timeline generation and AI).
        analysis_queue
            The ``AnalysisQueue`` instance (realtime mode).  If provided,
            the queue is drained before generating the report so that
            all pending analyses are flushed to ``ai_analyses.json``.
        """
        if not self.is_active:
            return None
        self._meta.ended_at = now_iso()
        self.save_meta()
        set_active_workspace_path(None)

        ai_mode = self._meta.ai_mode or "off"

        # --- AI analysis dispatch ---
        if ai_mode == "realtime" and analysis_queue is not None:
            # Wait for the background queue to finish all pending tasks
            try:
                analysis_queue.drain(timeout=120.0)
            except Exception:
                import traceback
                traceback.print_exc()

        elif ai_mode == "daily_report" and config is not None:
            # Batch-analyse all un-analysed entries, then generate summary
            try:
                from .ai_analyzer import batch_analyze_workspace, generate_daily_summary

                batch_analyze_workspace(config, self._workspace_dir, self._meta)
                generate_daily_summary(config, self._workspace_dir, self._meta)
            except Exception:
                import traceback
                traceback.print_exc()

        # Generate timeline report
        from .timeline import generate_timeline
        report_path = generate_timeline(
            self._workspace_dir,
            self._meta,
            config=config,
        )

        return str(report_path) if report_path else None

    # ------------------------------------------------------------------
    # Move / relocate
    # ------------------------------------------------------------------

    def move(
        self,
        target_parent: Path,
        keep_layout: bool = True,
    ) -> Path:
        """Relocate the **already-loaded** workspace into a new parent.

        The workspace MUST be loaded (via :meth:`load`) and MUST NOT be
        active — callers are expected to ``end`` the workspace first
        (the RPC layer handles that and the subsequent ``reopen`` so the
        UX feels continuous).

        Parameters
        ----------
        target_parent
            Existing directory the workspace will be placed inside.  When
            ``keep_layout`` is True (the default) the existing
            ``yymmdd/<title>`` two-level layout is preserved beneath
            ``target_parent`` so the destination ends up at
            ``target_parent/<yymmdd>/<title>``.  Otherwise the workspace
            is placed directly under ``target_parent``.
        keep_layout
            See above.

        Returns the new workspace directory.

        Strategy
        --------
        1. Compute ``dst_dir`` and assert it doesn't already exist.
        2. Try ``os.rename`` first — that's atomic on the same volume
           and finishes in milliseconds even for huge workspaces.
        3. On ``OSError`` for cross-device (errno 18 / EXDEV) fall back
           to ``shutil.copytree`` + post-copy verification + ``rmtree``
           of the original.  On any verification failure the original
           is preserved and the partial copy is removed.
        4. Rewrite ``workspace_meta.json``'s ``workspace_path`` to the
           new absolute path.
        5. Normalise any absolute ``input_content`` paths in
           ``pipelines/*/context.json`` to workspace-relative form so
           future moves are friction-free.
        6. Update the in-memory ``_workspace_dir`` reference.
        """
        import errno
        import shutil

        if self._workspace_dir is None or self._meta is None:
            raise RuntimeError("move() requires a loaded workspace")
        if self.is_active:
            raise RuntimeError(
                "move() requires the workspace to be ended first; "
                "call end() before moving"
            )

        target_parent = Path(target_parent).expanduser().resolve()
        if not target_parent.exists() or not target_parent.is_dir():
            raise NotADirectoryError(
                f"target parent does not exist or is not a directory: "
                f"{target_parent}"
            )

        src_dir = self._workspace_dir.resolve()
        if keep_layout:
            # Preserve the <yymmdd>/<title> layout: take the workspace's
            # own basename + its date-folder parent's name.
            date_folder = src_dir.parent.name
            title_folder = src_dir.name
            dst_dir = target_parent / date_folder / title_folder
        else:
            dst_dir = target_parent / src_dir.name

        # Refuse to overwrite — caller must clean up beforehand.
        if dst_dir.exists():
            raise FileExistsError(
                f"destination already exists: {dst_dir}"
            )
        # Refuse no-op / parent-of-self moves.
        try:
            if dst_dir.resolve() == src_dir:
                raise ValueError("destination is identical to source")
        except OSError:
            pass

        dst_dir.parent.mkdir(parents=True, exist_ok=True)

        # ── 2. attempt atomic rename, fall back to copy on EXDEV ──
        used_copy = False
        try:
            src_dir.rename(dst_dir)
        except OSError as e:
            if e.errno != errno.EXDEV:
                # Real failure — bubble up so caller can surface it.
                raise
            used_copy = True
            # Cross-device: copy → verify → delete original.
            shutil.copytree(src_dir, dst_dir, symlinks=True)
            missing = self._verify_image_entries(dst_dir)
            if missing:
                # Roll back: remove partial copy and any empty parent
                # directories we created on the way down.  Original
                # at ``src_dir`` is left intact.
                shutil.rmtree(dst_dir, ignore_errors=True)
                cleanup_dir = dst_dir.parent
                while (cleanup_dir != target_parent
                       and cleanup_dir.exists()
                       and not any(cleanup_dir.iterdir())):
                    try:
                        cleanup_dir.rmdir()
                    except OSError:
                        break
                    cleanup_dir = cleanup_dir.parent
                raise RuntimeError(
                    f"verification failed after cross-device copy: "
                    f"{len(missing)} image(s) missing in destination "
                    f"(first: {missing[0]}); original at {src_dir} is "
                    f"untouched"
                ) from None
            shutil.rmtree(src_dir)

        # ── 4. patch workspace_meta.json's workspace_path ────────
        meta_file = dst_dir / "workspace_meta.json"
        if meta_file.exists():
            meta_data = read_json(meta_file)
            meta_data["workspace_path"] = str(dst_dir)
            write_json(meta_file, meta_data)

        # ── 5. relativise stale absolute input_content paths ─────
        self._relativise_entries(dst_dir, src_dir)

        # ── 6. update in-memory state ────────────────────────────
        self._workspace_dir = dst_dir
        if self._meta is not None:
            self._meta.workspace_path = str(dst_dir)

        return dst_dir

    @staticmethod
    def _verify_image_entries(ws_dir: Path) -> list[Path]:
        """Walk every entry in ``ws_dir`` and return paths that don't
        resolve.  Used as a post-copy sanity check."""
        from .pipeline import resolve_entry_path

        missing: list[Path] = []
        pipelines_dir = ws_dir / "pipelines"
        if not pipelines_dir.is_dir():
            return missing
        for ctx_file in pipelines_dir.glob("*/context.json"):
            try:
                ctx = read_json(ctx_file)
            except Exception:  # noqa: BLE001
                continue
            for entry in ctx.get("entries", []):
                if entry.get("input_type") != "image":
                    continue
                ic = entry.get("input_content", "")
                if not ic:
                    continue
                resolved = resolve_entry_path(ws_dir, ic)
                if not resolved.exists():
                    missing.append(resolved)
        return missing

    @staticmethod
    def _relativise_entries(new_ws: Path, old_ws: Path) -> None:
        """Rewrite every absolute ``input_content`` path that *used* to
        live inside ``old_ws`` so it's now a workspace-relative POSIX
        path under ``new_ws``.  Paths outside the workspace are left
        alone (the user might have configured an external screenshot
        folder).

        This keeps the workspace portable for any future move — the
        next time the user relocates it a plain ``rename`` is enough,
        no JSON rewrites needed.
        """
        pipelines_dir = new_ws / "pipelines"
        if not pipelines_dir.is_dir():
            return

        old_ws_abs = old_ws.resolve()
        for ctx_file in pipelines_dir.glob("*/context.json"):
            try:
                ctx = read_json(ctx_file)
            except Exception:  # noqa: BLE001
                continue
            dirty = False
            for entry in ctx.get("entries", []):
                if entry.get("input_type") != "image":
                    continue
                ic = entry.get("input_content", "")
                if not ic:
                    continue
                p = Path(ic)
                if not p.is_absolute():
                    continue
                # Was the absolute path pointing into the old workspace?
                try:
                    rel = p.resolve().relative_to(old_ws_abs)
                except (ValueError, OSError):
                    # External path — leave it alone.
                    continue
                entry["input_content"] = rel.as_posix()
                dirty = True
            if dirty:
                write_json(ctx_file, ctx)

    def add_pipeline(self, name: str) -> None:
        """Register a pipeline in workspace metadata."""
        if self._meta and name not in self._meta.pipelines:
            self._meta.pipelines.append(name)
            self.save_meta()

    def activate_pipeline(self, name: str) -> bool:
        """Activate a pipeline. Returns True if successful."""
        if self._meta and name in self._meta.pipelines:
            self._meta.active_pipeline = name
            self.save_meta()
            return True
        return False

    def get_active_pipeline(self) -> Optional[str]:
        """Get the currently active pipeline name."""
        if self._meta:
            return self._meta.active_pipeline
        return None


def choose_folder_dialog() -> Optional[Path]:
    """Show macOS native folder chooser dialog. Returns selected path or None."""
    try:
        result = subprocess.run(
            [
                "osascript", "-e",
                'POSIX path of (choose folder with prompt "选择工作区存储位置")',
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None
