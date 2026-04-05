"""Timeline report generator for DailyStream."""

from pathlib import Path
from typing import Optional

from .config import Config, read_json, SHORT_TIME_PATTERN
from .pipeline import PipelineManager
from .templates import build_context, render_entry, get_timeline_templates


def _load_ai_analyses(workspace_dir: Path, pipeline_name: str) -> dict:
    """Load AI analyses for a pipeline, indexed by entry_index.

    Returns a dict mapping ``entry_index`` → analysis dict.
    """
    path = workspace_dir / "pipelines" / pipeline_name / "ai_analyses.json"
    if not path.exists():
        return {}
    data = read_json(path)
    result = {}
    for a in data.get("analyses", []):
        idx = a.get("entry_index")
        if idx is not None and a.get("status") == "completed":
            result[idx] = a
    return result


def _load_daily_summary(workspace_dir: Path) -> Optional[dict]:
    """Load the workspace-level AI daily summary."""
    path = workspace_dir / "ai_daily_summary.json"
    if not path.exists():
        return None
    return read_json(path)


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

    ai_mode = getattr(workspace_meta, "ai_mode", "off") or "off"

    templates = get_timeline_templates(
        config.timeline_templates if config else None,
        ai_mode=ai_mode,
    )

    # Pre-load AI analyses per pipeline
    ai_analyses_by_pipeline: dict[str, dict] = {}
    if ai_mode != "off":
        for pname in pm.list_pipelines():
            ai_analyses_by_pipeline[pname] = _load_ai_analyses(
                workspace_dir, pname
            )

    # Build per-pipeline entry index tracking
    # We need to know the entry_index within its own pipeline
    _pipeline_entry_counters: dict[str, int] = {}

    lines = []
    title = workspace_meta.title or workspace_meta.workspace_id
    lines.append(f"# {title}\n")
    lines.append(f"**Created**: {workspace_meta.created_at}  ")
    lines.append(f"**Ended**: {workspace_meta.ended_at or 'ongoing'}  ")
    lines.append(f"**Pipelines**: {', '.join(workspace_meta.pipelines)}")
    if ai_mode != "off":
        lines.append(f"**AI Mode**: {ai_mode}")
    lines.append("")
    lines.append("---\n")

    # We need to compute the entry_index within each pipeline for AI lookup
    # First, build a mapping: (pipeline_name, timestamp) → entry_index
    pipeline_entries_order: dict[str, list[str]] = {}
    for pname in pm.list_pipelines():
        entries = pm.get_entries(pname)
        pipeline_entries_order[pname] = [
            e.get("timestamp", "") for e in entries
        ]

    def _get_entry_index(pipe: str, ts: str) -> int:
        """Find the index of an entry within its pipeline."""
        order = pipeline_entries_order.get(pipe, [])
        try:
            return order.index(ts)
        except ValueError:
            return -1

    # AI statistics tracking
    ai_categories: dict[str, int] = {}
    ai_elements_all: list[str] = []

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

        # Look up AI analysis
        ai_desc = ""
        ai_cat = ""
        ai_elems_str = ""
        if ai_mode != "off":
            entry_idx = _get_entry_index(pipe, ts)
            analyses = ai_analyses_by_pipeline.get(pipe, {})
            analysis = analyses.get(entry_idx)
            if analysis:
                ai_desc = analysis.get("description", "")
                ai_cat = analysis.get("category", "")
                ai_elems = analysis.get("key_elements", [])
                ai_elems_str = ", ".join(ai_elems) if ai_elems else ""
                # Track statistics
                if ai_cat:
                    ai_categories[ai_cat] = ai_categories.get(ai_cat, 0) + 1
                ai_elements_all.extend(ai_elems)

        ctx = build_context(
            timestamp=ts,
            input_type=itype,
            description=desc,
            content=content,
            pipeline=pipe,
            image_path=content if itype == "image" else None,
            content_max_len=200,
            ai_analysis=ai_desc,
            ai_category=ai_cat,
            ai_elements=ai_elems_str,
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

        # Show per-pipeline AI summary if available
        if ai_mode != "off":
            analyses_data = read_json(
                workspace_dir / "pipelines" / pname / "ai_analyses.json"
            )
            pipeline_summary = analyses_data.get("daily_summary")
            if pipeline_summary:
                lines.append(f"> **🤖 AI Summary**: {pipeline_summary}")
                lines.append("")

        for entry in entries:
            ts = entry.get("timestamp", "?")
            time_short = SHORT_TIME_PATTERN(ts)
            desc = entry.get("description", "")
            lines.append(f"- **{time_short}**: {desc or entry.get('input_type', '?')}")
        lines.append("")

    # AI Analysis Statistics (when AI is active)
    if ai_mode != "off" and ai_categories:
        lines.append("---\n")
        lines.append("## 📊 AI Analysis Statistics\n")
        lines.append("### Activity Categories\n")
        for cat, count in sorted(
            ai_categories.items(), key=lambda x: x[1], reverse=True
        ):
            bar = "█" * count
            lines.append(f"- **{cat}**: {count} {bar}")
        lines.append("")

        if ai_elements_all:
            # Top 10 most frequent elements
            from collections import Counter

            elem_counts = Counter(ai_elements_all).most_common(10)
            lines.append("### Key Elements (Top 10)\n")
            for elem, count in elem_counts:
                lines.append(f"- `{elem}` × {count}")
            lines.append("")

    # Daily Summary (daily_report mode)
    if ai_mode == "daily_report":
        summary_data = _load_daily_summary(workspace_dir)
        if summary_data:
            lines.append("---\n")
            lines.append("## 🤖 AI Daily Summary\n")
            overall = summary_data.get("overall_summary", "")
            if overall:
                lines.append(overall)
                lines.append("")

            psummaries = summary_data.get("pipeline_summaries", {})
            if psummaries:
                lines.append("### Per-Pipeline Summaries\n")
                for pname, ps in psummaries.items():
                    lines.append(f"- **{pname}**: {ps}")
                lines.append("")

            gen_at = summary_data.get("generated_at", "")
            model = summary_data.get("model", "")
            if gen_at or model:
                lines.append(
                    f"*Generated at {gen_at} using {model}*\n"
                )

    report_path = workspace_dir / "timeline_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
