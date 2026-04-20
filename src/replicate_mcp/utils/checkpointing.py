"""Workflow checkpoint persistence utilities.

Features:
    - Atomic writes via ``tempfile`` + ``os.replace``
    - Monotonic version tracking per session
    - List / delete operations
    - Crash-safe — partial writes never corrupt existing checkpoints
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast


@dataclass
class CheckpointManager:
    """Persist workflow state to the filesystem with atomic writes."""

    base_path: Path
    _versions: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _path_for(self, session_id: str) -> Path:
        return self.base_path / f"{session_id}.json"

    def save(self, session_id: str, state: dict[str, Any]) -> Path:
        """Atomically write a checkpoint file.

        Writes to a temp file in the same directory, then uses
        ``os.replace`` (POSIX-atomic) to swap into place.  This
        guarantees that a crash mid-write won't corrupt the
        previous checkpoint.
        """
        version = self._versions.get(session_id, 0) + 1
        self._versions[session_id] = version

        envelope = {
            "_meta": {
                "version": version,
                "session_id": session_id,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            },
            "state": state,
        }

        target = self._path_for(session_id)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.base_path),
            prefix=f".{session_id}_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(envelope, f, indent=2)
            os.replace(tmp_path, str(target))
        except BaseException:
            # Clean up temp file on any failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        return target

    def load(self, session_id: str) -> dict[str, Any]:
        """Load checkpoint state, stripping the metadata envelope."""
        path = self._path_for(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint {session_id} not found")

        raw = json.loads(path.read_text())

        # Support both old (flat) and new (envelope) formats
        if isinstance(raw, dict) and "state" in raw and "_meta" in raw:
            self._versions[session_id] = raw["_meta"].get("version", 0)
            return cast(dict[str, Any], raw["state"])
        return cast(dict[str, Any], raw)

    def version(self, session_id: str) -> int:
        """Return the current version number for a session."""
        return self._versions.get(session_id, 0)

    def list_sessions(self) -> list[str]:
        """Return session IDs for all saved checkpoints."""
        return sorted(
            p.stem for p in self.base_path.glob("*.json")
            if not p.name.startswith(".")
        )

    def delete(self, session_id: str) -> None:
        """Delete a checkpoint file.

        Raises FileNotFoundError if the checkpoint does not exist.
        """
        path = self._path_for(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint {session_id} not found")
        path.unlink()
        self._versions.pop(session_id, None)

    def exists(self, session_id: str) -> bool:
        """Check whether a checkpoint exists."""
        return self._path_for(session_id).exists()


__all__ = ["CheckpointManager"]
