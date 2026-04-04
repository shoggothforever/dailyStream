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
def start(path: Optional[str], title: Optional[str]):
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

    ws_dir = wm.create(base_path=base, title=title)
    click.echo(f"✅ Workspace created: {ws_dir}")


@cli.command()
def end():
    """End the current workspace and generate timeline."""
    wm = WorkspaceManager()
    if not wm.is_active:
        click.echo("⚠️  No active workspace.")
        return

    report = wm.end()
    click.echo("✅ Workspace ended.")
    if report:
        click.echo(f"📄 Timeline report: {report}")


@cli.group()
def pipeline():
    """Pipeline management."""
    pass


@pipeline.command("create")
@click.argument("name")
def pipeline_create(name: str):
    """Create a new pipeline."""
    wm = WorkspaceManager()
    if not wm.is_active:
        click.echo("⚠️  No active workspace. Run 'dailystream start' first.")
        return

    pm = PipelineManager(wm.workspace_dir)
    pm.create(name)
    wm.add_pipeline(name)

    # Always activate the newly created pipeline
    wm.activate_pipeline(name)

    click.echo(f"✅ Pipeline '{name}' created and activated.")


@pipeline.command("list")
def pipeline_list():
    """List all pipelines."""
    wm = WorkspaceManager()
    if not wm.is_active:
        click.echo("⚠️  No active workspace.")
        return

    pm = PipelineManager(wm.workspace_dir)
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

    pm = PipelineManager(wm.workspace_dir)
    entry = pm.add_entry(pipeline_name, input_type, content, desc)
    click.echo(f"✅ Saved to '{pipeline_name}': {desc or content[:50]}")

    # Trigger sync
    try:
        from .note_sync import NoteSyncManager
        config = Config.load()
        syncer = NoteSyncManager(config, workspace_dir=wm.workspace_dir)
        syncer.sync_entry(wm.meta, pipeline_name, entry)
    except Exception:
        pass


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

    if meta.active_pipeline:
        pm = PipelineManager(wm.workspace_dir)
        entries = pm.get_entries(meta.active_pipeline)
        click.echo(f"   Entries in '{meta.active_pipeline}': {len(entries)}")


@cli.command("app")
def run_app_cmd():
    """Run the menu bar tray application."""
    from .app import run_app
    run_app()
