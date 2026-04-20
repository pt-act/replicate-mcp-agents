"""Unit tests for the checkpointing module (v2 — atomic writes, versioning)."""

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
        assert "\n" in text
        assert "  " in text

    # ---- v2 features ----

    def test_version_tracking(self, ckpt: CheckpointManager) -> None:
        assert ckpt.version("s") == 0
        ckpt.save("s", {"v": 1})
        assert ckpt.version("s") == 1
        ckpt.save("s", {"v": 2})
        assert ckpt.version("s") == 2

    def test_list_sessions(self, ckpt: CheckpointManager) -> None:
        assert ckpt.list_sessions() == []
        ckpt.save("beta", {})
        ckpt.save("alpha", {})
        assert ckpt.list_sessions() == ["alpha", "beta"]

    def test_delete(self, ckpt: CheckpointManager) -> None:
        ckpt.save("del-me", {"x": 1})
        assert ckpt.exists("del-me")
        ckpt.delete("del-me")
        assert not ckpt.exists("del-me")

    def test_delete_missing_raises(self, ckpt: CheckpointManager) -> None:
        with pytest.raises(FileNotFoundError):
            ckpt.delete("nonexistent")

    def test_exists(self, ckpt: CheckpointManager) -> None:
        assert not ckpt.exists("x")
        ckpt.save("x", {})
        assert ckpt.exists("x")

    def test_atomic_write_envelope(self, ckpt: CheckpointManager) -> None:
        """Verify the saved file has the envelope format."""
        ckpt.save("env", {"k": "v"})
        raw = json.loads((ckpt.base_path / "env.json").read_text())
        assert "_meta" in raw
        assert raw["_meta"]["session_id"] == "env"
        assert raw["_meta"]["version"] == 1
        assert "saved_at" in raw["_meta"]
        assert raw["state"] == {"k": "v"}

    def test_no_temp_files_left_on_success(self, ckpt: CheckpointManager) -> None:
        ckpt.save("clean", {"ok": True})
        tmp_files = list(ckpt.base_path.glob(".*"))
        assert len(tmp_files) == 0