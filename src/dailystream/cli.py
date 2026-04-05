"""CLI interface for DailyStream."""

import click
from pathlib import Path
from typing import Optional

from .config import Config
from .workspace import WorkspaceManager
from .pipeline import PipelineManager


@click.group()
def cli():
    """DailyStream — minimal daily recording stream system."""
    pass


@cli.command()
@click.option("--path", type=click.Path(), default=None, help="Workspace storage path.")
@click.option("--title", default=None, help="Workspace title.")
@click.option(
    "--ai-mode",
    type=click.Choice(["off", "realtime", "daily_report"], case_sensitive=False),
    default=None,
    help="AI analysis mode: off / realtime / daily_report. Default reads config.ai_default_mode.",
)
def start(path: Optional[str], title: Optional[str], ai_mode: Optional[str]):
    """Start a new workspace."""
    wm = WorkspaceManager()
    if wm.is_active:
        click.echo(f"⚠️  Workspace already active: {wm.workspace_dir}")
        return

    base = Path(path) if path else None
    if base is None:
        # Use default
        from .config import DEFAULT_WORKSPACE_ROOT
        base = DEFAULT_WORKSPACE_ROOT

    # Resolve ai_mode: CLI option > config default > "off"
    config = Config.load()
    resolved_ai_mode = ai_mode or getattr(config, "ai_default_mode", "off") or "off"

    ws_dir = wm.create(base_path=base, title=title, ai_mode=resolved_ai_mode)
    mode_label = f" (AI: {resolved_ai_mode})" if resolved_ai_mode != "off" else ""
    click.echo(f"✅ Workspace created: {ws_dir}{mode_label}")


@cli.command()
def end():
    """End the current workspace and generate timeline."""
    wm = WorkspaceManager()
    if not wm.is_active:
        click.echo("⚠️  No active workspace.")
        return

    config = Config.load()
    report = wm.end(config=config)
    click.echo("✅ Workspace ended.")
    if report:
        click.echo(f"📄 Timeline report: {report}")


@cli.group()
def pipeline():
    """Pipeline management."""
    pass


@pipeline.command("create")
@click.argument("name")
@click.option("--desc", "-d", default="", help="Description of this pipeline's work content.")
@click.option("--goal", "-g", default="", help="Goal / objective of this pipeline.")
def pipeline_create(name: str, desc: str, goal: str):
    """Create a new pipeline."""
    wm = WorkspaceManager()
    if not wm.is_active:
        click.echo("⚠️  No active workspace. Run 'dailystream start' first.")
        return

    config = Config.load()
    pm = PipelineManager(wm.workspace_dir, screenshot_save_path=config.screenshot_save_path)
    pm.create(name, description=desc, goal=goal)
    wm.add_pipeline(name)

    # Always activate the newly created pipeline
    wm.activate_pipeline(name)

    click.echo(f"✅ Pipeline '{name}' created and activated.")
    if desc:
        click.echo(f"   Description: {desc}")
    if goal:
        click.echo(f"   Goal: {goal}")


@pipeline.command("list")
def pipeline_list():
    """List all pipelines."""
    wm = WorkspaceManager()
    if not wm.is_active:
        click.echo("⚠️  No active workspace.")
        return

    pm = PipelineManager(wm.workspace_dir)  # no screenshots needed for list
    names = pm.list_pipelines()
    active = wm.get_active_pipeline()

    if not names:
        click.echo("No pipelines. Create one with 'dailystream pipeline create <name>'.")
        return

    for n in names:
        marker = " ← active" if n == active else ""
        click.echo(f"  {'→' if n == active else ' '} {n}{marker}")


@cli.command()
@click.argument("name")
def activate(name: str):
    """Activate a pipeline."""
    wm = WorkspaceManager()
    if not wm.is_active:
        click.echo("⚠️  No active workspace.")
        return

    if wm.activate_pipeline(name):
        click.echo(f"✅ Pipeline '{name}' activated.")
    else:
        click.echo(f"⚠️  Pipeline '{name}' not found.")


