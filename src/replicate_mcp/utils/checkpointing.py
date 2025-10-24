"""Workflow checkpoint persistence utilities."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


@dataclass
class CheckpointManager:
    """Persist workflow state to the filesystem."""

    base_path: Path

    def __post_init__(self) -> None:
        self.base_path.mkdir(parents=True, exist_ok=True)

    def save(self, session_id: str, state: dict[str, Any]) -> Path:
        path = self.base_path / f"{session_id}.json"
        path.write_text(json.dumps(state, indent=2))
        return path

    def load(self, session_id: str) -> dict[str, Any]:
        path = self.base_path / f"{session_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint {session_id} not found")
        return cast(dict[str, Any], json.loads(path.read_text()))


__all__ = ["CheckpointManager"]
