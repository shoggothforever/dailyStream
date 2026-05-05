"""Tests for the layered timeline export API.

Covers the split introduced to unblock Daily Review window opening:

1. :func:`timeline.generate_summary` returns a lightweight payload —
   stats, pipeline list, AI rollups — but **no per-entry data**, so the
   Swift UI can render the hero / stats strip instantly.
2. :func:`timeline.generate_pipeline_entries` returns only the entries
   for one pipeline, with images resolved to absolute paths.
3. :func:`timeline.generate_structured` (legacy full-payload API) keeps
   producing the same shape it always has, so any unchanged caller
   (e.g. the manual ``showDailyReview`` path) sees no regression.
"""

from pathlib import Path

import pytest


# ── helpers ──────────────────────────────────────────────────────────


def _make_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return path


@pytest.fixture()
def populated_workspace(workspace_with_pipeline):
    """Workspace with one pipeline pre-populated with a mix of entries."""
    wm, pm, pipe = workspace_with_pipeline
    ws = wm.workspace_dir

    # Add a second pipeline so we can test per-pipeline isolation.
    pm.create("docs", description="doc pipeline", goal="write docs")
    wm.add_pipeline("docs")

    # pipe: two images + one url + one text
    _make_png(ws / "screenshots" / "a.png")
    _make_png(ws / "screenshots" / "b.png")
    pm.add_entry(pipe, "image", str(ws / "screenshots" / "a.png"), "shot A")
    pm.add_entry(pipe, "image", str(ws / "screenshots" / "b.png"), "shot B")
    pm.add_entry(pipe, "url", "https://example.com", "link")
    pm.add_entry(pipe, "text", "a note", "note")

    # docs: just one url
    pm.add_entry("docs", "url", "https://docs.local", "doc link")

    return wm, pm, pipe


# ── generate_summary ─────────────────────────────────────────────────


class TestGenerateSummary:
    def test_none_when_no_entries(self, workspace_with_pipeline):
        from dailystream.timeline import generate_summary

        wm, _, _ = workspace_with_pipeline
        assert generate_summary(wm.workspace_dir, wm.meta) is None

    def test_has_stats_without_entries_field(self, populated_workspace):
        from dailystream.timeline import generate_summary

        wm, _, _ = populated_workspace
        data = generate_summary(wm.workspace_dir, wm.meta)

        assert data is not None
        # Summary must NOT carry per-entry data — that's the whole point.
        assert "entries" not in data

        stats = data["stats"]
        assert stats["total_entries"] == 5   # 4 + 1
        assert stats["type_counts"]["image"] == 2
        assert stats["type_counts"]["url"] == 2
        assert stats["type_counts"]["text"] == 1
        assert stats["pipeline_count"] == 2

    def test_pipeline_summaries_match_pipeline_list(self, populated_workspace):
        from dailystream.timeline import generate_summary

        wm, _, pipe = populated_workspace
        data = generate_summary(wm.workspace_dir, wm.meta)

        names = {p["name"]: p for p in data["pipeline_summaries"]}
        assert set(names) == {pipe, "docs"}
        assert names[pipe]["entry_count"] == 4
        assert names["docs"]["entry_count"] == 1
        assert names["docs"]["goal"] == "write docs"

    def test_workspace_header_includes_title_and_ai_mode(self, populated_workspace):
        from dailystream.timeline import generate_summary

        wm, _, _ = populated_workspace
        data = generate_summary(wm.workspace_dir, wm.meta)
        header = data["workspace"]
        assert header["title"]               # non-empty
        assert header["ai_mode"] == "off"
        assert set(header["pipelines"]) >= {"docs"}


# ── generate_pipeline_entries ────────────────────────────────────────


class TestGeneratePipelineEntries:
    def test_returns_only_requested_pipeline(self, populated_workspace):
        from dailystream.timeline import generate_pipeline_entries

        wm, _, pipe = populated_workspace
        result = generate_pipeline_entries(wm.workspace_dir, wm.meta, pipe)

        assert result["pipeline"] == pipe
        assert len(result["entries"]) == 4
        for e in result["entries"]:
            assert e["pipeline"] == pipe

    def test_image_input_content_is_absolute(self, populated_workspace):
        """Swift NSImage(contentsOfFile:) cannot resolve relative paths,
        so the wire format MUST expose absolute paths for image entries.
        """
        from dailystream.timeline import generate_pipeline_entries

        wm, _, pipe = populated_workspace
        result = generate_pipeline_entries(wm.workspace_dir, wm.meta, pipe)

        images = [e for e in result["entries"] if e["input_type"] == "image"]
        assert len(images) == 2
        for e in images:
            p = Path(e["input_content"])
            assert p.is_absolute(), f"expected absolute, got {p!r}"
            assert p.exists()

    def test_unknown_pipeline_returns_empty(self, populated_workspace):
        """Unknown pipeline names return empty entries rather than
        raising, so the Swift VM can treat 'not yet loaded / deleted'
        uniformly."""
        from dailystream.timeline import generate_pipeline_entries

        wm, _, _ = populated_workspace
        result = generate_pipeline_entries(
            wm.workspace_dir, wm.meta, "does-not-exist"
        )
        assert result == {"pipeline": "does-not-exist", "entries": []}

    def test_url_and_text_pass_through_verbatim(self, populated_workspace):
        """Non-image input_content must not be mangled."""
        from dailystream.timeline import generate_pipeline_entries

        wm, _, pipe = populated_workspace
        result = generate_pipeline_entries(wm.workspace_dir, wm.meta, pipe)

        kinds = {(e["input_type"], e["input_content"]) for e in result["entries"]}
        assert ("url", "https://example.com") in kinds
        assert ("text", "a note") in kinds


# ── legacy generate_structured (backward compatibility) ──────────────


class TestGenerateStructuredBackwardCompatible:
    """The legacy full-payload API must keep its shape so callers such
    as the manual ``showDailyReview`` path continue to decode cleanly.
    """

    def test_shape_matches_contract(self, populated_workspace):
        from dailystream.timeline import generate_structured

        wm, _, _ = populated_workspace
        data = generate_structured(wm.workspace_dir, wm.meta)

        assert data is not None
        for key in (
            "workspace", "stats", "entries",
            "pipeline_summaries", "daily_summary",
        ):
            assert key in data, f"missing key: {key}"
        assert isinstance(data["entries"], list)
        assert len(data["entries"]) == 5

    def test_image_paths_still_absolute(self, populated_workspace):
        from dailystream.timeline import generate_structured

        wm, _, _ = populated_workspace
        data = generate_structured(wm.workspace_dir, wm.meta)

        for e in data["entries"]:
            if e["input_type"] == "image":
                assert Path(e["input_content"]).is_absolute()

    def test_totals_match_summary(self, populated_workspace):
        """Summary and full export must agree on every aggregate."""
        from dailystream.timeline import generate_summary, generate_structured

        wm, _, _ = populated_workspace
        full = generate_structured(wm.workspace_dir, wm.meta)
        summ = generate_summary(wm.workspace_dir, wm.meta)

        assert full["stats"] == summ["stats"]
        assert full["pipeline_summaries"] == summ["pipeline_summaries"]
        assert full["workspace"] == summ["workspace"]
        assert full["daily_summary"] == summ["daily_summary"]
