"""Workspace management for DailyStream."""

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

    def _save_meta(self) -> None:
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

    def create(self, base_path: Optional[Path] = None, title: Optional[str] = None) -> Path:
        """Create a new workspace. Returns workspace directory path."""
        if base_path is None:
            base_path = DEFAULT_WORKSPACE_ROOT

        base_path = Path(base_path)
        workspace_id = datetime.now().strftime("%Y-%m-%d_%H%M%S")

        # Folder name includes the workspace title for readability
        safe_title = self._safe_dirname(title) if title else ""
        folder_name = f"{workspace_id}_{safe_title}" if safe_title else workspace_id
        workspace_dir = base_path / folder_name
        workspace_dir.mkdir(parents=True, exist_ok=True)

        self._workspace_dir = workspace_dir
        self._meta = WorkspaceMeta(
            workspace_id=workspace_id,
            workspace_path=str(workspace_dir),
            created_at=now_iso(),
            title=title or workspace_id,
        )
        self._save_meta()
        set_active_workspace_path(workspace_dir)
        return workspace_dir

    def end(self) -> Optional[str]:
        """End the current workspace. Returns path to timeline report or None."""
        if not self.is_active:
            return None
        self._meta.ended_at = now_iso()
        self._save_meta()
        set_active_workspace_path(None)

        # Generate timeline report
        from .timeline import generate_timeline
        report_path = generate_timeline(self._workspace_dir, self._meta)

        # Sync all entries to notes on end
        self._sync_all_on_end()

        return str(report_path) if report_path else None

    def _sync_all_on_end(self) -> None:
        """Sync only un-synced pipeline entries to local Markdown when ending workspace."""
        try:
            from .config import Config
            from .note_sync import NoteSyncManager
            from .pipeline import PipelineManager

            config = Config.load()
            syncer = NoteSyncManager(config, workspace_dir=self._workspace_dir)
            pm = PipelineManager(self._workspace_dir)

            for pipeline_name in pm.list_pipelines():
                for i, entry in enumerate(pm.get_entries(pipeline_name)):
                    # Skip entries already synced in real-time
                    if entry.get("synced", False):
                        continue
                    syncer.sync_entry(
                        workspace_meta=self._meta,
                        pipeline_name=pipeline_name,
                        entry=entry,
                    )
                    pm.mark_entry_synced(pipeline_name, i)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"End sync failed: {e}")

    def add_pipeline(self, name: str) -> None:
        """Register a pipeline in workspace metadata."""
        if self._meta and name not in self._meta.pipelines:
            self._meta.pipelines.append(name)
            self._save_meta()

    def activate_pipeline(self, name: str) -> bool:
        """Activate a pipeline. Returns True if successful."""
        if self._meta and name in self._meta.pipelines:
            self._meta.active_pipeline = name
            self._save_meta()
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
