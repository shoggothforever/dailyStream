#!/usr/bin/env python3
"""Compress existing PNG screenshots in DailyStream workspaces to JPEG.

What it does
============

1. Walk one or more workspace roots, finding every
   ``pipelines/*/context.json`` file.
2. For each entry whose ``input_type == "image"`` and whose
   ``input_content`` points to a PNG file:

   a. Transcode the PNG to a same-named ``.jpg`` alongside it
      (resolution preserved, JPEG quality 85 by default).
   b. Rewrite the entry's ``input_content`` path to the new ``.jpg``.
   c. Delete the original PNG.

3. Re-render the per-pipeline ``stream.md`` and top-level
   ``stream.md`` so every link points at the new filenames.  This is
   done by invoking the same ``NoteSyncManager`` the core uses at
   capture time, so the output is byte-for-byte identical to what
   would be produced if the entries had been captured as JPEG in the
   first place.

Typical savings on retina screencaptures: **5-10× smaller**
(~3 MB PNG → ~400 KB JPEG), with no visible quality loss.

Safety
------

* Runs as a **dry-run by default** — nothing on disk changes unless
  you pass ``--commit``.
* On any transcode failure the original PNG is kept (the script logs
  a warning and leaves both the entry and the file untouched).
* ``--quality`` lets you trade space for fidelity (default 85).

Examples
--------

Dry-run against every workspace under the default root::

    python scripts/compress_existing_screenshots.py \\
        --root ~/.dailystream/workspaces

Commit, with quality 90 (less savings, slightly sharper)::

    python scripts/compress_existing_screenshots.py \\
        --root ~/Desktop/dailyStream \\
        --quality 90 \\
        --commit

Target a single workspace directly::

    python scripts/compress_existing_screenshots.py \\
        --workspace ~/Desktop/dailyStream/260404/清明 \\
        --commit
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Iterable

# Allow running directly from the repo without installing.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from dailystream.capture import _try_compress_to_jpeg  # noqa: E402
from dailystream.pipeline import (  # noqa: E402
    resolve_entry_path, PipelineManager,
)
from dailystream.workspace import WorkspaceManager  # noqa: E402
from dailystream.note_sync import NoteSyncManager  # noqa: E402
from dailystream.config import Config  # noqa: E402

logger = logging.getLogger("compress_existing")


def _iter_workspaces(root: Path) -> Iterable[Path]:
    """Yield every workspace directory under ``root``.

    A workspace is any directory containing ``workspace_meta.json``.
    Handles both the flat layout (``root/<ws>``) and the dated layout
    (``root/<yymmdd>/<ws>``).
    """
    if (root / "workspace_meta.json").exists():
        yield root
        return
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if (child / "workspace_meta.json").exists():
            yield child
            continue
        # Two-level: child is a date folder.
        for gc in sorted(child.iterdir()):
            if gc.is_dir() and (gc / "workspace_meta.json").exists():
                yield gc


def _compress_workspace(
    ws_dir: Path, quality: int, commit: bool,
) -> tuple[int, int, int]:
    """Compress every PNG entry in a workspace.

    Returns ``(converted, skipped, bytes_saved)``.  In dry-run mode
    nothing is written; the counts reflect what *would* happen.
    """
    pipelines_dir = ws_dir / "pipelines"
    if not pipelines_dir.exists():
        return 0, 0, 0

    converted = 0
    skipped = 0
    bytes_saved = 0
    changed_pipelines: set[str] = set()

    for ctx_path in sorted(pipelines_dir.glob("*/context.json")):
        pipeline_name = ctx_path.parent.name
        try:
            ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            logger.warning("skip %s: %s", ctx_path, e)
            continue

        entries = ctx.get("entries", [])
        dirty = False

        for entry in entries:
            if entry.get("input_type") != "image":
                continue
            raw_path = entry.get("input_content") or ""
            if not raw_path:
                continue

            abs_path = resolve_entry_path(ws_dir, raw_path)
            if abs_path.suffix.lower() != ".png":
                skipped += 1
                continue
            if not abs_path.exists():
                logger.warning(
                    "missing file, skipping: %s (pipeline=%s)",
                    abs_path, pipeline_name,
                )
                skipped += 1
                continue

            png_size = abs_path.stat().st_size
            jpeg_path = abs_path.with_suffix(".jpg")
            # If a same-named .jpg already exists we don't want to
            # clobber it; take it to mean a prior run handled this
            # entry already.
            if jpeg_path.exists():
                logger.info(
                    "%s already exists, just rewriting entry path",
                    jpeg_path,
                )
                if commit:
                    entry["input_content"] = _rewrite_path(
                        raw_path, abs_path, jpeg_path, ws_dir,
                    )
                    dirty = True
                converted += 1
                continue

            if commit:
                new_path = _try_compress_to_jpeg(abs_path, quality=quality)
                if new_path is None:
                    logger.warning("transcode failed: %s", abs_path)
                    skipped += 1
                    continue
                jpeg_size = new_path.stat().st_size
                bytes_saved += png_size - jpeg_size
                entry["input_content"] = _rewrite_path(
                    raw_path, abs_path, new_path, ws_dir,
                )
                dirty = True
                converted += 1
            else:
                # Dry-run: estimate savings at the configured quality.
                # We don't actually decode to avoid needing Pillow for
                # preview mode — use a rough 85%-saved heuristic for
                # screencapture-style PNGs, which matches real results
                # well enough for an "expected savings" number.
                est_jpeg = int(png_size * 0.15)
                bytes_saved += png_size - est_jpeg
                converted += 1

        if dirty:
            ctx_path.write_text(
                json.dumps(ctx, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            changed_pipelines.add(pipeline_name)

    # Re-render stream.md so links point at the new .jpg names.
    if commit and changed_pipelines:
        _rebuild_stream_md(ws_dir, changed_pipelines)

    return converted, skipped, bytes_saved


def _rewrite_path(
    raw: str, old_abs: Path, new_abs: Path, ws_dir: Path,
) -> str:
    """Preserve the rel-vs-abs style of the original entry path."""
    # If the original path was workspace-relative, keep it relative.
    try:
        if not Path(raw).is_absolute():
            return str(new_abs.relative_to(ws_dir).as_posix())
    except ValueError:
        pass
    # Absolute → return absolute with the new suffix.
    return str(new_abs)


def _rebuild_stream_md(ws_dir: Path, pipelines: set[str]) -> None:
    """Regenerate the per-pipeline + top-level stream.md files.

    Uses ``wm.load`` (pure read from disk) instead of ``wm.open`` so
    the user's *active* workspace state is never mutated by a batch
    compression run.
    """
    try:
        config = Config.load()
        wm = WorkspaceManager()
        if not wm.load(ws_dir):
            logger.warning("Could not load meta for %s", ws_dir)
            return
        pm = PipelineManager(ws_dir, config.screenshot_save_path)
        syncer = NoteSyncManager(config, workspace_dir=ws_dir)
        from dailystream.pipeline import PipelineEntry
        for pname in pipelines:
            pmeta = pm.get_pipeline_meta(pname)
            # Remove the stale per-pipeline file so sync_entry rewrites it.
            pfile = ws_dir / "pipelines" / pname / "stream.md"
            try:
                pfile.unlink(missing_ok=True)
            except OSError:
                pass
            for entry in pm.get_entries(pname):
                pe = PipelineEntry(
                    timestamp=entry["timestamp"],
                    input_type=entry["input_type"],
                    input_content=entry["input_content"],
                    description=entry.get("description", ""),
                )
                syncer.sync_entry(wm.meta, pname, pe, pipeline_meta=pmeta)
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not rebuild stream.md for %s: %s", ws_dir, e)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compress existing PNG screenshots to JPEG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--root", type=Path,
        help="Workspace root directory (scans all workspaces inside).",
    )
    group.add_argument(
        "--workspace", type=Path,
        help="Target a single workspace directory.",
    )
    parser.add_argument(
        "--quality", type=int, default=85,
        help="JPEG quality 1-100 (default: 85).",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually write changes (default: dry-run).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    workspaces: list[Path]
    if args.workspace:
        if not (args.workspace / "workspace_meta.json").exists():
            print(f"Not a workspace: {args.workspace}", file=sys.stderr)
            return 2
        workspaces = [args.workspace]
    else:
        if not args.root.exists():
            print(f"Root not found: {args.root}", file=sys.stderr)
            return 2
        workspaces = list(_iter_workspaces(args.root))

    if not workspaces:
        print("No workspaces found.")
        return 0

    total_conv = total_skip = total_saved = 0
    for ws in workspaces:
        conv, skip, saved = _compress_workspace(
            ws, quality=args.quality, commit=args.commit,
        )
        total_conv += conv
        total_skip += skip
        total_saved += saved
        print(
            f"{ws}: converted={conv} skipped={skip} "
            f"saved≈{saved / 1024 / 1024:.1f} MB"
        )

    banner = "COMMITTED" if args.commit else "DRY-RUN (no changes written)"
    print()
    print(f"[{banner}] total: converted={total_conv} skipped={total_skip} "
          f"saved≈{total_saved / 1024 / 1024:.1f} MB")
    if not args.commit:
        print("Re-run with --commit to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
