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


# ---------------------------------------------------------------------------
# Shared helpers for structured (Daily Review) export
# ---------------------------------------------------------------------------


def _enrich_entry(
    entry: dict,
    workspace_dir: Path,
    ai_analyses: dict,
    entry_idx: int,
) -> dict:
    """Normalise a raw pipeline entry into the Swift-facing shape.

    - image ``input_content`` is resolved to an absolute path (the Swift
      ``NSImage(contentsOfFile:)`` API does not honour
      workspace-relative paths);
    - ai_* fields are populated from the matching AI analysis (when
      available).
    """
    itype = entry.get("input_type", "?")
    raw_content = entry.get("input_content", "")

    if itype == "image" and raw_content:
        resolved = str(resolve_entry_path(workspace_dir, raw_content))
    else:
        resolved = raw_content

    e: dict = {
        "timestamp": entry.get("timestamp", ""),
        "pipeline": entry.get("pipeline", "unknown"),
        "input_type": itype,
        "description": entry.get("description", ""),
        "input_content": resolved,
        "ai_description": "",
        "ai_category": "",
        "ai_elements": [],
    }

    analysis = ai_analyses.get(entry_idx) if ai_analyses else None
    if analysis:
        e["ai_description"] = analysis.get("description", "")
        e["ai_category"] = analysis.get("category", "")
        e["ai_elements"] = analysis.get("key_elements", [])
    return e


def _workspace_header(workspace_meta, ai_mode: str, pipeline_names: list[str]) -> dict:
    """The static ``workspace`` block shared by summary and full exports."""
    return {
        "workspace_id": getattr(workspace_meta, "workspace_id", ""),
        "title": getattr(workspace_meta, "title", None)
                 or getattr(workspace_meta, "workspace_id", ""),
        "created_at": getattr(workspace_meta, "created_at", ""),
        "ended_at": getattr(workspace_meta, "ended_at", None),
        "ai_mode": ai_mode,
        "pipelines": pipeline_names,
    }


def _daily_summary_block(workspace_dir: Path, ai_mode: str) -> Optional[dict]:
    if ai_mode != "daily_report":
        return None
    summary_data = _load_daily_summary(workspace_dir)
    if not summary_data:
        return None
    return {
        "overall_summary": summary_data.get("overall_summary", ""),
        "pipeline_summaries": summary_data.get("pipeline_summaries", {}),
        "generated_at": summary_data.get("generated_at", ""),
        "model": summary_data.get("model", ""),
    }


def _pipeline_entry_index_map(pm: PipelineManager, pipeline_name: str) -> dict:
    """Map ``timestamp → index-within-pipeline`` for AI-analysis lookup."""
    return {
        e.get("timestamp", ""): idx
        for idx, e in enumerate(pm.get_entries(pipeline_name))
    }


# ---------------------------------------------------------------------------
# New layered export API (consumed by AppState.endWorkspace)
# ---------------------------------------------------------------------------


def generate_summary(
    workspace_dir: Path,
    workspace_meta,
    config: Optional[Config] = None,
) -> Optional[dict]:
    """Lightweight payload: workspace header + stats + pipeline summaries
    + (optional) daily summary.  **Entries are NOT included** — they are
    streamed per-pipeline via :func:`generate_pipeline_entries`, letting
    the Daily Review window open instantly while the (potentially large)
    per-pipeline content loads in the background.

    Returns ``None`` when the workspace has no entries.
    """
    from collections import Counter

    pm = PipelineManager(workspace_dir)
    all_entries = pm.get_all_entries()
    if not all_entries:
        return None

    ai_mode = getattr(workspace_meta, "ai_mode", "off") or "off"
    pipeline_names = pm.list_pipelines()

    # Pre-load AI analyses once; reused by stats aggregation below.
    ai_analyses_by_pipeline: dict[str, dict] = {}
    if ai_mode != "off":
        for pname in pipeline_names:
            ai_analyses_by_pipeline[pname] = _load_ai_analyses(
                workspace_dir, pname
            )

    # Aggregate stats without materialising the enriched-entry list.
    type_counts: dict[str, int] = {}
    ai_categories: dict[str, int] = {}
    ai_elements_all: list[str] = []
    per_pipeline_counts: dict[str, int] = {p: 0 for p in pipeline_names}

    for pname in pipeline_names:
        entries = pm.get_entries(pname)
        per_pipeline_counts[pname] = len(entries)
        analyses = ai_analyses_by_pipeline.get(pname, {})
        for idx, entry in enumerate(entries):
            itype = entry.get("input_type", "?")
            type_counts[itype] = type_counts.get(itype, 0) + 1
            analysis = analyses.get(idx) if analyses else None
            if analysis:
                cat = analysis.get("category", "")
                if cat:
                    ai_categories[cat] = ai_categories.get(cat, 0) + 1
                ai_elements_all.extend(analysis.get("key_elements", []) or [])

    pipeline_summaries: list[dict] = []
    for pname in pipeline_names:
        meta = pm.get_pipeline_meta(pname)
        pipeline_summaries.append({
            "name": pname,
            "entry_count": per_pipeline_counts.get(pname, 0),
            "description": meta.get("description", ""),
            "goal": meta.get("goal", ""),
        })

    elem_counts = Counter(ai_elements_all).most_common(10)

    return {
        "workspace": _workspace_header(workspace_meta, ai_mode, pipeline_names),
        "stats": {
            "total_entries": len(all_entries),
            "type_counts": type_counts,
            "pipeline_count": len(pipeline_summaries),
            "ai_categories": ai_categories,
            "top_elements": [
                {"name": n, "count": c} for n, c in elem_counts
            ],
        },
        "pipeline_summaries": pipeline_summaries,
        "daily_summary": _daily_summary_block(workspace_dir, ai_mode),
    }


