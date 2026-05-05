#!/usr/bin/env python3
"""Relocate one or more DailyStream workspaces to a new root directory.

What it does
============

1. Copy every workspace under ``--source`` into ``--dest`` (preserving
   the ``yymmdd/<title>/...`` two-level layout).
2. Rewrite each workspace's JSON state so that:

   * ``workspace_meta.json``'s ``workspace_path`` field reflects the
     new on-disk location.
   * Every ``pipelines/*/context.json`` entry whose ``input_content``
     is an absolute path inside the *old* workspace is rewritten to be
     workspace-*relative* (e.g. ``screenshots/foo.png``) — this is what
     makes the workspace portable for any future move.
   * Absolute paths pointing outside the old workspace are left alone
     (the user might have configured an external screenshot folder).

3. Verify every image referenced by an entry actually exists under the
   new location before deleting anything.
4. (Optional) Update ``~/.dailystream/state.json`` if its
   ``active_workspace_path`` referenced the old location.
5. Only when verification passes (and ``--commit`` was passed) does the
   script remove the original workspaces.  Without ``--commit`` it runs
   as a dry-run and prints a summary of what *would* happen.

Safety
------

* The script never writes inside the original ``--source`` location
  until the final cleanup step.
* The cleanup step is a plain ``shutil.rmtree`` — make sure you have a
  separate backup (e.g. the ``tar -czf`` snapshot the project README
  recommends) before running with ``--commit``.

Examples
--------

Dry-run (default)::

    python scripts/relocate_workspaces.py \\
        --source ~/.dailystream/workspaces \\
        --dest ~/Desktop/dailyStream

Commit (after reviewing the dry-run output)::

    python scripts/relocate_workspaces.py \\
        --source ~/.dailystream/workspaces \\
        --dest ~/Desktop/dailyStream \\
        --commit

"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


# ── data structures ─────────────────────────────────────────────────


@dataclass
class WorkspacePlan:
    src: Path
    dst: Path
    rewrites: list[tuple[Path, str, str]] = field(default_factory=list)
    # (json_path, old_value, new_value) — for the diff report
    missing_after_copy: list[Path] = field(default_factory=list)


# ── discovery ───────────────────────────────────────────────────────


def find_workspaces(root: Path) -> Iterator[Path]:
    """Yield every directory under ``root`` that contains a
    ``workspace_meta.json`` file."""
    if not root.is_dir():
        return
    for meta in root.rglob("workspace_meta.json"):
        yield meta.parent


# ── planning ────────────────────────────────────────────────────────


def _abs_path_inside(p: Path, root: Path) -> bool:
    """True if ``p`` (absolute) lives under ``root`` (absolute)."""
    try:
        p.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def plan_workspace(src_ws: Path, dst_ws: Path) -> WorkspacePlan:
    """Compute what changes a workspace move would entail without
    touching the filesystem."""
    plan = WorkspacePlan(src=src_ws, dst=dst_ws)

    # 1) workspace_meta.json — update workspace_path
    meta_file = src_ws / "workspace_meta.json"
    if meta_file.exists():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        old_wp = meta.get("workspace_path", "")
        new_wp = str(dst_ws)
        if old_wp != new_wp:
            plan.rewrites.append((meta_file, old_wp, new_wp))

    # 2) every pipeline's context.json
    pipelines_dir = src_ws / "pipelines"
    if pipelines_dir.is_dir():
        for ctx_file in pipelines_dir.glob("*/context.json"):
            try:
                ctx = json.loads(ctx_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            for entry in ctx.get("entries", []):
                if entry.get("input_type") != "image":
                    continue
                ic = entry.get("input_content", "")
                if not ic:
                    continue
                p = Path(ic)
                if not p.is_absolute():
                    continue  # already relative — nothing to do
                if not _abs_path_inside(p, src_ws):
                    # Outside the workspace (e.g. iCloud-shared folder).
                    # We must NOT rewrite this — the file isn't moving.
                    continue
                rel = p.resolve().relative_to(src_ws.resolve()).as_posix()
                plan.rewrites.append((ctx_file, ic, rel))

    return plan


# ── execution ───────────────────────────────────────────────────────


def execute_copy(src_ws: Path, dst_ws: Path) -> None:
    """Copy a whole workspace tree.  Refuses to overwrite an existing
    destination."""
    if dst_ws.exists():
        raise FileExistsError(
            f"destination already exists: {dst_ws} — refusing to overwrite",
        )
    dst_ws.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_ws, dst_ws, symlinks=True)


def apply_rewrites(plan: WorkspacePlan) -> None:
    """Apply JSON rewrites *inside the destination tree*.  Source files
    in ``plan.src`` are never touched."""
    src_root = plan.src.resolve()
    dst_root = plan.dst.resolve()

    # Bucket rewrites by (relative file path under workspace) so we can
    # apply all the changes for one file in a single read/write cycle.
    by_file: dict[Path, list[tuple[str, str]]] = {}
    for json_path, old, new in plan.rewrites:
        rel = json_path.resolve().relative_to(src_root)
        by_file.setdefault(rel, []).append((old, new))

    for rel_path, swaps in by_file.items():
        dst_file = dst_root / rel_path
        text = dst_file.read_text(encoding="utf-8")
        for old, new in swaps:
            # Use JSON-quoted replacement so we never accidentally hit a
            # substring inside another value.
            old_token = json.dumps(old)
            new_token = json.dumps(new)
            text = text.replace(old_token, new_token)
        dst_file.write_text(text, encoding="utf-8")


def verify(plan: WorkspacePlan) -> list[Path]:
    """Walk the *destination* workspace's context.json files and check
    every image entry's ``input_content`` resolves to an existing file
    relative to the new workspace root.  Returns a list of missing
    paths."""
    missing: list[Path] = []
    dst_root = plan.dst
    for ctx_file in (dst_root / "pipelines").glob("*/context.json"):
        try:
            ctx = json.loads(ctx_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        for entry in ctx.get("entries", []):
            if entry.get("input_type") != "image":
                continue
            ic = entry.get("input_content", "")
            if not ic:
                continue
            p = Path(ic)
            target = p if p.is_absolute() else (dst_root / p)
            if not target.exists():
                missing.append(target)
    return missing


# ── reporting ───────────────────────────────────────────────────────


def render_summary(plans: list[WorkspacePlan]) -> str:
    out: list[str] = []
    total_rewrites = sum(len(p.rewrites) for p in plans)
    out.append(
        f"Found {len(plans)} workspace(s); "
        f"{total_rewrites} JSON path rewrite(s) planned.\n"
    )
    for p in plans:
        out.append(f"• {p.src}")
        out.append(f"  → {p.dst}")
        out.append(f"    rewrites: {len(p.rewrites)}")
    return "\n".join(out)


# ── main ────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Move DailyStream workspaces to a new root and rewrite "
            "absolute screenshot paths into workspace-relative form."
        ),
    )
    parser.add_argument(
        "--source", required=True, type=Path,
        help="Existing workspace root (e.g. ~/.dailystream/workspaces)",
    )
    parser.add_argument(
        "--dest", required=True, type=Path,
        help="New workspace root (e.g. ~/Desktop/dailyStream)",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help=(
            "Actually perform the migration and remove the source "
            "workspaces.  Without this flag the script only prints "
            "what it *would* do."
        ),
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path.home() / ".dailystream" / "state.json",
        help=(
            "Path to ~/.dailystream/state.json — its "
            "``active_workspace_path`` will be rewritten if it points "
            "into ``--source``."
        ),
    )
    args = parser.parse_args(argv)

    src_root: Path = args.source.expanduser().resolve()
    dst_root: Path = args.dest.expanduser().resolve()

    if src_root == dst_root:
        print("source and destination are identical — nothing to do",
              file=sys.stderr)
        return 2

    if not src_root.is_dir():
        print(f"source is not a directory: {src_root}", file=sys.stderr)
        return 2

    # Build per-workspace plans
    workspaces = list(find_workspaces(src_root))
    if not workspaces:
        print(f"no workspaces found under {src_root}", file=sys.stderr)
        return 0

    plans: list[WorkspacePlan] = []
    for src_ws in workspaces:
        rel = src_ws.relative_to(src_root)
        dst_ws = dst_root / rel
        plans.append(plan_workspace(src_ws, dst_ws))

    print(render_summary(plans))

    if not args.commit:
        print("\n[dry-run] re-run with --commit to apply.")
        return 0

    # ── commit phase ────────────────────────────────────────────
    print("\n=== COMMIT ===")
    print("(1/4) copying workspaces …")
    for plan in plans:
        execute_copy(plan.src, plan.dst)
        print(f"  ✓ {plan.src.name} → {plan.dst}")

    print("(2/4) rewriting JSON files in the destination …")
    for plan in plans:
        apply_rewrites(plan)
        print(f"  ✓ {plan.dst}: {len(plan.rewrites)} rewrite(s)")

    print("(3/4) verifying every image entry resolves …")
    any_missing = False
    for plan in plans:
        missing = verify(plan)
        if missing:
            any_missing = True
            print(f"  ✗ {plan.dst} — {len(missing)} missing files:")
            for m in missing[:5]:
                print(f"      {m}")
            if len(missing) > 5:
                print(f"      … and {len(missing) - 5} more")
        else:
            print(f"  ✓ {plan.dst}")

    if any_missing:
        print(
            "\nABORT: some image entries failed to resolve in the new "
            "location.  The originals at --source were NOT removed.  "
            "Inspect the issues above, then either delete the partial "
            f"copies under {dst_root} and re-run, or fix the data and "
            "re-run with --commit again."
        )
        return 1

    print("(4/4) removing originals …")
    for plan in plans:
        shutil.rmtree(plan.src)
        print(f"  ✓ removed {plan.src}")

    # Clean up now-empty parent directories (e.g. ``yymmdd/`` shells
    # that held only the workspaces we just moved) plus any .DS_Store
    # junk macOS dropped there.
    for ds in src_root.rglob(".DS_Store"):
        try:
            ds.unlink()
        except OSError:
            pass
    for d in sorted(
        (p for p in src_root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts), reverse=True,
    ):
        try:
            d.rmdir()
        except OSError:
            pass  # non-empty — keep it

    # Patch ~/.dailystream/state.json if needed
    state_file: Path = args.state_file.expanduser()
    if state_file.exists():
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        active = data.get("active_workspace_path", "")
        if active and Path(active).resolve().is_relative_to(src_root):
            new_active = str(
                dst_root / Path(active).resolve().relative_to(src_root)
            )
            data["active_workspace_path"] = new_active
            state_file.write_text(json.dumps(data, indent=2),
                                  encoding="utf-8")
            print(f"  ✓ updated active_workspace_path in {state_file}")

    print("\nDONE.  Workspaces are now portable — feel free to move them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
