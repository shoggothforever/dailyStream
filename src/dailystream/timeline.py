"""Timeline report generator for DailyStream."""

from pathlib import Path
from typing import Optional

from .config import Config, read_json, SHORT_TIME_PATTERN
from .pipeline import PipelineManager
from .templates import build_context, render_entry, get_timeline_templates


def generate_timeline(
    workspace_dir: Path,
    workspace_meta,
    config: Optional[Config] = None,
) -> Optional[Path]:
    """Generate a Markdown timeline report for the workspace.

    Returns path to the generated report file.
    """
    pm = PipelineManager(workspace_dir)
    all_entries = pm.get_all_entries()

    if not all_entries:
        return None

    templates = get_timeline_templates(
        config.timeline_templates if config else None
    )

    lines = []
    title = workspace_meta.title or workspace_meta.workspace_id
    lines.append(f"# {title}\n")
    lines.append(f"**Created**: {workspace_meta.created_at}  ")
    lines.append(f"**Ended**: {workspace_meta.ended_at or 'ongoing'}  ")
    lines.append(f"**Pipelines**: {', '.join(workspace_meta.pipelines)}\n")
    lines.append("---\n")

    # Group by pipeline
    pipelines_data: dict[str, list[dict]] = {}
    for entry in all_entries:
        p = entry.get("pipeline", "unknown")
        pipelines_data.setdefault(p, []).append(entry)

    # Timeline: all entries sorted by time
    lines.append("## Timeline\n")
    for entry in all_entries:
        ts = entry.get("timestamp", "?")
        pipe = entry.get("pipeline", "?")
        itype = entry.get("input_type", "?")
        desc = entry.get("description", "")
        content = entry.get("input_content", "")

        ctx = build_context(
            timestamp=ts,
            input_type=itype,
            description=desc,
            content=content,
            pipeline=pipe,
            image_path=content if itype == "image" else None,
            content_max_len=200,
        )
        entry_block = render_entry(templates, ctx)
        lines.append(entry_block)
        lines.append("")

    # Per-pipeline summary
    lines.append("---\n")
    lines.append("## By Pipeline\n")
    for pname, entries in pipelines_data.items():
        lines.append(f"### {pname} ({len(entries)} entries)\n")
        # Show pipeline description and goal if available
        meta = pm.get_pipeline_meta(pname)
        if meta.get("description"):
            lines.append(f"> **📝 Description**: {meta['description']}")
        if meta.get("goal"):
            lines.append(f"> **🎯 Goal**: {meta['goal']}")
        if meta.get("description") or meta.get("goal"):
            lines.append("")
        for entry in entries:
            ts = entry.get("timestamp", "?")
            time_short = SHORT_TIME_PATTERN(ts)
            desc = entry.get("description", "")
            lines.append(f"- **{time_short}**: {desc or entry.get('input_type', '?')}")
        lines.append("")

    report_path = workspace_dir / "timeline_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