@cli.command()
@click.argument("content")
@click.option("--desc", "-d", default="", help="Description for the entry.")
@click.option("--type", "input_type", default="text", help="Input type: text/url/image.")
def feed(content: str, desc: str, input_type: str):
    """Feed content to the active pipeline."""
    wm = WorkspaceManager()
    if not wm.is_active:
        click.echo("⚠️  No active workspace.")
        return

    pipeline_name = wm.get_active_pipeline()
    if not pipeline_name:
        click.echo("⚠️  No active pipeline. Activate one first.")
        return

    config = Config.load()
    pm = PipelineManager(wm.workspace_dir, screenshot_save_path=config.screenshot_save_path)
    entry = pm.add_entry(pipeline_name, input_type, content, desc)
    click.echo(f"✅ Saved to '{pipeline_name}': {desc or content[:50]}")

    # Trigger sync
    try:
        from .note_sync import NoteSyncManager
        syncer = NoteSyncManager(config, workspace_dir=wm.workspace_dir)
        pipeline_meta = pm.get_pipeline_meta(pipeline_name)
        syncer.sync_entry(wm.meta, pipeline_name, entry, pipeline_meta=pipeline_meta)
    except Exception:
        import traceback
        traceback.print_exc()

    # Trigger AI analysis (realtime mode, synchronous for CLI)
    ai_mode = getattr(wm.meta, "ai_mode", "off") or "off"
    if ai_mode == "realtime" and input_type in ("image", "url"):
        try:
            from .ai_analyzer import ImageAnalyzer, AnalysisStore
            from dataclasses import asdict

            analyzer = ImageAnalyzer(config)
            if analyzer.available:
                entry_dict = asdict(entry) if hasattr(entry, "__dataclass_fields__") else entry
                entries = pm.get_entries(pipeline_name)
                entry_index = max(0, len(entries) - 1)

                result = None
                if input_type == "image" and Path(content).exists():
                    result = analyzer.analyze_image(Path(content), user_hint=desc)
                elif input_type == "url":
                    result = analyzer.analyze_url(content, user_hint=desc)

                if result is not None:
                    result.entry_index = entry_index
                    result.timestamp = entry_dict.get("timestamp", "")
                    store_path = wm.workspace_dir / "pipelines" / pipeline_name / "ai_analyses.json"
                    store = AnalysisStore(store_path, pipeline_name=pipeline_name)
                    store.set_model(analyzer._model)
                    store.append(result)
                    click.echo(f"🤖 AI: {result.description[:80]}")
        except Exception:
            import traceback
            traceback.print_exc()


@cli.command()
def status():
    """Show current workspace and pipeline status."""
    wm = WorkspaceManager()
    if not wm.is_active:
        click.echo("No active workspace.")
        return

    meta = wm.meta
    click.echo(f"📁 Workspace: {meta.title or meta.workspace_id}")
    click.echo(f"   Path: {meta.workspace_path}")
    click.echo(f"   Created: {meta.created_at}")
    click.echo(f"   Active pipeline: {meta.active_pipeline or 'none'}")
    click.echo(f"   Pipelines: {', '.join(meta.pipelines) if meta.pipelines else 'none'}")
    ai_mode = getattr(meta, "ai_mode", "off") or "off"
    click.echo(f"   AI Mode: {ai_mode}")

    if meta.active_pipeline:
        pm = PipelineManager(wm.workspace_dir)
        entries = pm.get_entries(meta.active_pipeline)
        click.echo(f"   Entries in '{meta.active_pipeline}': {len(entries)}")


@cli.command()
@click.option("--force", is_flag=True, help="Re-analyse entries that already have results.")
def analyze(force: bool):
    """Run AI analysis on all un-analysed entries in the current workspace."""
    wm = WorkspaceManager()
    if not wm.is_active:
        click.echo("⚠️  No active workspace.")
        return

    config = Config.load()
    pm = PipelineManager(wm.workspace_dir, screenshot_save_path=config.screenshot_save_path)

    try:
        from .ai_analyzer import ImageAnalyzer, AnalysisStore, AnalysisResult
    except ImportError:
        click.echo("⚠️  AI module not available. Install with: pip install 'dailystream[ai]'")
        return

    analyzer = ImageAnalyzer(config)
    if not analyzer.available:
        click.echo("⚠️  AI not available. Check API key (DAILYSTREAM_AI_KEY or config.ai_api_key).")
        return

    total_new = 0
    for pipeline_name in pm.list_pipelines():
        entries = pm.get_entries(pipeline_name)
        store_path = pm.get_ai_analyses_path(pipeline_name)
        store = AnalysisStore(store_path, pipeline_name=pipeline_name)

        for idx, entry in enumerate(entries):
            itype = entry.get("input_type", "")
            ts = entry.get("timestamp", "")
            content = entry.get("input_content", "")
            desc = entry.get("description", "")

            if itype not in ("image", "url"):
                continue
            if not force and store.has_analysis(idx, ts):
                continue

            click.echo(f"  🔍 [{pipeline_name}] #{idx} ({itype}): {desc[:40] or content[:40]}...", nl=False)

            result = None
            if itype == "image" and Path(content).exists():
                result = analyzer.analyze_image(Path(content), user_hint=desc)
            elif itype == "url":
                result = analyzer.analyze_url(content, user_hint=desc)

            if result is not None:
                result.entry_index = idx
                result.timestamp = ts
                store.append(result)
                click.echo(f" ✅ {result.category}")
                total_new += 1
            else:
                fail = AnalysisResult.failed(idx, ts, itype, "Analysis returned None")
                store.append(fail)
                click.echo(" ❌ failed")

        store.set_model(analyzer._model)

    click.echo(f"\n🤖 Analysis complete: {total_new} new results.")


