"""CLI entrypoint for Replicate MCP agent orchestration.

Phase 2 improvements:
    - Rich progress bars and streaming output
    - Coloured error messages with suggested fixes
    - Input validation via :mod:`replicate_mcp.validation`
    - Security check (warns if REPLICATE_API_TOKEN is unset)
    - ``agents`` sub-command group for listing / running single agents
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from replicate_mcp import __version__
from replicate_mcp.agents.execution import AgentExecutor
from replicate_mcp.security import SecretManager
from replicate_mcp.validation import AgentInputModel, WorkflowInputModel

console = Console()
err_console = Console(stderr=True, style="bold red")

_secret_manager = SecretManager()


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version=__version__)
def app() -> None:
    """Replicate MCP Agent Orchestration CLI.

    Manages multi-agent AI workflows powered by Replicate models.
    """


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init() -> None:
    """Initialise local MCP configuration for the Replicate agent."""
    config_path = Path.home() / ".replicate" / "mcp.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        config_path.write_text(
            "server:\n  transport: stdio\n  log_level: INFO\n  max_concurrency: 8\n"
        )
        console.print(f"✓ Created [cyan]{config_path}[/cyan]", style="green")
    else:
        console.print(
            f"• Config already exists at [cyan]{config_path}[/cyan]", style="yellow"
        )

    # Warn if token missing
    token = _secret_manager.get_token(required=False)
    if not token:
        console.print(
            "[yellow]⚠  REPLICATE_API_TOKEN is not set. "
            "Export it before running models:[/yellow]\n"
            "    export REPLICATE_API_TOKEN=<your-token>"
        )
    else:
        masked = _secret_manager.masked_token()
        console.print(f"✓ API token detected: [dim]{masked}[/dim]")


# ---------------------------------------------------------------------------
# workflows sub-group
# ---------------------------------------------------------------------------


@app.group()
def workflows() -> None:
    """Manage multi-agent workflows."""


@workflows.command("list")
def list_workflows() -> None:
    """List registered workflows."""
    table = Table(title="Registered Workflows", show_header=True)
    table.add_column("Name", style="cyan", min_width=20)
    table.add_column("Nodes", justify="right")
    table.add_column("Resumable", justify="center")
    table.add_column("Est. Cost", justify="right", style="green")
    # Placeholder — real data will come from a workflow registry in Phase 3
    console.print(table)
    console.print("[dim]No workflows registered yet.[/dim]")


@workflows.command("run")
@click.argument("workflow_name")
@click.option("--input", "input_payload", help="JSON payload or path to JSON file.")
@click.option("--stream/--no-stream", default=False, help="Enable streaming output.")
@click.option(
    "--checkpoint-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory for checkpoint files.",
)
@click.option("--resume-from", default=None, help="Node ID to resume from.")
def run_workflow(
    workflow_name: str,
    input_payload: str | None,
    stream: bool,
    checkpoint_dir: Path | None,
    resume_from: str | None,
) -> None:
    """Execute a workflow with the provided input."""
    payload = _load_payload(input_payload)

    try:
        validated = WorkflowInputModel(
            workflow_name=workflow_name,
            initial_input=payload,
            stream=stream,
            checkpoint_dir=checkpoint_dir,
            resume_from=resume_from,
        )
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[bold red]✗ Validation error:[/bold red] {exc}")
        sys.exit(1)

    console.print(
        Panel(
            f"[bold]Workflow:[/bold] {validated.workflow_name}\n"
            f"[bold]Input:[/bold] {json.dumps(validated.initial_input, indent=2)}\n"
            f"[bold]Stream:[/bold] {validated.stream}",
            title="Running Workflow",
            border_style="blue",
        )
    )
    console.print("[dim]Workflow execution will be available in Phase 3.[/dim]")


# ---------------------------------------------------------------------------
# agents sub-group
# ---------------------------------------------------------------------------


@app.group()
def agents() -> None:
    """Manage and invoke individual Replicate agents."""


@agents.command("list")
def list_agents() -> None:
    """List all registered agents."""
    # Import the default server registry for a real listing
    try:
        from replicate_mcp.server import _registry  # noqa: PLC0415

        agent_map = _registry.list_agents()
    except Exception:  # noqa: BLE001
        agent_map = {}

    table = Table(title="Registered Agents", show_header=True)
    table.add_column("Name", style="cyan", min_width=16)
    table.add_column("Model", style="dim", min_width=30)
    table.add_column("Streaming", justify="center")
    table.add_column("Tags")
    table.add_column("Est. Cost", justify="right", style="green")

    for name, meta in sorted(agent_map.items()):
        table.add_row(
            name,
            meta.replicate_model(),
            "✓" if meta.supports_streaming else "✗",
            ", ".join(meta.tags) if meta.tags else "—",
            f"${meta.estimated_cost:.4f}" if meta.estimated_cost else "—",
        )

    console.print(table)


@agents.command("run")
@click.argument("agent_id")
@click.option("--input", "input_payload", help="JSON payload or path to JSON file.")
@click.option("--stream/--no-stream", default=True, help="Enable streaming output.")
@click.option(
    "--timeout",
    "timeout_s",
    type=float,
    default=120.0,
    help="Max seconds to wait for a response.",
)
def run_agent(agent_id: str, input_payload: str | None, stream: bool, timeout_s: float) -> None:
    """Invoke a single Replicate agent and print streaming output."""
    payload = _load_payload(input_payload)

    # Validate input
    try:
        validated = AgentInputModel(agent_id=agent_id, payload=payload, stream=stream, timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[bold red]✗ Validation error:[/bold red] {exc}")
        sys.exit(1)

    # Token check
    token = _secret_manager.get_token(required=False)
    if not token:
        err_console.print(
            "[bold red]✗ REPLICATE_API_TOKEN is not set.[/bold red]\n"
            "  Run: [cyan]export REPLICATE_API_TOKEN=<your-token>[/cyan]"
        )
        sys.exit(1)

    executor = AgentExecutor(api_token=token)

    console.print(
        f"\n[bold blue]▶ Running agent:[/bold blue] [cyan]{validated.agent_id}[/cyan]\n"
    )

    async def _stream() -> None:
        output_parts: list[str] = []
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task(f"Calling [cyan]{validated.agent_id}[/cyan]…")
            async for chunk in executor.run(validated.agent_id, validated.payload):
                if chunk.get("error"):
                    progress.stop()
                    err_console.print(
                        f"[bold red]✗ Error:[/bold red] {chunk['error']}"
                    )
                    return
                if "chunk" in chunk:
                    output_parts.append(chunk["chunk"])
                    progress.update(task, description=f"Streaming… ({len(output_parts)} chunks)")
                if chunk.get("done"):
                    progress.stop()
                    full_output = chunk.get("output") or "".join(output_parts)
                    latency = chunk.get("latency_ms", 0)
                    console.print(
                        Panel(
                            full_output,
                            title=f"[green]✓ {validated.agent_id}[/green]  "
                                  f"[dim]{latency:.0f}ms[/dim]",
                            border_style="green",
                        )
                    )

    asyncio.run(_stream())


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@app.command()
def status() -> None:
    """Show system status (token, version, circuit breaker states)."""
    console.print(f"[bold]replicate-mcp-agents[/bold] v{__version__}\n")

    token = _secret_manager.get_token(required=False)
    if token:
        masked = _secret_manager.masked_token()
        console.print(f"  API Token  [green]✓[/green]  {masked}")
    else:
        console.print("  API Token  [red]✗ not set[/red]  — export REPLICATE_API_TOKEN")

    try:
        from replicate_mcp.server import _registry  # noqa: PLC0415

        count = _registry.count
        console.print(f"  Agents     [green]✓[/green]  {count} registered")
    except Exception:  # noqa: BLE001
        console.print("  Agents     [yellow]?[/yellow]  could not load registry")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_payload(raw: str | None) -> dict[str, Any]:
    """Parse raw CLI input into a dict.  Accepts JSON string or file path."""
    if not raw:
        return {}
    path = Path(raw)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            err_console.print(f"[bold red]✗ Invalid JSON in file {path}:[/bold red] {exc}")
            sys.exit(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        err_console.print(f"[bold red]✗ Invalid JSON input:[/bold red] {exc}")
        sys.exit(1)


__all__ = ["app"]
