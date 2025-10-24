"""CLI entrypoint for Replicate MCP agent orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from replicate_mcp import __version__

console = Console()


@click.group()
@click.version_option(version=__version__)
def app() -> None:
    """Replicate MCP Agent Orchestration CLI."""


@app.command()
def init() -> None:
    """Initialise local MCP configuration for the Replicate agent."""

    config_path = Path.home() / ".replicate" / "mcp.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text("server:\n  transport: stdio\n  log_level: info\n")
        console.print(f"✓ Created {config_path}", style="green")
    else:
        console.print(f"• Configuration already exists at {config_path}", style="yellow")


@app.group()
def workflows() -> None:
    """Manage registered agent workflows."""


@workflows.command("list")
def list_workflows() -> None:
    """List available workflows (placeholder implementation)."""

    table = Table(title="Available Workflows")
    table.add_column("Name", style="cyan")
    table.add_column("Steps", justify="right")
    table.add_column("Resumable", justify="center")
    table.add_column("Avg Cost", justify="right", style="green")

    console.print(table)


@app.command()
@click.argument("workflow_name")
@click.option("--input", "input_payload", help="JSON payload or path to input file.")
@click.option("--stream/--no-stream", default=False, help="Enable streaming output")
def run(workflow_name: str, input_payload: str | None, stream: bool) -> None:
    """Execute a workflow with the provided input (placeholder)."""

    payload: str | None = None
    if input_payload:
        potential_path = Path(input_payload)
        if potential_path.exists():
            payload = potential_path.read_text()
        else:
            payload = input_payload

    if payload:
        try:
            parsed = json.loads(payload)
            display_payload = json.dumps(parsed)
        except json.JSONDecodeError:
            display_payload = payload
    else:
        display_payload = "{}"
    console.print(
        f"[bold]Running workflow[/bold] {workflow_name} with payload {display_payload} (stream={stream})"
    )


__all__ = ["app"]
