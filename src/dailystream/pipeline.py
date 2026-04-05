"""Pipeline management for DailyStream."""

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from .config import read_json, write_json, now_iso, now_filename


@dataclass
class PipelineEntry:
    """A single entry in a pipeline."""

    timestamp: str
    input_type: str  # "url" | "image" | "text"
    input_content: str  # file path, URL, or text content
    description: str


class PipelineManager:
    """Manages pipeline lifecycle within a workspace."""

    def __init__(
        self,
        workspace_dir: Path,
        screenshot_save_path: str = "",
    ) -> None:
        self._workspace_dir = workspace_dir
        self._custom_screenshots_dir: Optional[Path] = None
        if screenshot_save_path:
            self._custom_screenshots_dir = Path(screenshot_save_path)

    def _pipeline_dir(self, name: str) -> Path:
        return self._workspace_dir / "pipelines" / name

    def _context_path(self, name: str) -> Path:
        return self._pipeline_dir(name) / "context.json"

    def _screenshots_dir(self) -> Path:
        """Screenshots directory.

        Returns the custom path from config if set, otherwise falls back
        to the default ``<workspace>/screenshots/``.
        """
        if self._custom_screenshots_dir is not None:
            return self._custom_screenshots_dir
        return self._workspace_dir / "screenshots"

    def create(
        self,
        name: str,
        description: str = "",
        goal: str = "",
    ) -> Path:
        """Create a new pipeline. Returns pipeline directory.

        Parameters
        ----------
        name
            Pipeline name (used as directory name).
        description
            Free-form description of what this pipeline is about.
        goal
            The objective / goal this pipeline aims to achieve.
        """
        pipeline_dir = self._pipeline_dir(name)
        pipeline_dir.mkdir(parents=True, exist_ok=True)
        self._screenshots_dir().mkdir(exist_ok=True)

        # Initialize context.json if not exists
        ctx_path = self._context_path(name)
        if not ctx_path.exists():
            write_json(ctx_path, {
                "name": name,
                "created_at": now_iso(),
                "description": description,
                "goal": goal,
                "entries": [],
            })
        return pipeline_dir

    def get_pipeline_meta(self, name: str) -> dict:
        """Get pipeline metadata (name, description, goal, created_at)."""
        ctx = read_json(self._context_path(name))
        return {
            "name": ctx.get("name", name),
            "description": ctx.get("description", ""),
            "goal": ctx.get("goal", ""),
            "created_at": ctx.get("created_at", ""),
        }

    def list_pipelines(self) -> list[str]:
        """List all pipeline names in workspace."""
        pipelines_dir = self._workspace_dir / "pipelines"
        if not pipelines_dir.exists():
            return []
        return sorted([
            d.name for d in pipelines_dir.iterdir()
            if d.is_dir() and (d / "context.json").exists()
        ])

    def add_entry(
        self,
        pipeline_name: str,
        input_type: str,
        input_content: str,
        description: str,
    ) -> PipelineEntry:
        """Add an entry to a pipeline. Returns the created entry."""
        entry = PipelineEntry(
            timestamp=now_iso(),
            input_type=input_type,
            input_content=input_content,
            description=description,
        )

        ctx_path = self._context_path(pipeline_name)
        ctx = read_json(ctx_path)
        if "entries" not in ctx:
            ctx["entries"] = []
        entry_dict = asdict(entry)
        entry_dict["synced"] = False
        ctx["entries"].append(entry_dict)
        write_json(ctx_path, ctx)
        return entry

    def mark_entry_synced(self, pipeline_name: str, index: int) -> None:
        """Mark an entry as synced by index."""
        ctx_path = self._context_path(pipeline_name)
        ctx = read_json(ctx_path)
        entries = ctx.get("entries", [])
        if 0 <= index < len(entries):
            entries[index]["synced"] = True
            write_json(ctx_path, ctx)

    def get_entries(self, pipeline_name: str) -> list[dict]:
        """Get all entries for a pipeline."""
        ctx = read_json(self._context_path(pipeline_name))
        return ctx.get("entries", [])

    def get_screenshots_dir(self, pipeline_name: str = "") -> Path:
        """Get screenshots directory (workspace-level), creating if needed.

        The *pipeline_name* argument is accepted for backward-compat but
        ignored — all screenshots live under ``<workspace>/screenshots/``.
        """
        d = self._screenshots_dir()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get_all_entries(self) -> list[dict]:
        """Get all entries across all pipelines, with pipeline name attached.
        
        Returns a new list where each entry dict has a 'pipeline' key added,
        without modifying the original stored entries.
        """
        all_entries = []
        for name in self.list_pipelines():
            for entry in self.get_entries(name):
                # Create new dict with pipeline info, don't modify original
                entry_with_pipeline = {**entry, "pipeline": name}
                all_entries.append(entry_with_pipeline)
        all_entries.sort(key=lambda e: e.get("timestamp", ""))
        return all_entries
