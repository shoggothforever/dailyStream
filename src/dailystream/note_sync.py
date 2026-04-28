"""Note sync module for DailyStream — local Markdown and Obsidian.

Storage layout (since v2):

    <workspace>/
        stream.md                          ← TOP-LEVEL INDEX (pure index page,
                                             rebuilt lazily on pipeline
                                             create/delete/rename and on
                                             workspace open)
        pipelines/
            <pipeline_name>/
                context.json
                stream.md                  ← all entries for this pipeline
        screenshots/
            ...                            ← workspace-level, shared by all
                                             pipelines; referenced from
                                             pipeline stream.md as
                                             ``../../screenshots/foo.png``

Rationale: a single monolithic ``stream.md`` grew unbounded as captures
accumulated, which made both the Markdown viewer and delete/update
regeneration slow. Splitting per-pipeline keeps each file short while
keeping a discoverable top-level overview.

Obsidian sync is still supported as an optional secondary backend and
remains a single file per workspace (unchanged).
"""

from __future__ import annotations

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

# ------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------

INDEX_FILENAME = "stream.md"
PIPELINE_STREAM_FILENAME = "stream.md"
PIPELINES_SUBDIR = "pipelines"
INDEX_BACKUP_SUFFIX = ".legacy.bak"


def _pipeline_stream_path(workspace_dir: Path, pipeline_name: str) -> Path:
    """Return the per-pipeline ``stream.md`` path."""
    return workspace_dir / PIPELINES_SUBDIR / pipeline_name / PIPELINE_STREAM_FILENAME


def _index_path(workspace_dir: Path) -> Path:
    """Return the top-level workspace index ``stream.md`` path."""
    return workspace_dir / INDEX_FILENAME


# ------------------------------------------------------------------
# Per-pipeline syncer
# ------------------------------------------------------------------

class LocalMarkdownSyncer:
    """Sync entries to a per-pipeline ``stream.md`` inside
    ``<workspace>/pipelines/<pipeline_name>/``.

    The top-level ``<workspace>/stream.md`` is *not* touched here — it is
    handled by :class:`WorkspaceIndexSyncer` and rebuilt on pipeline
    lifecycle events (create / delete / rename / workspace open).
    """

    def __init__(self, workspace_dir: Path, config: Optional[Config] = None) -> None:
        self._ws_dir = workspace_dir
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
        pipeline_meta: Optional[dict] = None,
    ) -> None:
        """Append an entry to ``pipelines/<pipeline_name>/stream.md``.

        Parameters
        ----------
        workspace_title
            Kept for backward-compat; the per-pipeline file uses the
            pipeline's own name/emoji/meta as its title instead.
        pipeline_meta
            Optional dict with ``description`` and ``goal`` keys.
            When the per-pipeline file is first created, these render
            as a brief info block right after the heading.
        """
        pipeline_md = _pipeline_stream_path(self._ws_dir, pipeline_name)
        pipeline_md.parent.mkdir(parents=True, exist_ok=True)

        # Image paths are rendered relative to the pipeline md's directory
        # so the file is self-contained when shared out of the workspace.
        ctx = build_context(
            timestamp=timestamp,
            input_type=input_type,
            description=description,
            content=content,
            pipeline=pipeline_name,
            image_path=image_path,
            workspace_dir=self._ws_dir,
            image_base_dir=pipeline_md.parent,
        )
        entry_block = render_entry(self._templates, ctx)

        if not pipeline_md.exists():
            header = self._format_pipeline_header(pipeline_name, pipeline_meta)
            full = header + "\n" + entry_block + "\n"
            pipeline_md.write_text(full, encoding="utf-8")
            return

        existing = pipeline_md.read_text(encoding="utf-8")
        updated = existing.rstrip("\n") + "\n\n" + entry_block + "\n"
        pipeline_md.write_text(updated, encoding="utf-8")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_pipeline_header(
        pipeline_name: str, meta: Optional[dict]
    ) -> str:
        """Render the heading + optional info block for a pipeline file."""
        lines: list[str] = [f"# {pipeline_name}", ""]
        if meta:
            desc = meta.get("description", "")
            goal = meta.get("goal", "")
            if desc:
                lines.append(f"> **📝 Description**: {desc}")
            if goal:
                lines.append(f"> **🎯 Goal**: {goal}")
            if desc or goal:
                lines.append("")
                lines.append("---")
                lines.append("")
        return "\n".join(lines)


# ------------------------------------------------------------------
# Top-level index syncer
# ------------------------------------------------------------------

