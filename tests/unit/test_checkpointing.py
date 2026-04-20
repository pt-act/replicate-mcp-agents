"""Unit tests for the checkpointing module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from replicate_mcp.utils.checkpointing import CheckpointManager


@pytest.fixture()
def ckpt(tmp_path: Path) -> CheckpointManager:
    """Create a CheckpointManager rooted in a temp directory."""
    return CheckpointManager(base_path=tmp_path / "checkpoints")


class TestCheckpointManager:
    """Tests for filesystem checkpoint persistence."""

    def test_base_path_created(self, tmp_path: Path) -> None:
        bp = tmp_path / "deep" / "nested" / "dir"
        assert not bp.exists()
        CheckpointManager(base_path=bp)
        assert bp.is_dir()

    def test_save_creates_file(self, ckpt: CheckpointManager) -> None:
        state = {"step": 1, "data": "hello"}
        path = ckpt.save("sess-1", state)
        assert path.exists()
        assert path.name == "sess-1.json"
        assert json.loads(path.read_text()) == state

    def test_load_returns_saved_state(self, ckpt: CheckpointManager) -> None:
        state = {"step": 2, "nested": {"key": [1, 2, 3]}}
        ckpt.save("sess-2", state)
        loaded = ckpt.load("sess-2")
        assert loaded == state

    def test_load_missing_raises(self, ckpt: CheckpointManager) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            ckpt.load("nonexistent")

    def test_save_overwrites_existing(self, ckpt: CheckpointManager) -> None:
        ckpt.save("sess-3", {"v": 1})
        ckpt.save("sess-3", {"v": 2})
        assert ckpt.load("sess-3") == {"v": 2}

    def test_multiple_sessions(self, ckpt: CheckpointManager) -> None:
        ckpt.save("a", {"session": "a"})
        ckpt.save("b", {"session": "b"})
        assert ckpt.load("a")["session"] == "a"
        assert ckpt.load("b")["session"] == "b"

    def test_save_empty_state(self, ckpt: CheckpointManager) -> None:
        ckpt.save("empty", {})
        assert ckpt.load("empty") == {}

    def test_save_complex_state(self, ckpt: CheckpointManager) -> None:
        state = {
            "step": 5,
            "agents": ["a", "b"],
            "results": {"a": {"output": "hello"}, "b": None},
            "cost": 0.123,
        }
        ckpt.save("complex", state)
        assert ckpt.load("complex") == state

    def test_json_formatting(self, ckpt: CheckpointManager) -> None:
        """Verify saved JSON is pretty-printed (indent=2)."""
        ckpt.save("fmt", {"k": "v"})
        path = ckpt.base_path / "fmt.json"
        text = path.read_text()
        assert "\n" in text  # multi-line
        assert "  " in text  # indented