def generate_pipeline_entries(
    workspace_dir: Path,
    workspace_meta,
    pipeline_name: str,
    config: Optional[Config] = None,
) -> dict:
    """Return ``{"pipeline": name, "entries": [...]}`` for a single
    pipeline, in chronological order, with image paths resolved to
    absolute and AI fields populated.

    Unknown pipelines return an empty ``entries`` list instead of
    raising, so the Swift VM can treat "no data yet" uniformly.
    """
    pm = PipelineManager(workspace_dir)
    pipeline_names = pm.list_pipelines()
    if pipeline_name not in pipeline_names:
        return {"pipeline": pipeline_name, "entries": []}

    ai_mode = getattr(workspace_meta, "ai_mode", "off") or "off"
    ai_analyses: dict = {}
    if ai_mode != "off":
        ai_analyses = _load_ai_analyses(workspace_dir, pipeline_name)

    raw_entries = pm.get_entries(pipeline_name)
    enriched = [
        _enrich_entry(
            {**e, "pipeline": pipeline_name},
            workspace_dir,
            ai_analyses,
            idx,
        )
        for idx, e in enumerate(raw_entries)
    ]
    return {"pipeline": pipeline_name, "entries": enriched}


# ---------------------------------------------------------------------------
# Legacy full-payload export (kept for backward compatibility)
# ---------------------------------------------------------------------------


def generate_structured(
    workspace_dir: Path,
    workspace_meta,
    config: Optional[Config] = None,
) -> Optional[dict]:
    """Full structured export consumed by the Swift Daily Review window
    via ``timeline.export_structured``.  Equivalent to
    :func:`generate_summary` + all entries from every pipeline merged
    into a single time-sorted list.

    This is the legacy shape — kept for backward compatibility and for
    the ``showDailyReview`` path that doesn't benefit from splitting.
    Newer flows should prefer :func:`generate_summary` +
    :func:`generate_pipeline_entries`.

    Returns ``None`` when the workspace has no entries.
    """
    pm = PipelineManager(workspace_dir)
    all_entries = pm.get_all_entries()
    if not all_entries:
        return None

    summary = generate_summary(workspace_dir, workspace_meta, config=config)
    if summary is None:  # defensive — mirrors the early return above
        return None

    # Expand entries for every pipeline, in time order (all_entries is
    # already sorted globally by PipelineManager).
    ai_mode = summary["workspace"]["ai_mode"]
    ai_analyses_by_pipeline: dict[str, dict] = {}
    if ai_mode != "off":
        for pname in pm.list_pipelines():
            ai_analyses_by_pipeline[pname] = _load_ai_analyses(
                workspace_dir, pname
            )

    # Per-pipeline ts→index map so AI lookup is O(1).
    ts_to_idx_per_pipeline: dict[str, dict] = {
        pname: _pipeline_entry_index_map(pm, pname)
        for pname in pm.list_pipelines()
    }

    enriched_entries: list[dict] = []
    for entry in all_entries:
        pipe = entry.get("pipeline", "unknown")
        ts = entry.get("timestamp", "")
        entry_idx = ts_to_idx_per_pipeline.get(pipe, {}).get(ts, -1)
        analyses = ai_analyses_by_pipeline.get(pipe, {})
        enriched_entries.append(
            _enrich_entry(entry, workspace_dir, analyses, entry_idx)
        )

    return {
        **summary,
        "entries": enriched_entries,
    }