class WorkspaceIndexSyncer:
    """Rebuild the top-level ``<workspace>/stream.md`` as a pure index page
    that links to each pipeline's own ``stream.md``.

    The index is intentionally lazy: it only needs to be regenerated when
    the set of pipelines changes (pipeline.create / delete / rename) or
    when a workspace is opened. For individual entry additions, only the
    per-pipeline file is touched.
    """

    def __init__(self, workspace_dir: Path) -> None:
        self._ws_dir = workspace_dir

    def rebuild(
        self,
        workspace_title: str,
        pipelines: list[dict],
    ) -> None:
        """Regenerate the top-level index page from scratch.

        Parameters
        ----------
        workspace_title
            Rendered as the ``# {title}`` heading.
        pipelines
            Ordered list of dicts; each dict should provide:
              - ``name``        (required)
              - ``entry_count`` (int, optional — shown as a suffix)
              - ``description`` (str, optional)
              - ``goal``        (str, optional)
        """
        index_md = _index_path(self._ws_dir)
        index_md.parent.mkdir(parents=True, exist_ok=True)

        lines: list[str] = []
        title = workspace_title.strip() or self._ws_dir.name
        lines.append(f"# {title}")
        lines.append("")

        if not pipelines:
            lines.append("_No pipelines yet._")
            lines.append("")
            index_md.write_text("\n".join(lines), encoding="utf-8")
            return

        lines.append("## 📋 Pipelines")
        lines.append("")
        for p in pipelines:
            name = p.get("name", "")
            if not name:
                continue
            entry_count = p.get("entry_count", 0)
            # Encode the link target so spaces / unicode names work in
            # both the native viewer and generic Markdown readers.
            from urllib.parse import quote as url_quote
            href = f"{PIPELINES_SUBDIR}/{url_quote(name, safe='')}/{PIPELINE_STREAM_FILENAME}"
            count_suffix = f" · {entry_count} entr{'y' if entry_count == 1 else 'ies'}"
            line = f"- [{name}]({href}){count_suffix}"
            lines.append(line)

            # Optional meta block, indented under the bullet
            desc = (p.get("description") or "").strip()
            goal = (p.get("goal") or "").strip()
            if desc:
                lines.append(f"    - 📝 {desc}")
            if goal:
                lines.append(f"    - 🎯 {goal}")
        lines.append("")

        index_md.write_text("\n".join(lines), encoding="utf-8")

    def remove_legacy_monolith(self) -> None:
        """No-op placeholder — kept for symmetry; actual migration lives
        in :func:`migrate_monolithic_stream_md`.
        """


# ------------------------------------------------------------------
# Migration: monolithic stream.md → per-pipeline files
# ------------------------------------------------------------------

_HEADING_RE = re.compile(r"^## (?P<name>.+?)\s*$", re.MULTILINE)


def migrate_monolithic_stream_md(workspace_dir: Path) -> bool:
    """Detect a legacy single-file ``stream.md`` and split its ``## pipeline``
    sections into per-pipeline files under ``pipelines/<name>/stream.md``.

    Returns True if a migration was performed, False if nothing to do.

    Safety:
        * Only triggers when the top-level ``stream.md`` contains at least
          one ``## `` heading AND **no** pipeline already has its own
          ``stream.md`` file.
        * The original file is renamed to ``stream.md.legacy.bak`` instead
          of being deleted, so users can recover if anything goes wrong.
    """
    index_md = _index_path(workspace_dir)
    if not index_md.exists():
        return False

    pipelines_dir = workspace_dir / PIPELINES_SUBDIR
    # If any pipeline already has its own stream.md, assume migration
    # was done previously (or the workspace is new).
    if pipelines_dir.exists():
        for sub in pipelines_dir.iterdir():
            if sub.is_dir() and (sub / PIPELINE_STREAM_FILENAME).exists():
                return False

    try:
        raw = index_md.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        logger.warning("migrate: failed to read %s", index_md)
        return False

    headings = list(_HEADING_RE.finditer(raw))
    if not headings:
        return False

    logger.info(
        "migrate: splitting legacy %s into %d pipeline files",
        index_md, len(headings),
    )

    # Extract each section body (from the line after its heading up to
    # the line before the next heading, or EOF).
    for idx, m in enumerate(headings):
        name = m.group("name").strip()
        body_start = m.end()
        body_end = headings[idx + 1].start() if idx + 1 < len(headings) else len(raw)
        body = raw[body_start:body_end].strip("\n")

        # Rewrite image paths: legacy files used
        # ``screenshots/foo.png`` (relative to workspace root). The new
        # per-pipeline file lives two levels deeper, so prepend ``../../``.
        body = re.sub(
            r"(!\[[^\]]*\]\()(screenshots/)",
            r"\1../../\2",
            body,
        )

        target = _pipeline_stream_path(workspace_dir, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        header = f"# {name}\n\n"
        target.write_text(header + body + "\n", encoding="utf-8")

    # Back up the old monolith so users can recover if desired.
    backup = index_md.with_suffix(index_md.suffix + INDEX_BACKUP_SUFFIX)
    try:
        index_md.rename(backup)
        logger.info("migrate: backed up legacy index → %s", backup.name)
    except Exception:  # noqa: BLE001
        logger.warning("migrate: could not rename legacy index to %s", backup)
    return True


# ------------------------------------------------------------------
# Obsidian syncer (unchanged — still one file per workspace)
# ------------------------------------------------------------------

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


# ------------------------------------------------------------------
# Unified facade
# ------------------------------------------------------------------

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

    def sync_entry(
        self,
        workspace_meta,
        pipeline_name: str,
        entry,
        pipeline_meta: Optional[dict] = None,
    ) -> None:
        """Sync a single entry to configured backends. Fire-and-forget.

        Parameters
        ----------
        pipeline_meta
            Optional dict with ``description`` and ``goal`` keys.
            Passed through to ``LocalMarkdownSyncer`` so that the
            per-pipeline stream file's header includes the info block on
            first creation.
        """
        if isinstance(entry, dict):
            entry_data = entry
        else:
            entry_data = asdict(entry) if hasattr(entry, "__dataclass_fields__") else {
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
                    pipeline_meta=pipeline_meta,
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
