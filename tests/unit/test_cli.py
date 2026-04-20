"""Comprehensive CLI tests using Click's test runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from replicate_mcp.cli.main import app, _load_payload


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run(*args: str, env: dict | None = None, input: str | None = None) -> "Result":
    runner = CliRunner()
    return runner.invoke(app, list(args), env=env or {}, input=input, catch_exceptions=False)


# ---------------------------------------------------------------------------
# Top-level commands
# ---------------------------------------------------------------------------


class TestCLITopLevel:
    def test_help(self) -> None:
        result = _run("--help")
        assert result.exit_code == 0
        assert "Replicate MCP" in result.output

    def test_version(self) -> None:
        result = _run("--version")
        assert result.exit_code == 0

    def test_unknown_command(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["nonexistent-command"])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# init command
# ---------------------------------------------------------------------------


class TestInitCommand:
    def test_init_creates_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        runner = CliRunner()
        result = runner.invoke(app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Created" in result.output or "already exists" in result.output

    def test_init_with_token_set(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        runner = CliRunner()
        result = runner.invoke(app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        # Should show masked token
        assert "..." in result.output or "API token" in result.output

    def test_init_warns_if_no_token(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        runner = CliRunner()
        result = runner.invoke(app, ["init"], catch_exceptions=False)
        assert "REPLICATE_API_TOKEN" in result.output


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_status_no_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        runner = CliRunner()
        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "REPLICATE_API_TOKEN" in result.output

    def test_status_with_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        runner = CliRunner()
        result = runner.invoke(app, ["status"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "replicate-mcp-agents" in result.output


# ---------------------------------------------------------------------------
# workflows sub-commands
# ---------------------------------------------------------------------------


class TestWorkflowsCommands:
    def test_workflows_help(self) -> None:
        result = _run("workflows", "--help")
        assert result.exit_code == 0

    def test_workflows_list(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["workflows", "list"], catch_exceptions=False)
        assert result.exit_code == 0

    def test_workflows_run_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["workflows", "run", "--help"], catch_exceptions=False)
        assert result.exit_code == 0

    def test_workflows_run_no_checkpoint_with_resume_fails(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["workflows", "run", "my_wf", "--resume-from", "node_a"],
            catch_exceptions=False,
        )
        # Should print validation error and exit 1
        assert result.exit_code == 1

    def test_workflows_run_with_json_input(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["workflows", "run", "my_wf", "--input", '{"key": "value"}'],
            catch_exceptions=False,
        )
        # Phase 3 placeholder — just verify no crash
        assert result.exit_code == 0

    def test_workflows_run_with_file_input(self, tmp_path: Path) -> None:
        input_file = tmp_path / "input.json"
        input_file.write_text('{"prompt": "hello"}')
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["workflows", "run", "my_wf", "--input", str(input_file)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

    def test_workflows_run_invalid_json_fails(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["workflows", "run", "my_wf", "--input", "not-json"],
        )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# agents sub-commands
# ---------------------------------------------------------------------------


class TestAgentsCommands:
    def test_agents_help(self) -> None:
        result = _run("agents", "--help")
        assert result.exit_code == 0

    def test_agents_list(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["agents", "list"], catch_exceptions=False)
        assert result.exit_code == 0
        # Default agents should appear
        assert "llama3_chat" in result.output or "Agent" in result.output

    def test_agents_run_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["agents", "run", "--help"], catch_exceptions=False)
        assert result.exit_code == 0

    def test_agents_run_no_token_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["agents", "run", "llama3_chat", "--input", '{"prompt":"hi"}'],
        )
        assert result.exit_code == 1

    def test_agents_run_invalid_agent_id_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["agents", "run", "9invalid_id!", "--input", '{"prompt":"hi"}'],
        )
        # Should fail validation or token check
        assert result.exit_code == 1

    def test_agents_run_invalid_json_input_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["agents", "run", "llama3_chat", "--input", "not{json}"],
        )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# _load_payload helper
# ---------------------------------------------------------------------------


class TestLoadPayload:
    def test_none_returns_empty(self) -> None:
        assert _load_payload(None) == {}

    def test_json_string(self) -> None:
        result = _load_payload('{"key": "value"}')
        assert result == {"key": "value"}

    def test_file_path(self, tmp_path: Path) -> None:
        f = tmp_path / "data.json"
        f.write_text('{"a": 1}')
        result = _load_payload(str(f))
        assert result == {"a": 1}

    def test_invalid_json_string_exits(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            # _load_payload calls sys.exit(1) on bad json
            with pytest.raises(SystemExit) as exc_info:
                _load_payload("not-valid-json")
            assert exc_info.value.code == 1

    def test_invalid_json_in_file_exits(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.json"
        f.write_text("{ broken json")
        with pytest.raises(SystemExit) as exc_info:
            _load_payload(str(f))
        assert exc_info.value.code == 1