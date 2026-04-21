"""Comprehensive CLI tests using Click's test runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from replicate_mcp.cli.main import _load_payload, app

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _run(*args: str, env: dict | None = None, input: str | None = None) -> Result:
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

    def test_init_config_already_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """init should report 'already exists' when config file is present."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        config_path = tmp_path / ".replicate" / "mcp.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text("server:\n  transport: stdio\n")
        runner = CliRunner()
        result = runner.invoke(app, ["init"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "already exists" in result.output

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

    def test_workflows_run_unregistered_exits_1(self) -> None:
        """Running a workflow that is not registered should exit with code 1."""
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["workflows", "run", "nonexistent_wf", "--input", '{"key": "value"}'],
            catch_exceptions=False,
        )
        # Phase 4: fully implemented — exits 1 when workflow not in registry
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "not found" in (result.output or "")

    def test_workflows_run_with_file_input_unregistered(self, tmp_path: Path) -> None:
        """Running with a file payload but unregistered workflow exits 1."""
        input_file = tmp_path / "input.json"
        input_file.write_text('{"prompt": "hello"}')
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["workflows", "run", "nonexistent_wf", "--input", str(input_file)],
            catch_exceptions=False,
        )
        assert result.exit_code == 1

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


# ---------------------------------------------------------------------------
# Phase 4 — serve command
# ---------------------------------------------------------------------------


