"""CLI entrypoint for Replicate MCP agent orchestration.

Phase 4 improvements:
    - ``serve`` command with ``--transport`` (stdio | sse | streamable-http),
      ``--host``, and ``--port`` options for cloud-hosted MCP deployments.
    - ``workflows run`` fully implemented: resolves specs from the SDK workflow
      registry and executes each step sequentially via AgentExecutor.
    - ``workers`` sub-group with ``start`` command for launching HTTP worker
      nodes that participate in distributed execution.
    - ``agents run`` extended with ``--json`` (raw output) and ``--model``
      (model-path override) flags.
    - Timeout enforcement via ``asyncio.wait_for``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast

import anyio
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
# serve
# ---------------------------------------------------------------------------


@app.command()
@click.option(
    "--transport",
    type=click.Choice(["stdio", "sse", "streamable-http"]),
    default="stdio",
    show_default=True,
    help="MCP transport protocol.",
)
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind host (HTTP transports).")  # noqa: S104
@click.option("--port", default=8080, show_default=True, type=int, help="TCP port (HTTP transports).")
@click.option("--mount-path", default=None, help="URL prefix for SSE transport.")
@click.option("--log-level", default="info", show_default=True, help="Uvicorn log level.")
def serve(
    transport: str,
    host: str,
    port: int,
    mount_path: str | None,
    log_level: str,
) -> None:
    """Launch the MCP server with the specified transport.

    \b
    Transports:
      stdio           — default; for Claude Desktop / Cursor integration.
      sse             — HTTP Server-Sent Events; cloud-hosted MCP.
      streamable-http — modern bidirectional HTTP; MCP 1.x preferred.

    Examples:

    \b
      # Local stdio (Claude Desktop)
      replicate-agent serve

      # Cloud-hosted SSE on port 8080
      replicate-agent serve --transport sse --port 8080

      # Streamable HTTP (MCP 1.x)
      replicate-agent serve --transport streamable-http --host 0.0.0.0 --port 9090
    """
    from replicate_mcp.server import serve as _serve_stdio  # noqa: PLC0415
    from replicate_mcp.server import serve_http, serve_streamable_http  # noqa: PLC0415

    token = _secret_manager.get_token(required=False)
    if not token:
        console.print(
            "[yellow]⚠  REPLICATE_API_TOKEN is not set.[/yellow]  "
            "Tool calls will fail until it is exported."
        )

    if transport == "stdio":
        console.print("[dim]Starting MCP server (stdio)…[/dim]")
        _serve_stdio()
    elif transport == "sse":
        console.print(f"[bold]Starting MCP SSE server[/bold] on [cyan]http://{host}:{port}[/cyan]")
        serve_http(host=host, port=port, mount_path=mount_path, log_level=log_level)
    else:  # streamable-http
        console.print(
            f"[bold]Starting MCP Streamable HTTP server[/bold] on [cyan]http://{host}:{port}[/cyan]"
        )
        serve_streamable_http(host=host, port=port, log_level=log_level)


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
def list_workflows_cmd() -> None:
    """List registered workflows (from the SDK workflow registry)."""
    from replicate_mcp.sdk import list_workflows  # noqa: PLC0415

    wf_map = list_workflows()

    table = Table(title="Registered Workflows", show_header=True)
    table.add_column("Name", style="cyan", min_width=20)
    table.add_column("Steps", justify="right")
    table.add_column("Agents")
    table.add_column("Description")

    if not wf_map:
        console.print(table)
        console.print("[dim]No workflows registered. Use sdk.register_workflow() to add one.[/dim]")
        return

    for name, spec in sorted(wf_map.items()):
        table.add_row(
            name,
            str(spec.step_count),
            " → ".join(spec.agent_names),
            spec.description or "—",
        )
    console.print(table)


@workflows.command("run")
@click.argument("workflow_name")
@click.option("--input", "input_payload", help="JSON payload or path to JSON file.")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output raw JSON.")
@click.option(
    "--timeout",
    "timeout_s",
    type=float,
    default=300.0,
    show_default=True,
    help="Max seconds per step.",
)
@click.option(
    "--checkpoint-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Directory for checkpoint files.",
)
@click.option("--resume-from", default=None, help="Step index to resume from (0-based).")
def run_workflow(
    workflow_name: str,
    input_payload: str | None,
    output_json: bool,
    timeout_s: float,
    checkpoint_dir: Path | None,
    resume_from: str | None,
) -> None:
    """Execute a registered workflow step-by-step.

    The workflow must be registered via ``sdk.register_workflow()`` before
    this command is invoked.  Each step's output is passed as the input to
    the next step, with ``input_map`` applied for key remapping.

    Example::

        replicate-agent workflows run research --input '{"query": "MCP protocol"}'
    """
    from replicate_mcp.sdk import get_workflow  # noqa: PLC0415

    payload = _load_payload(input_payload)

    try:
        validated = WorkflowInputModel(
            workflow_name=workflow_name,
            initial_input=payload,
            stream=False,
            checkpoint_dir=checkpoint_dir,
            resume_from=resume_from,
        )
    except Exception as exc:  # noqa: BLE001
        err_console.print(f"[bold red]✗ Validation error:[/bold red] {exc}")
        sys.exit(1)

    spec = get_workflow(workflow_name)
    if spec is None:
        err_console.print(
            f"[bold red]✗ Workflow '{workflow_name}' not found.[/bold red]\n"
            "  Register it with [cyan]sdk.register_workflow(spec)[/cyan] before running."
        )
        sys.exit(1)

    token = _secret_manager.get_token(required=False)
    if not token:
        err_console.print(
            "[bold red]✗ REPLICATE_API_TOKEN is not set.[/bold red]\n"
            "  Run: [cyan]export REPLICATE_API_TOKEN=<your-token>[/cyan]"
        )
        sys.exit(1)

    executor = AgentExecutor(api_token=token)
    resume_step = int(resume_from) if resume_from is not None else 0

    if not output_json:
        console.print(
            Panel(
                f"[bold]Workflow:[/bold] {spec.name}  "
                f"([dim]{spec.step_count} steps[/dim])\n"
                f"[bold]Input:[/bold] {json.dumps(validated.initial_input, indent=2)}",
                title="Running Workflow",
                border_style="blue",
            )
        )

    all_results: list[dict[str, Any]] = []

    async def _run_workflow() -> None:
        current_input: dict[str, Any] = dict(validated.initial_input)

        for step_idx, step in enumerate(spec.steps):
            if step_idx < resume_step:
                continue

            # Apply input_map: remap keys from previous output
            step_input = dict(current_input)
            for target_key, source_key in step.input_map.items():
                if source_key in current_input:
                    step_input[target_key] = current_input[source_key]

            if not output_json:
                console.print(
                    f"\n[bold cyan][Step {step_idx + 1}/{spec.step_count}][/bold cyan] "
                    f"[green]{step.agent_name}[/green]"
                )

            step_chunks: list[dict[str, Any]] = []
            with anyio.move_on_after(timeout_s) as scope:
                async for chunk in executor.run(step.agent_name, step_input):
                    step_chunks.append(chunk)
                    if chunk.get("error") and not output_json:
                        err_console.print(f"  [bold red]Error:[/bold red] {chunk['error']}")
                    if chunk.get("done") and not output_json:
                        out = chunk.get("output", "")
                        lat = chunk.get("latency_ms", 0)
                        console.print(
                            Panel(
                                str(out),
                                title=f"[green]✓ {step.agent_name}[/green] [dim]{lat:.0f}ms[/dim]",
                                border_style="green",
                            )
                        )
                        # Feed this step's output as next step's input
                        if out:
                            current_input = {"output": out, "result": out}
            if scope.cancelled_caught:
                err_console.print(
                    f"  [bold red]✗ Step {step_idx + 1} timed out after {timeout_s:.0f}s.[/bold red]"
                )
                if not output_json:
                    break

            all_results.append(
                {
                    "step": step_idx,
                    "agent": step.agent_name,
                    "chunks": step_chunks,
                }
            )

            # Checkpoint after each step
            if checkpoint_dir:
                _save_checkpoint(checkpoint_dir, workflow_name, step_idx, current_input)

    asyncio.run(_run_workflow())

    if output_json:
        console.print(json.dumps(all_results, indent=2))


def _save_checkpoint(
    checkpoint_dir: Path,
    workflow_name: str,
    step_idx: int,
    state: dict[str, Any],
) -> None:
    """Persist a workflow step checkpoint to disk."""
    import tempfile  # noqa: PLC0415

    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    target = checkpoint_dir / f"{workflow_name}_step_{step_idx}.json"
    with tempfile.NamedTemporaryFile(
        "w", dir=checkpoint_dir, delete=False, suffix=".json"
    ) as tmp:
        json.dump(state, tmp)
        tmp_path = Path(tmp.name)
    tmp_path.replace(target)


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
@click.option("--model", "model_override", default=None, help="Override Replicate model path.")
@click.option("--stream/--no-stream", default=True, help="Enable streaming output.")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output raw JSON chunks.")
@click.option(
    "--timeout",
    "timeout_s",
    type=float,
    default=120.0,
    show_default=True,
    help="Max seconds to wait for a response.",
)
def run_agent(
    agent_id: str,
    input_payload: str | None,
    model_override: str | None,
    stream: bool,
    output_json: bool,
    timeout_s: float,
) -> None:
    """Invoke a single Replicate agent and print streaming output.

    AGENT_ID may be a short name (``llama3_chat``) or a full Replicate
    model path (``meta/llama-3-8b-instruct``).  Use ``--model`` to
    override the registered model path with any Replicate model.

    Examples::

    \b
      replicate-agent agents run llama3_chat --input '{"prompt": "Hello!"}'
      replicate-agent agents run my_agent --model meta/llama-3-70b --json
    """
    payload = _load_payload(input_payload)

    # Validate input
    try:
        validated = AgentInputModel(
            agent_id=agent_id, payload=payload, stream=stream, timeout_s=timeout_s
        )
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

    # Build model_map with optional override
    from replicate_mcp.agents.execution import DEFAULT_MODEL_MAP  # noqa: PLC0415

    model_map = dict(DEFAULT_MODEL_MAP)
    if model_override:
        model_map[agent_id] = model_override

    executor = AgentExecutor(api_token=token, model_map=model_map)
    all_chunks: list[dict[str, Any]] = []

    if not output_json:
        console.print(
            f"\n[bold blue]▶ Running agent:[/bold blue] [cyan]{validated.agent_id}[/cyan]"
            + (f" → [dim]{model_override}[/dim]" if model_override else "")
            + "\n"
        )

    async def _stream() -> None:
        output_parts: list[str] = []
        with anyio.move_on_after(validated.timeout_s) as scope:
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                console=console,
                transient=not output_json,
            ) as progress:
                ptask = progress.add_task(f"Calling [cyan]{validated.agent_id}[/cyan]…")
                async for chunk in executor.run(validated.agent_id, validated.payload):
                    all_chunks.append(chunk)
                    if chunk.get("error"):
                        if not output_json:
                            progress.stop()
                            err_console.print(f"[bold red]✗ Error:[/bold red] {chunk['error']}")
                        return
                    if "chunk" in chunk:
                        output_parts.append(chunk["chunk"])
                        if not output_json:
                            progress.update(
                                ptask,
                                description=f"Streaming… ({len(output_parts)} chunks)",
                            )
                    if chunk.get("done"):
                        if not output_json:
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
        if scope.cancelled_caught:
            err_console.print(
                f"[bold red]✗ Timed out after {validated.timeout_s:.0f}s.[/bold red]"
            )
            sys.exit(1)

    asyncio.run(_stream())

    if output_json:
        console.print(json.dumps(all_chunks, indent=2))


# ---------------------------------------------------------------------------
# workers sub-group
# ---------------------------------------------------------------------------


@app.group()
def workers() -> None:
    """Manage distributed worker nodes."""


@workers.command("start")
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind host.")  # noqa: S104
@click.option("--port", default=7999, show_default=True, type=int, help="TCP port.")
@click.option("--node-id", default=None, help="Human-readable node identifier.")
@click.option("--concurrency", default=8, show_default=True, type=int, help="Max parallel tasks.")
@click.option("--log-level", default="info", show_default=True, help="Uvicorn log level.")
def start_worker(
    host: str,
    port: int,
    node_id: str | None,
    concurrency: int,
    log_level: str,
) -> None:
    """Start an HTTP worker node for distributed agent execution.

    The worker exposes three endpoints:

    \b
      POST /execute  — run an agent, returns TaskResult JSON.
      GET  /health   — liveness probe.
      GET  /metrics  — load counters.

    Connect from a coordinator using RemoteWorkerNode + HttpWorkerTransport:

    \b
      from replicate_mcp.distributed import HttpWorkerTransport, RemoteWorkerNode
      transport = HttpWorkerTransport("http://<worker-host>:<port>")
      node = RemoteWorkerNode("<node-id>", transport=transport)

    Example::

    \b
      # On the worker machine:
      export REPLICATE_API_TOKEN=r8_...
      replicate-agent workers start --port 7999 --node-id gpu-node-1
    """
    token = _secret_manager.get_token(required=False)
    if not token:
        err_console.print(
            "[bold red]✗ REPLICATE_API_TOKEN is not set.[/bold red]\n"
            "  Workers need the token to call Replicate models."
        )
        sys.exit(1)

    from replicate_mcp.worker_server import serve_worker  # noqa: PLC0415

    console.print(
        Panel(
            f"[bold]Node ID:[/bold]     {node_id or 'auto'}\n"
            f"[bold]Address:[/bold]     http://{host}:{port}\n"
            f"[bold]Concurrency:[/bold] {concurrency}\n"
            f"[bold]Token:[/bold]       {_secret_manager.masked_token() or '(not set)'}",
            title="[bold green]Starting Worker Node[/bold green]",
            border_style="green",
        )
    )
    asyncio.run(
        serve_worker(
            host=host,
            port=port,
            api_token=token,
            node_id=node_id,
            log_level=log_level,
            max_concurrency=concurrency,
        )
    )


@workers.command("ping")
@click.argument("url")
def ping_worker(url: str) -> None:
    """Check reachability of a remote worker node.

    URL should be the base HTTP URL of the worker, e.g. ``http://host:7999``.
    """
    from replicate_mcp.distributed import HttpWorkerTransport  # noqa: PLC0415

    transport = HttpWorkerTransport(url)

    async def _ping() -> None:
        ok = await transport.health_check()
        if ok:
            metrics = await transport.get_metrics()
            console.print(f"[green]✓[/green] Worker at [cyan]{url}[/cyan] is healthy")
            if metrics:
                console.print(f"  Active tasks:     {metrics.get('active_tasks', '?')}")
                console.print(f"  Total processed:  {metrics.get('total_processed', '?')}")
        else:
            err_console.print(f"[bold red]✗[/bold red] Worker at [cyan]{url}[/cyan] is unreachable")
            sys.exit(1)

    asyncio.run(_ping())


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
            return cast(dict[str, Any], json.loads(path.read_text()))
        except json.JSONDecodeError as exc:
            err_console.print(f"[bold red]✗ Invalid JSON in file {path}:[/bold red] {exc}")
            sys.exit(1)
    try:
        return cast(dict[str, Any], json.loads(raw))
    except json.JSONDecodeError as exc:
        err_console.print(f"[bold red]✗ Invalid JSON input:[/bold red] {exc}")
        sys.exit(1)


__all__ = ["app"]
