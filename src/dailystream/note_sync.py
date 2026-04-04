"""Note sync module for DailyStream — local Markdown and Obsidian.

The primary sync target is a Markdown file inside the workspace directory
itself (``stream.md``).  Images are referenced by relative path so the
whole workspace folder is self-contained and portable.

Obsidian sync is still supported as an optional secondary backend.
"""

import logging
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .config import Config

logger = logging.getLogger(__name__)


class LocalMarkdownSyncer:
    """Sync entries to a Markdown file inside the workspace directory.

    File layout::

        <workspace>/
          stream.md            ← this file
          screenshots/         ← all images live here (workspace-level)
          pipelines/<name>/
            context.json
    """

    def __init__(self, workspace_dir: Path) -> None:
        self._ws_dir = workspace_dir
        self._md_path = workspace_dir / "stream.md"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def sync_entry(
        self,
        workspace_title: str,
        pipeline_name: str,
        timestamp: str,
        input_type: str,
        description: str,
        content: str,
        image_path: Optional[str] = None,
    ) -> None:
        """Append an entry to ``stream.md``."""
        time_short = timestamp.split("T")[1][:8] if "T" in timestamp else timestamp

        existing = ""
        if self._md_path.exists():
            existing = self._md_path.read_text(encoding="utf-8")

        lines: list[str] = []

        # First entry → write title header
        if not existing:
            lines.append(f"# {workspace_title}\n")

        # Pipeline heading (only once per pipeline)
        heading = f"## {pipeline_name}"
        if heading not in existing:
            lines.append(f"\n{heading}\n")

        # Entry — keep it minimal, entries are separated by ---
        lines.append(f"\n**{time_short}** · {input_type}\n")
        if description:
            lines.append(f"\n{description}\n")

        if input_type == "image" and image_path:
            # Use relative path from workspace root so Markdown renderers
            # (Obsidian, Typora, VS Code, GitHub, …) can display it.
            try:
                rel = Path(image_path).resolve().relative_to(self._ws_dir.resolve())
            except ValueError:
                rel = Path(image_path)
            # URL-encode spaces (and other special chars) so Markdown
            # renderers can resolve the link correctly.
            from urllib.parse import quote
            rel_encoded = quote(str(rel.as_posix()), safe="/")
            lines.append(f"\n![screenshot]({rel_encoded})\n")
        elif input_type == "url":
            lines.append(f"\n[{content}]({content})\n")
        elif input_type == "text" and content != description:
            lines.append(f"\n> {content[:500]}\n")

        lines.append("\n---\n")

        with open(self._md_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))


class ObsidianSyncer:
    """Sync entries to Obsidian vault as Markdown.

    Structure: one workspace = one directory + one main .md file.
    Pipelines are separated by headings.
    """

    def __init__(self, vault_path: str) -> None:
        self.vault_path = Path(vault_path)

    def sync_entry(
        self,
        workspace_id: str,
        workspace_title: str,
        pipeline_name: str,
        timestamp: str,
        input_type: str,
        description: str,
        content: str,
        image_path: Optional[str] = None,
    ) -> None:
        """Append an entry to the workspace markdown file."""
        ws_dir = self.vault_path / "DailyStream" / workspace_id
        ws_dir.mkdir(parents=True, exist_ok=True)

        md_file = ws_dir / f"{workspace_title or workspace_id}.md"

        # Copy image to workspace dir if present
        rel_img = None
        if image_path and Path(image_path).exists():
            screenshots_dir = ws_dir / "screenshots"
            screenshots_dir.mkdir(exist_ok=True)
            src = Path(image_path)
            dst = screenshots_dir / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
            rel_img = f"screenshots/{src.name}"

        # Format time
        time_short = timestamp.split("T")[1][:8] if "T" in timestamp else timestamp

        # Build markdown entry
        lines: list[str] = []

        # Check if file exists and if pipeline heading already present
        existing = ""
        if md_file.exists():
            existing = md_file.read_text(encoding="utf-8")

        if not existing:
            lines.append(f"# {workspace_title or workspace_id}\n")

        # Add pipeline heading if not present
        heading = f"## {pipeline_name}"
        if heading not in existing:
            lines.append(f"\n{heading}\n")

        lines.append(f"\n### {time_short} — {input_type}\n")
        if description:
            lines.append(f"{description}\n")
        if rel_img:
            lines.append(f"![[{rel_img}]]\n")
        elif input_type == "url":
            lines.append(f"[{content}]({content})\n")
        elif input_type == "text" and content != description:
            lines.append(f"> {content[:500]}\n")
        lines.append("")

        # Append to file
        with open(md_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))


class NoteSyncManager:
    """Unified sync interface."""

    def __init__(self, config: Config, workspace_dir: Optional[Path] = None) -> None:
        self.config = config
        self._local: Optional[LocalMarkdownSyncer] = None
        self._obsidian: Optional[ObsidianSyncer] = None

        if workspace_dir is not None:
            self._local = LocalMarkdownSyncer(workspace_dir)

        if config.note_sync_backend in ("obsidian", "both"):
            if config.obsidian_vault_path:
                self._obsidian = ObsidianSyncer(config.obsidian_vault_path)

    def sync_entry(self, workspace_meta, pipeline_name: str, entry) -> None:
        """Sync a single entry to configured backends. Fire-and-forget."""
        if isinstance(entry, dict):
            entry_data = entry
        else:
            entry_data = asdict(entry) if hasattr(entry, '__dataclass_fields__') else {
                "timestamp": entry.timestamp,
                "input_type": entry.input_type,
                "input_content": entry.input_content,
                "description": entry.description,
            }

        ws_id = workspace_meta.workspace_id
        ws_title = workspace_meta.title or ws_id

        image_path = None
        if entry_data["input_type"] == "image":
            image_path = entry_data["input_content"]

        # Local Markdown (always, when workspace_dir was provided)
        if self._local:
            try:
                self._local.sync_entry(
                    workspace_title=ws_title,
                    pipeline_name=pipeline_name,
                    timestamp=entry_data["timestamp"],
                    input_type=entry_data["input_type"],
                    description=entry_data["description"],
                    content=entry_data["input_content"],
                    image_path=image_path,
                )
            except Exception as e:
                logger.warning(f"Local markdown sync failed: {e}")

        # Obsidian
        if self._obsidian:
            try:
                self._obsidian.sync_entry(
                    workspace_id=ws_id,
                    workspace_title=ws_title,
                    pipeline_name=pipeline_name,
                    timestamp=entry_data["timestamp"],
                    input_type=entry_data["input_type"],
                    description=entry_data["description"],
                    content=entry_data["input_content"],
                    image_path=image_path,
                )
            except Exception as e:
                logger.warning(f"Obsidian sync failed: {e}")
