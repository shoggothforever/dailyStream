"""Note sync module for DailyStream — local Markdown and Obsidian.

The primary sync target is a Markdown file inside the workspace directory
itself (``stream.md``).  Images are referenced by relative path so the
whole workspace folder is self-contained and portable.

Obsidian sync is still supported as an optional secondary backend.
"""

import logging
import re
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .config import Config
from .templates import (
    build_context,
    render_entry,
    get_entry_templates,
    get_obsidian_templates,
)

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

    def __init__(self, workspace_dir: Path, config: Optional[Config] = None) -> None:
        self._ws_dir = workspace_dir
        self._md_path = workspace_dir / "stream.md"
        self._templates = get_entry_templates(
            config.entry_templates if config else None
        )

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
        """Insert an entry into ``stream.md`` under its pipeline section.

        Instead of blindly appending to the end of the file, the new entry
        is inserted at the **end of the matching ``## pipeline_name``
        section**.  This way switching between pipelines keeps each
        pipeline's entries grouped together.
        """
        # Build template context and render
        ctx = build_context(
            timestamp=timestamp,
            input_type=input_type,
            description=description,
            content=content,
            pipeline=pipeline_name,
            image_path=image_path,
            workspace_dir=self._ws_dir,
        )
        entry_block = render_entry(self._templates, ctx)

        # ----------------------------------------------------------
        # Insert into the correct position
        # ----------------------------------------------------------
        existing = ""
        if self._md_path.exists():
            existing = self._md_path.read_text(encoding="utf-8")

        heading = f"## {pipeline_name}"

        if not existing:
            # Brand-new file
            full = f"# {workspace_title}\n\n{heading}\n\n{entry_block}\n"
            self._md_path.write_text(full, encoding="utf-8")
            return

        if heading in existing:
            # Find the end of this pipeline's section.
            # A section ends right before the next ``## `` heading or at EOF.
            heading_pos = existing.index(heading)
            after_heading = heading_pos + len(heading)
            # Look for the next ## heading after this one
            next_heading = re.search(r'^## ', existing[after_heading:], re.MULTILINE)
            if next_heading:
                insert_pos = after_heading + next_heading.start()
                # Insert just before the next heading (keep a blank line)
                updated = (
                    existing[:insert_pos].rstrip("\n")
                    + "\n\n"
                    + entry_block
                    + "\n\n"
                    + existing[insert_pos:]
                )
            else:
                # This is the last section — append at end
                updated = existing.rstrip("\n") + "\n\n" + entry_block + "\n"
            self._md_path.write_text(updated, encoding="utf-8")
        else:
            # New pipeline — append the heading + entry at the end
            addition = f"\n{heading}\n\n{entry_block}\n"
            updated = existing.rstrip("\n") + "\n" + addition
            self._md_path.write_text(updated, encoding="utf-8")


class ObsidianSyncer:
    """Sync entries to Obsidian vault as Markdown.

    Structure: one workspace = one directory + one main .md file.
    Pipelines are separated by headings.
    """

    def __init__(self, vault_path: str, config: Optional[Config] = None) -> None:
        self.vault_path = Path(vault_path)
        self._templates = get_obsidian_templates(
            config.obsidian_templates if config else None
        )

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

        # Build template context and render
        ctx = build_context(
            timestamp=timestamp,
            input_type=input_type,
            description=description,
            content=content,
            pipeline=pipeline_name,
            image_path=image_path,
            obsidian_rel_img=rel_img,
        )
        entry_block = render_entry(self._templates, ctx)

        # Build file content
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

        lines.append(f"\n{entry_block}\n")

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
            self._local = LocalMarkdownSyncer(workspace_dir, config)

        if config.note_sync_backend in ("obsidian", "both"):
            if config.obsidian_vault_path:
                self._obsidian = ObsidianSyncer(config.obsidian_vault_path, config)

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
