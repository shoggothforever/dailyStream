"""Timeline report generator for DailyStream."""

from pathlib import Path
from typing import Optional

from .pipeline import PipelineManager


def generate_timeline(workspace_dir: Path, workspace_meta) -> Optional[Path]:
    """Generate a Markdown timeline report for the workspace.

    Returns path to the generated report file.
    """
    pm = PipelineManager(workspace_dir)
    all_entries = pm.get_all_entries()

    if not all_entries:
        return None

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

        # Format timestamp (show just time portion)
        time_short = ts.split("T")[1][:8] if "T" in ts else ts

        lines.append(f"### {time_short} — [{pipe}] ({itype})\n")
        if desc:
            lines.append(f"{desc}\n")
        if itype == "image":
            # Relative path for image
            lines.append(f"![screenshot]({content})\n")
        elif itype == "url":
            lines.append(f"[{content}]({content})\n")
        elif content and content != desc:
            lines.append(f"> {content[:200]}\n")
        lines.append("")

    # Per-pipeline summary
    lines.append("---\n")
    lines.append("## By Pipeline\n")
    for pname, entries in pipelines_data.items():
        lines.append(f"### {pname} ({len(entries)} entries)\n")
        for entry in entries:
            ts = entry.get("timestamp", "?")
            time_short = ts.split("T")[1][:8] if "T" in ts else ts
            desc = entry.get("description", "")
            lines.append(f"- **{time_short}**: {desc or entry.get('input_type', '?')}")
        lines.append("")

    report_path = workspace_dir / "timeline_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