@cli.command("app")
def run_app_cmd():
    """Run the menu bar tray application."""
    from .app import run_app
    run_app()


# --- Screenshot preset management ---

@cli.group("preset")
def preset():
    """Manage screenshot presets."""
    pass


@preset.command("list")
def preset_list():
    """List all screenshot presets."""
    config = Config.load()
    presets = config.screenshot_presets or []
    if not presets:
        click.echo("No screenshot presets configured.")
        click.echo("Create one with: dailystream preset create --name 'My Region'")
        return

    click.echo(f"📐 Screenshot presets ({len(presets)}):\n")
    for i, p in enumerate(presets, 1):
        name = p.get("name", f"Preset {i}")
        region = p.get("region", "?")
        hotkey = p.get("hotkey", "")
        hotkey_str = f"  [{hotkey}]" if hotkey else ""
        click.echo(f"  {i}. {name}  →  {region}{hotkey_str}")


@preset.command("create")
@click.option("--name", "-n", required=True, help="Preset name.")
@click.option("--region", "-r", default=None, help="Region as 'x,y,w,h'. If omitted, interactive selection.")
@click.option("--hotkey", "-k", default=None, help="Global hotkey, e.g. '<cmd>+3'. Optional.")
def preset_create(name: str, region: Optional[str], hotkey: Optional[str]):
    """Create a new screenshot preset.

    If --region is omitted, opens an interactive overlay for you to
    drag-select the desired capture area.
    """
    config = Config.load()

    if region is None:
        click.echo("🖱  Drag to select a screen region. Press Esc to cancel...")
        try:
            from .capture import capture_screen_region
            region = capture_screen_region()
        except Exception as e:
            click.echo(f"⚠️  Interactive selection failed: {e}")
            click.echo("   Provide --region 'x,y,w,h' instead.")
            return
        if not region:
            click.echo("Cancelled.")
            return
        click.echo(f"   Selected region: {region}")

    # Validate format
    parts = region.split(",")
    if len(parts) != 4:
        click.echo("⚠️  Region must be 'x,y,w,h' (4 comma-separated integers).")
        return
    try:
        [int(p) for p in parts]
    except ValueError:
        click.echo("⚠️  Region values must be integers.")
        return

    if config.screenshot_presets is None:
        config.screenshot_presets = []
    preset_entry: dict[str, str] = {"name": name, "region": region}
    if hotkey:
        preset_entry["hotkey"] = hotkey
    config.screenshot_presets.append(preset_entry)
    config.save()
    msg = f"✅ Preset '{name}' saved → {region}"
    if hotkey:
        msg += f"  [{hotkey}]"
    click.echo(msg)


@preset.command("delete")
@click.argument("name_or_index")
def preset_delete(name_or_index: str):
    """Delete a screenshot preset by name or index (1-based)."""
    config = Config.load()
    presets = config.screenshot_presets or []
    if not presets:
        click.echo("No presets to delete.")
        return

    # Try as index first
    idx = None
    try:
        i = int(name_or_index) - 1
        if 0 <= i < len(presets):
            idx = i
    except ValueError:
        pass

    # Try as name
    if idx is None:
        for i, p in enumerate(presets):
            if p.get("name", "").lower() == name_or_index.lower():
                idx = i
                break

    if idx is None:
        click.echo(f"⚠️  Preset '{name_or_index}' not found. Use 'dailystream preset list' to see all.")
        return

    removed = presets.pop(idx)
    config.screenshot_presets = presets if presets else None
    config.save()
    click.echo(f"✅ Deleted preset '{removed.get('name', '?')}' ({removed.get('region', '?')})")
