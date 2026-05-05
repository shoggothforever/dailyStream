"""Timeline report generator for DailyStream."""

from pathlib import Path
from typing import Optional

from .config import Config, read_json, SHORT_TIME_PATTERN
from .pipeline import PipelineManager, resolve_entry_path
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
            # Resolve workspace-relative image paths to absolute so that
            # ``build_context`` can compute a correct link relative to
            # ``image_base_dir`` (= workspace root, where this
            # ``timeline_report.md`` file lives).
            image_path=(
                str(resolve_entry_path(workspace_dir, content))
                if itype == "image" and content
                else None
            ),
            image_base_dir=workspace_dir,
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


def generate_structured(
    workspace_dir: Path,
    workspace_meta,
    config: Optional[Config] = None,
) -> Optional[dict]:
    """Generate a structured JSON representation of the workspace timeline.

    This is consumed by the Swift Daily Review window (M4) via the
    ``timeline.export_structured`` RPC method.  It provides all the data
    needed for the Hero / StatsStrip / TimelineWaterfall views.

    Returns ``None`` when the workspace has no entries.
    """
    from dataclasses import asdict as _asdict
    from collections import Counter

    pm = PipelineManager(workspace_dir)
    all_entries = pm.get_all_entries()

    if not all_entries:
        return None

    ai_mode = getattr(workspace_meta, "ai_mode", "off") or "off"

    # Pre-load AI analyses per pipeline
    ai_analyses_by_pipeline: dict[str, dict] = {}
    if ai_mode != "off":
        for pname in pm.list_pipelines():
            ai_analyses_by_pipeline[pname] = _load_ai_analyses(
                workspace_dir, pname
            )

    # Build per-pipeline entry index order for AI lookup
    pipeline_entries_order: dict[str, list[str]] = {}
    for pname in pm.list_pipelines():
        entries = pm.get_entries(pname)
        pipeline_entries_order[pname] = [
            e.get("timestamp", "") for e in entries
        ]

    def _get_entry_index(pipe: str, ts: str) -> int:
        order = pipeline_entries_order.get(pipe, [])
        try:
            return order.index(ts)
        except ValueError:
            return -1

    # Stats
    ai_categories: dict[str, int] = {}
    ai_elements_all: list[str] = []
    type_counts: dict[str, int] = {}

    enriched_entries: list[dict] = []
    for entry in all_entries:
        ts = entry.get("timestamp", "")
        pipe = entry.get("pipeline", "unknown")
        itype = entry.get("input_type", "?")
        type_counts[itype] = type_counts.get(itype, 0) + 1

        # For image entries ``input_content`` is a workspace-relative
        # path (newer data) or an absolute path (legacy).  Swift clients
        # consume this via ``NSImage(contentsOfFile:)`` which needs an
        # absolute filesystem path, so we always resolve it here.
        raw_content = entry.get("input_content", "")
        if itype == "image" and raw_content:
            resolved = str(resolve_entry_path(workspace_dir, raw_content))
        else:
            resolved = raw_content

        e: dict = {
            "timestamp": ts,
            "pipeline": pipe,
            "input_type": itype,
            "description": entry.get("description", ""),
            "input_content": resolved,
            "ai_description": "",
            "ai_category": "",
            "ai_elements": [],
        }

        if ai_mode != "off":
            entry_idx = _get_entry_index(pipe, ts)
            analyses = ai_analyses_by_pipeline.get(pipe, {})
            analysis = analyses.get(entry_idx)
            if analysis:
                e["ai_description"] = analysis.get("description", "")
                e["ai_category"] = analysis.get("category", "")
                e["ai_elements"] = analysis.get("key_elements", [])
                cat = e["ai_category"]
                if cat:
                    ai_categories[cat] = ai_categories.get(cat, 0) + 1
                ai_elements_all.extend(e["ai_elements"])

        enriched_entries.append(e)

    # Pipeline summaries
    pipeline_summaries: list[dict] = []
    for pname in pm.list_pipelines():
        meta = pm.get_pipeline_meta(pname)
        pcount = sum(1 for e in enriched_entries if e["pipeline"] == pname)
        ps: dict = {
            "name": pname,
            "entry_count": pcount,
            "description": meta.get("description", ""),
            "goal": meta.get("goal", ""),
        }
        pipeline_summaries.append(ps)

    # Daily summary (daily_report mode)
    daily_summary = None
    if ai_mode == "daily_report":
        summary_data = _load_daily_summary(workspace_dir)
        if summary_data:
            daily_summary = {
                "overall_summary": summary_data.get("overall_summary", ""),
                "pipeline_summaries": summary_data.get("pipeline_summaries", {}),
                "generated_at": summary_data.get("generated_at", ""),
                "model": summary_data.get("model", ""),
            }

    # Top elements
    elem_counts = Counter(ai_elements_all).most_common(10)

    return {
        "workspace": {
            "workspace_id": getattr(workspace_meta, "workspace_id", ""),
            "title": getattr(workspace_meta, "title", None)
                     or getattr(workspace_meta, "workspace_id", ""),
            "created_at": getattr(workspace_meta, "created_at", ""),
            "ended_at": getattr(workspace_meta, "ended_at", None),
            "ai_mode": ai_mode,
            "pipelines": [p.get("name", "") for p in pipeline_summaries],
        },
        "stats": {
            "total_entries": len(enriched_entries),
            "type_counts": type_counts,
            "pipeline_count": len(pipeline_summaries),
            "ai_categories": ai_categories,
            "top_elements": [
                {"name": name, "count": count} for name, count in elem_counts
            ],
        },
        "entries": enriched_entries,
        "pipeline_summaries": pipeline_summaries,
        "daily_summary": daily_summary,
    }