class TestServeCommand:
    def test_serve_help(self) -> None:
        result = _run("serve", "--help")
        assert result.exit_code == 0
        assert "stdio" in result.output
        assert "sse" in result.output

    def test_serve_invalid_transport_fails(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["serve", "--transport", "grpc"])
        assert result.exit_code != 0

    def test_serve_no_token_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """serve should warn when REPLICATE_API_TOKEN is not set."""
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        from unittest.mock import patch  # noqa: PLC0415

        with patch("replicate_mcp.server.serve") as mock_serve:
            mock_serve.return_value = None
            runner = CliRunner()
            result = runner.invoke(app, ["serve"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "REPLICATE_API_TOKEN" in result.output

    def test_serve_sse_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """serve --transport sse should call serve_http."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        from unittest.mock import patch  # noqa: PLC0415

        with (
            patch("replicate_mcp.server.serve"),
            patch("replicate_mcp.server.serve_http") as mock_http,
        ):
            mock_http.return_value = None
            runner = CliRunner()
            result = runner.invoke(app, ["serve", "--transport", "sse"], catch_exceptions=False)
        assert result.exit_code == 0
        mock_http.assert_called_once()

    def test_serve_streamable_http_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """serve --transport streamable-http should call serve_streamable_http."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        from unittest.mock import patch  # noqa: PLC0415

        with (
            patch("replicate_mcp.server.serve"),
            patch("replicate_mcp.server.serve_streamable_http") as mock_streamable,
        ):
            mock_streamable.return_value = None
            runner = CliRunner()
            result = runner.invoke(
                app, ["serve", "--transport", "streamable-http"], catch_exceptions=False
            )
        assert result.exit_code == 0
        mock_streamable.assert_called_once()

    def test_serve_workflows_file_bad_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """serve with an invalid YAML workflows file should warn but not crash."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        from unittest.mock import patch  # noqa: PLC0415

        wf_file = tmp_path / "bad.yaml"
        wf_file.write_text(": invalid : yaml : [}")
        with patch("replicate_mcp.server.serve") as mock_serve:
            mock_serve.return_value = None
            runner = CliRunner()
            result = runner.invoke(
                app,
                ["serve", "--workflows-file", str(wf_file)],
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        assert "Failed" in result.output or "failed" in result.output.lower()


# ---------------------------------------------------------------------------
# Phase 4 — workers commands
# ---------------------------------------------------------------------------


class TestWorkersCommands:
    def test_workers_help(self) -> None:
        result = _run("workers", "--help")
        assert result.exit_code == 0
        assert "start" in result.output

    def test_workers_start_help(self) -> None:
        result = _run("workers", "start", "--help")
        assert result.exit_code == 0
        assert "--port" in result.output

    def test_workers_ping_help(self) -> None:
        result = _run("workers", "ping", "--help")
        assert result.exit_code == 0

    def test_workers_ping_healthy(self) -> None:
        """Ping a 'healthy' mocked transport."""
        from unittest.mock import AsyncMock, patch  # noqa: PLC0415

        runner = CliRunner()
        with (
            patch(
                "replicate_mcp.distributed.HttpWorkerTransport.health_check",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "replicate_mcp.distributed.HttpWorkerTransport.get_metrics",
                new=AsyncMock(return_value={"active_tasks": 0, "total_processed": 5}),
            ),
        ):
            result = runner.invoke(
                app, ["workers", "ping", "http://localhost:7999"], catch_exceptions=False
            )
        assert result.exit_code == 0
        assert "healthy" in result.output.lower()

    def test_workers_ping_unreachable_exits_1(self) -> None:
        """Ping an unreachable worker exits with code 1."""
        from unittest.mock import AsyncMock, patch  # noqa: PLC0415

        runner = CliRunner()
        with patch(
            "replicate_mcp.distributed.HttpWorkerTransport.health_check",
            new=AsyncMock(return_value=False),
        ):
            result = runner.invoke(app, ["workers", "ping", "http://localhost:7999"])
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Phase 4 — agents run extended flags
# ---------------------------------------------------------------------------


class TestAgentsRunExtended:
    def test_agents_run_json_flag_help(self) -> None:
        result = _run("agents", "run", "--help")
        assert "--json" in result.output
        assert "--model" in result.output

    def test_agents_run_missing_token_exits_1(self) -> None:
        """agents run should exit 1 when REPLICATE_API_TOKEN is not set."""
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["agents", "run", "llama3_chat", "--input", '{"prompt": "hi"}'],
            env={"REPLICATE_API_TOKEN": ""},
        )
        assert result.exit_code == 1

    def test_workflows_list_output(self) -> None:
        """workflows list should not crash and print a table."""
        result = _run("workflows", "list")
        assert result.exit_code == 0

    def test_workflows_run_registered_without_token_exits_1(self) -> None:
        """If workflow is registered but token is missing, exit 1."""
        from replicate_mcp.sdk import WorkflowBuilder, register_workflow  # noqa: PLC0415

        spec = WorkflowBuilder("cli-test-wf").then("some_agent").build()
        register_workflow(spec)
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["workflows", "run", "cli-test-wf", "--input", '{"k": "v"}'],
            env={"REPLICATE_API_TOKEN": ""},
        )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# Phase 5a — audit CLI commands
# ---------------------------------------------------------------------------


class TestAuditCommands:
    """Tests for the `audit` CLI subgroup."""

    @pytest.fixture()
    def audit_log(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        """Fixture that redirects the default audit log path to a temp file and
        pre-populates it with a handful of sample records."""
        from replicate_mcp.utils.audit import AuditLogger  # noqa: PLC0415

        log_path = tmp_path / "audit.jsonl"
        # Patch the module-level default so that AuditLogger() uses the tmp path
        monkeypatch.setattr("replicate_mcp.utils.audit._DEFAULT_AUDIT_PATH", log_path)
        log = AuditLogger(path=log_path)
        log.record(
            agent="summariser", model="meta/llama", latency_ms=3000.0, cost_usd=0.002, success=True
        )
        log.record(
            agent="classifier", model="meta/llama", latency_ms=1500.0, cost_usd=0.001, success=True
        )
        log.record(
            agent="summariser",
            model="black-forest-labs/flux",
            latency_ms=5000.0,
            cost_usd=0.003,
            success=False,
        )
        return log

    def test_audit_help(self) -> None:
        result = _run("audit", "--help")
        assert result.exit_code == 0
        assert "audit" in result.output.lower()

    def test_audit_tail_help(self) -> None:
        result = _run("audit", "tail", "--help")
        assert result.exit_code == 0
        assert "--n" in result.output

    def test_audit_costs_help(self) -> None:
        result = _run("audit", "costs", "--help")
        assert result.exit_code == 0
        assert "--period" in result.output

    def test_audit_stats_help(self) -> None:
        result = _run("audit", "stats", "--help")
        assert result.exit_code == 0

    def test_audit_clear_help(self) -> None:
        result = _run("audit", "clear", "--help")
        assert result.exit_code == 0

    def test_audit_tail_empty_log(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """tail prints a helpful message when log is absent."""
        monkeypatch.setattr(
            "replicate_mcp.utils.audit._DEFAULT_AUDIT_PATH", tmp_path / "absent.jsonl"
        )
        result = _run("audit", "tail")
        assert result.exit_code == 0

    def test_audit_tail_with_records(self, audit_log: Any) -> None:
        """tail prints a table of records."""
        result = _run("audit", "tail")
        assert result.exit_code == 0
        assert "summariser" in result.output or "meta/llama" in result.output

    def test_audit_tail_n_option(self, audit_log: Any) -> None:
        """--n limits the number of rows shown."""
        result = _run("audit", "tail", "--n", "1")
        assert result.exit_code == 0

    def test_audit_tail_filter_agent(self, audit_log: Any) -> None:
        """--agent filters by agent name."""
        result = _run("audit", "tail", "--agent", "summariser")
        assert result.exit_code == 0

    def test_audit_costs_no_log(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """costs prints a message when log is absent."""
        monkeypatch.setattr(
            "replicate_mcp.utils.audit._DEFAULT_AUDIT_PATH", tmp_path / "absent.jsonl"
        )
        result = _run("audit", "costs")
        assert result.exit_code == 0

    def test_audit_costs_with_records(self, audit_log: Any) -> None:
        """costs shows the spend table."""
        result = _run("audit", "costs", "--period", "all")
        assert result.exit_code == 0
        assert "TOTAL" in result.output

    def test_audit_costs_period_choices(self, audit_log: Any) -> None:
        """costs should accept all valid period values."""
        for period in ("today", "week", "month", "all"):
            result = _run("audit", "costs", "--period", period)
            assert result.exit_code == 0, f"Failed for period={period}"

    def test_audit_stats_no_log(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "replicate_mcp.utils.audit._DEFAULT_AUDIT_PATH", tmp_path / "absent.jsonl"
        )
        result = _run("audit", "stats")
        assert result.exit_code == 0

    def test_audit_stats_with_records(self, audit_log: Any) -> None:
        result = _run("audit", "stats", "--period", "all")
        assert result.exit_code == 0

    def test_audit_stats_filter_agent(self, audit_log: Any) -> None:
        result = _run("audit", "stats", "summariser", "--period", "all")
        assert result.exit_code == 0

    def test_audit_stats_no_match(self, audit_log: Any) -> None:
        result = _run("audit", "stats", "nonexistent_agent")
        assert result.exit_code == 0

    def test_audit_stats_period_choices(self, audit_log: Any) -> None:
        for period in ("today", "week", "month", "all"):
            result = _run("audit", "stats", "--period", period)
            assert result.exit_code == 0, f"Failed for period={period}"

    def test_audit_clear_no_log(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "replicate_mcp.utils.audit._DEFAULT_AUDIT_PATH", tmp_path / "absent.jsonl"
        )
        runner = CliRunner()
        result = runner.invoke(app, ["audit", "clear"], input="y\n")
        assert result.exit_code == 0

    def test_audit_clear_with_log(self, audit_log: Any) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["audit", "clear"], input="y\n")
        assert result.exit_code == 0
        assert not audit_log.exists()

    def test_audit_clear_aborted(self, audit_log: Any) -> None:
        runner = CliRunner()
        runner.invoke(app, ["audit", "clear"], input="n\n")
        # User said no — file should still exist
        assert audit_log.exists()


# ---------------------------------------------------------------------------
# Phase 5a — serve --workflows-file
# ---------------------------------------------------------------------------


class TestServeWorkflowsFile:
    def test_serve_help_includes_workflows_file(self) -> None:
        result = _run("serve", "--help")
        assert "--workflows-file" in result.output

    def test_serve_workflows_file_bad_path_skips_gracefully(self) -> None:
        """Non-existent workflow file should not crash the serve command."""
        # The serve command will try to load the file before starting the server.
        # Since the file doesn't exist, click's Path(exists=True) validation rejects it.
        result = _run("serve", "--workflows-file", "/nonexistent/path/wf.yaml")
        # click raises UsageError for invalid path
        assert result.exit_code != 0

    def test_serve_workflows_file_loads_successfully(self, tmp_path: Path) -> None:
        """A valid workflow file should be loaded at serve startup."""
        from unittest.mock import patch  # noqa: PLC0415

        from replicate_mcp.sdk import reset_workflow_registry  # noqa: PLC0415

        reset_workflow_registry()

        wf_file = tmp_path / "wf.yaml"
        wf_file.write_text("workflows:\n  - name: serve-test-wf\n    steps:\n      - agent: a\n")

        runner = CliRunner()
        # We must mock the actual server start so the command returns
        with patch("replicate_mcp.server.serve") as mock_serve:
            mock_serve.return_value = None
            result = runner.invoke(
                app,
                ["serve", "--workflows-file", str(wf_file)],
                catch_exceptions=False,
            )

        assert result.exit_code == 0
        # The loaded workflow should have been registered
        from replicate_mcp.sdk import get_workflow  # noqa: PLC0415

        assert get_workflow("serve-test-wf") is not None
        reset_workflow_registry()


# ---------------------------------------------------------------------------
# Phase 6 — doctor command
# ---------------------------------------------------------------------------


class TestDoctorCommand:
    def test_doctor_help(self) -> None:
        result = _run("doctor", "--help")
        assert result.exit_code == 0
        assert "diagnostic" in result.output.lower() or "health" in result.output.lower()

    def test_doctor_no_token_shows_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        result = _run("doctor")
        assert result.exit_code == 0  # doctor never hard-fails, just reports
        assert "not set" in result.output or "API Token" in result.output

    def test_doctor_with_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        result = _run("doctor")
        assert result.exit_code == 0
        assert "API Token" in result.output

    def test_doctor_shows_python_version(self) -> None:
        result = _run("doctor")
        assert result.exit_code == 0
        import sys  # noqa: PLC0415

        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
        assert py_ver in result.output

    def test_doctor_shows_package_check(self) -> None:
        result = _run("doctor")
        assert result.exit_code == 0
        assert "Package" in result.output or "depend" in result.output.lower()

    def test_doctor_shows_audit_log_status(self) -> None:
        result = _run("doctor")
        assert result.exit_code == 0
        assert "Audit" in result.output or "audit" in result.output.lower()

    def test_doctor_shows_router_state(self) -> None:
        result = _run("doctor")
        assert result.exit_code == 0
        assert "Router" in result.output or "router" in result.output.lower()

    def test_doctor_summary_panel(self) -> None:
        result = _run("doctor")
        assert result.exit_code == 0
        assert "Doctor Report" in result.output

    def test_doctor_bad_token_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Token with unexpected format should show warning but still pass."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "short-token")
        result = _run("doctor")
        assert result.exit_code == 0
        assert "unexpected" in result.output.lower() or "format" in result.output.lower()

    def test_doctor_api_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When API is unreachable, doctor should report it but not fail."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        from unittest.mock import patch  # noqa: PLC0415

        with patch("httpx.get", side_effect=ConnectionError("network down")):
            result = _run("doctor")
        assert result.exit_code == 0
        assert "unreachable" in result.output.lower() or "API" in result.output

    def test_doctor_api_returns_500(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When API returns 5xx, doctor should report failure."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        from unittest.mock import MagicMock, patch  # noqa: PLC0415

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        with patch("httpx.get", return_value=mock_resp):
            result = _run("doctor")
        assert result.exit_code == 0
        assert "503" in result.output or "API" in result.output

    def test_doctor_audit_log_with_records(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Doctor should report audit log record count when records exist."""
        from replicate_mcp.utils.audit import AuditLogger  # noqa: PLC0415

        log_path = tmp_path / "audit.jsonl"
        monkeypatch.setattr("replicate_mcp.utils.audit._DEFAULT_AUDIT_PATH", log_path)
        log = AuditLogger(path=log_path)
        log.record(agent="test_agent", model="m1", latency_ms=100.0, cost_usd=0.001, success=True)

        result = _run("doctor")
        assert result.exit_code == 0
        assert "record" in result.output.lower() or "Audit" in result.output

    def test_doctor_router_state_with_models(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Doctor should show model count when router state exists."""
        from replicate_mcp.routing import CostAwareRouter  # noqa: PLC0415
        from replicate_mcp.utils.router_state import RouterStateManager  # noqa: PLC0415

        state_path = tmp_path / "router-state.json"
        monkeypatch.setattr("replicate_mcp.utils.router_state._DEFAULT_STATE_PATH", state_path)
        mgr = RouterStateManager(path=state_path)
        router = CostAwareRouter()
        router.register_model("a/model", initial_cost=0.01)
        mgr.save_router(router)

        result = _run("doctor")
        assert result.exit_code == 0
        assert "model" in result.output.lower() or "Router" in result.output

    def test_doctor_audit_log_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Doctor should handle audit log exceptions gracefully."""
        from unittest.mock import patch  # noqa: PLC0415

        with patch("replicate_mcp.utils.audit.AuditLogger", side_effect=RuntimeError("boom")):
            result = _run("doctor")
        assert result.exit_code == 0
        assert "error" in result.output.lower() or "Doctor Report" in result.output

    def test_doctor_router_state_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Doctor should handle router state exceptions gracefully."""
        from unittest.mock import patch  # noqa: PLC0415

        with patch(
            "replicate_mcp.utils.router_state.RouterStateManager",
            side_effect=RuntimeError("state err"),
        ):
            result = _run("doctor")
        assert result.exit_code == 0
        assert "error" in result.output.lower() or "Router" in result.output

    def test_doctor_config_dir_not_writable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Doctor should report when config dir is not writable."""
        from unittest.mock import patch  # noqa: PLC0415

        with patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")):
            result = _run("doctor")
        assert result.exit_code == 0
        assert "not writable" in result.output.lower() or "permission" in result.output.lower()


# ---------------------------------------------------------------------------
# Phase 6 — dry-run flag
# ---------------------------------------------------------------------------


class TestDryRunFlag:
    def test_dry_run_help_shows_flag(self) -> None:
        result = _run("agents", "run", "--help")
        assert result.exit_code == 0
        assert "--dry-run" in result.output

    def test_dry_run_no_token_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """dry-run still requires a token for model resolution."""
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        result = _run(
            "agents",
            "run",
            "llama3_chat",
            "--dry-run",
            "--input",
            '{"prompt": "test"}',
        )
        assert result.exit_code == 1

    def test_dry_run_with_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        result = _run(
            "agents",
            "run",
            "llama3_chat",
            "--dry-run",
            "--input",
            '{"prompt": "test"}',
        )
        assert result.exit_code == 0
        assert "Dry-Run Report" in result.output
        assert "No API call was made" in result.output

    def test_dry_run_shows_model_info(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        result = _run(
            "agents",
            "run",
            "llama3_chat",
            "--dry-run",
            "--input",
            '{"prompt": "hello"}',
        )
        assert result.exit_code == 0
        assert "Agent ID" in result.output
        assert "Model" in result.output
        assert "Input keys" in result.output

    def test_dry_run_shows_streaming_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        result = _run(
            "agents",
            "run",
            "llama3_chat",
            "--dry-run",
            "--no-stream",
            "--input",
            '{"prompt": "test"}',
        )
        assert result.exit_code == 0
        assert "Streaming" in result.output

    def test_dry_run_invalid_agent_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        result = _run(
            "agents",
            "run",
            "9bad_id!",
            "--dry-run",
            "--input",
            '{"prompt": "test"}',
        )
        assert result.exit_code == 1

    def test_dry_run_with_model_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        result = _run(
            "agents",
            "run",
            "llama3_chat",
            "--dry-run",
            "--model",
            "meta/llama-3-70b",
            "--input",
            '{"prompt": "test"}',
        )
        assert result.exit_code == 0
        assert "meta/llama-3-70b" in result.output

    def test_dry_run_invalid_json_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        result = _run(
            "agents",
            "run",
            "llama3_chat",
            "--dry-run",
            "--input",
            "not-json",
        )
        assert result.exit_code == 1

    def test_dry_run_with_router_state_data(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """dry-run should show estimated cost/latency when router state has EMA data."""
        from replicate_mcp.routing import CostAwareRouter  # noqa: PLC0415
        from replicate_mcp.utils.router_state import RouterStateManager  # noqa: PLC0415

        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)

        # Write router state with EMA data for the llama3_chat model
        state_path = tmp_path / "router-state.json"
        mgr = RouterStateManager(path=state_path)
        router = CostAwareRouter()
        model_id = "meta/llama-3-8b-instruct"
        router.register_model(model_id, initial_cost=0.003, initial_latency_ms=2500)
        router.record_outcome(model_id, latency_ms=2600, cost_usd=0.0031, success=True)
        mgr.save_router(router)

        # Patch the default state path so doctor/dry-run finds our state
        monkeypatch.setattr("replicate_mcp.utils.router_state._DEFAULT_STATE_PATH", state_path)

        result = _run(
            "agents",
            "run",
            "llama3_chat",
            "--dry-run",
            "--input",
            '{"prompt": "test"}',
        )
        assert result.exit_code == 0
        assert "Dry-Run Report" in result.output
        # When router has data, it should show dollar amount (not "cold start")
        assert "$" in result.output or "Est." in result.output

    def test_dry_run_model_resolution_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """dry-run with a model override that can't be resolved should exit 1."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        # Use an agent_id that isn't in DEFAULT_MODEL_MAP and provide no --model
        # We need to trigger the resolve_model exception path
        from unittest.mock import patch  # noqa: PLC0415

        with patch(
            "replicate_mcp.agents.execution.AgentExecutor.resolve_model",
            side_effect=RuntimeError("no model found"),
        ):
            result = _run(
                "agents",
                "run",
                "llama3_chat",
                "--dry-run",
                "--input",
                '{"prompt": "test"}',
            )
        assert result.exit_code == 1
        assert "Model resolution failed" in result.output


# ---------------------------------------------------------------------------
# Coverage: agents run streaming path
# ---------------------------------------------------------------------------


class TestAgentsRunStreaming:
    """Tests for the actual streaming execution path in `agents run`."""

    def _make_async_gen(self, chunks: list[dict[str, Any]]) -> Any:
        """Create an async generator that yields the given chunks."""

        async def _gen(*args: Any, **kwargs: Any) -> Any:
            for chunk in chunks:
                yield chunk

        return _gen

    def test_agents_run_streaming_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """agents run with streaming should show progress and output panel."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        from unittest.mock import patch  # noqa: PLC0415

        chunks = [
            {"chunk": "Hello "},
            {"chunk": "world"},
            {"done": True, "output": "Hello world", "latency_ms": 1500.0},
        ]
        with patch(
            "replicate_mcp.agents.execution.AgentExecutor.run",
            new=self._make_async_gen(chunks),
        ):
            result = _run("agents", "run", "llama3_chat", "--input", '{"prompt": "hi"}')
        assert result.exit_code == 0
        assert "Hello world" in result.output or "✓" in result.output

    def test_agents_run_streaming_error_chunk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """agents run should display error when chunk contains error."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        from unittest.mock import patch  # noqa: PLC0415

        chunks = [{"error": "model overload"}]
        with patch(
            "replicate_mcp.agents.execution.AgentExecutor.run",
            new=self._make_async_gen(chunks),
        ):
            result = _run("agents", "run", "llama3_chat", "--input", '{"prompt": "hi"}')
        # Should show error message
        assert "model overload" in result.output or "Error" in result.output

    def test_agents_run_json_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """agents run --json should output raw JSON chunks."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        from unittest.mock import patch  # noqa: PLC0415

        chunks = [
            {"chunk": "test"},
            {"done": True, "output": "test", "latency_ms": 500.0},
        ]
        with patch(
            "replicate_mcp.agents.execution.AgentExecutor.run",
            new=self._make_async_gen(chunks),
        ):
            result = _run(
                "agents",
                "run",
                "llama3_chat",
                "--json",
                "--input",
                '{"prompt": "hi"}',
            )
        assert result.exit_code == 0

    def test_agents_run_with_model_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """agents run --model should override the default model path."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        from unittest.mock import patch  # noqa: PLC0415

        chunks = [{"done": True, "output": "ok", "latency_ms": 200.0}]
        with patch(
            "replicate_mcp.agents.execution.AgentExecutor.run",
            new=self._make_async_gen(chunks),
        ):
            result = _run(
                "agents",
                "run",
                "llama3_chat",
                "--model",
                "meta/llama-3-70b",
                "--input",
                '{"prompt": "hi"}',
            )
        assert result.exit_code == 0
        assert "meta/llama-3-70b" in result.output or "✓" in result.output

    def test_agents_run_no_stream_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """agents run --no-stream should work."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        from unittest.mock import patch  # noqa: PLC0415

        chunks = [{"done": True, "output": "result", "latency_ms": 100.0}]
        with patch(
            "replicate_mcp.agents.execution.AgentExecutor.run",
            new=self._make_async_gen(chunks),
        ):
            result = _run(
                "agents",
                "run",
                "llama3_chat",
                "--no-stream",
                "--input",
                '{"prompt": "hi"}',
            )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Coverage: workers start full path
# ---------------------------------------------------------------------------


class TestWorkersStart:
    """Tests for the `workers start` command execution paths."""

    def test_workers_start_no_token_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """workers start should exit 1 when no API token is set."""
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        result = _run("workers", "start")
        assert result.exit_code == 1
        assert "REPLICATE_API_TOKEN" in result.output

    def test_workers_start_with_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """workers start should show the startup panel when token is set."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        from unittest.mock import AsyncMock, patch  # noqa: PLC0415

        with patch("replicate_mcp.worker_server.serve_worker", new=AsyncMock()):
            result = _run("workers", "start", "--port", "7999")
        assert result.exit_code == 0
        assert "Starting Worker" in result.output or "Worker Node" in result.output

    def test_workers_start_custom_options(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """workers start should pass custom options through."""
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)
        from unittest.mock import AsyncMock, patch  # noqa: PLC0415

        with patch("replicate_mcp.worker_server.serve_worker", new=AsyncMock()) as mock_serve:
            result = _run(
                "workers",
                "start",
                "--host",
                "0.0.0.0",  # noqa: S104
                "--port",
                "8888",
                "--node-id",
                "gpu-1",
                "--concurrency",
                "4",
            )
        assert result.exit_code == 0
        mock_serve.assert_called_once()
        kwargs = mock_serve.call_args.kwargs
        assert kwargs["host"] == "0.0.0.0"  # noqa: S104
        assert kwargs["port"] == 8888


# ---------------------------------------------------------------------------
# Coverage: doctor failed checks panel
# ---------------------------------------------------------------------------


class TestDoctorFailedPanel:
    """Test that doctor shows the red failed panel when checks fail."""

    def test_doctor_old_python_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Doctor should show failure when Python version is < 3.10."""
        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        # sys.version_info is a C type that can't be instantiated directly,
        # so we create a tuple subclass with .major/.minor/.micro attributes.
        _FakeVI = type(  # noqa: PYI024,N806
            "version_info",
            (tuple,),
            {
                "major": property(lambda self: self[0]),
                "minor": property(lambda self: self[1]),
                "micro": property(lambda self: self[2]),
            },
        )
        monkeypatch.setattr("sys.version_info", _FakeVI((3, 9, 0)))
        result = _run("doctor")
        assert result.exit_code == 0
        # Should show the failure message about Python version
        assert "3.9.0" in result.output or "requires 3.10" in result.output

    def test_doctor_missing_dep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Doctor should report missing dependencies."""
        from unittest.mock import patch  # noqa: PLC0415

        monkeypatch.delenv("REPLICATE_API_TOKEN", raising=False)
        # Make one of the checked deps fail to import
        real_import = __import__

        def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "yaml":
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_fake_import):
            result = _run("doctor")
        assert result.exit_code == 0
        assert "missing" in result.output.lower() or "yaml" in result.output.lower()


# ---------------------------------------------------------------------------
# Coverage: workflow run execution path
# ---------------------------------------------------------------------------


class TestWorkflowRunExecution:
    """Test the actual workflow execution path with mocked executor."""

    def test_workflow_run_with_registered_workflow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Running a registered workflow should execute steps."""
        from unittest.mock import patch  # noqa: PLC0415

        from replicate_mcp.sdk import (  # noqa: PLC0415
            WorkflowBuilder,
            register_workflow,
            reset_workflow_registry,
        )

        reset_workflow_registry()
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)

        spec = WorkflowBuilder("test-wf-exec").then("step1_agent").build()
        register_workflow(spec)

        async def _mock_run(*args: Any, **kwargs: Any) -> Any:
            yield {"done": True, "output": "step1 result", "latency_ms": 500.0}

        with patch("replicate_mcp.agents.execution.AgentExecutor.run", new=_mock_run):
            result = _run(
                "workflows",
                "run",
                "test-wf-exec",
                "--input",
                '{"prompt": "test"}',
            )
        assert result.exit_code == 0
        assert "test-wf-exec" in result.output or "step1_agent" in result.output
        reset_workflow_registry()

    def test_workflow_run_json_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """workflows run --json should output raw JSON."""
        from unittest.mock import patch  # noqa: PLC0415

        from replicate_mcp.sdk import (  # noqa: PLC0415
            WorkflowBuilder,
            register_workflow,
            reset_workflow_registry,
        )

        reset_workflow_registry()
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)

        spec = WorkflowBuilder("test-wf-json").then("agent_a").build()
        register_workflow(spec)

        async def _mock_run(*args: Any, **kwargs: Any) -> Any:
            yield {"done": True, "output": "done", "latency_ms": 100.0}

        with patch("replicate_mcp.agents.execution.AgentExecutor.run", new=_mock_run):
            result = _run(
                "workflows",
                "run",
                "test-wf-json",
                "--json",
                "--input",
                '{"prompt": "test"}',
            )
        assert result.exit_code == 0
        reset_workflow_registry()

    def test_workflow_run_with_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """workflows run --timeout should be accepted."""
        from unittest.mock import patch  # noqa: PLC0415

        from replicate_mcp.sdk import (  # noqa: PLC0415
            WorkflowBuilder,
            register_workflow,
            reset_workflow_registry,
        )

        reset_workflow_registry()
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)

        spec = WorkflowBuilder("test-wf-timeout").then("agent_a").build()
        register_workflow(spec)

        async def _mock_run(*args: Any, **kwargs: Any) -> Any:
            yield {"done": True, "output": "ok", "latency_ms": 50.0}

        with patch("replicate_mcp.agents.execution.AgentExecutor.run", new=_mock_run):
            result = _run(
                "workflows",
                "run",
                "test-wf-timeout",
                "--timeout",
                "60",
                "--input",
                '{"prompt": "test"}',
            )
        assert result.exit_code == 0
        reset_workflow_registry()

    def test_workflow_run_with_checkpoint_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """workflows run --checkpoint-dir should save checkpoint files."""
        from unittest.mock import patch  # noqa: PLC0415

        from replicate_mcp.sdk import (  # noqa: PLC0415
            WorkflowBuilder,
            register_workflow,
            reset_workflow_registry,
        )

        reset_workflow_registry()
        monkeypatch.setenv("REPLICATE_API_TOKEN", "r" + "A" * 38)

        spec = WorkflowBuilder("test-wf-checkpoint").then("agent_a").build()
        register_workflow(spec)

        async def _mock_run(*args: Any, **kwargs: Any) -> Any:
            yield {"done": True, "output": "ok", "latency_ms": 50.0}

        ckpt_dir = tmp_path / "checkpoints"
        with patch("replicate_mcp.agents.execution.AgentExecutor.run", new=_mock_run):
            result = _run(
                "workflows",
                "run",
                "test-wf-checkpoint",
                "--checkpoint-dir",
                str(ckpt_dir),
                "--input",
                '{"prompt": "test"}',
            )
        assert result.exit_code == 0
        # Checkpoint file should have been created
        ckpt_files = list(ckpt_dir.glob("*.json"))
        assert len(ckpt_files) >= 1
        reset_workflow_registry()
