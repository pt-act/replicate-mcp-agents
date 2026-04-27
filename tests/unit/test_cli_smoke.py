"""Smoke tests for the CLI entrypoint."""

from __future__ import annotations

from click.testing import CliRunner

from replicate_mcp.cli.main import app


def test_cli_help() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Replicate MCP Agent Orchestration CLI" in result.output


def test_cli_version() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
