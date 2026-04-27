"""Persistent storage for CostAwareRouter learned statistics.

Router state (EMA latency/cost/quality, Thompson Sampling posteriors, invocation
counts) accumulates real signal from production API calls.  Without persistence
it evaporates on every process restart, forcing a costly cold-start re-exploration
cycle that wastes money and degrades routing quality for every deployment.

This module provides :class:`RouterStateManager`, which saves and loads router
state atomically using the same ``tempfile + os.replace`` pattern as
:class:`~replicate_mcp.utils.checkpointing.CheckpointManager`.

Key design decisions:
    - The file is written to ``~/.replicate/router-state.json`` by default and is
      human-readable JSON so operators can inspect, edit, or delete it easily.
    - Saves are atomic (crash-safe).  A partial write never corrupts the
      previous snapshot.
    - :meth:`RouterStateManager.save_router` / :meth:`load_into_router` are the
      only public methods, keeping the coupling surface minimal.
    - :class:`~replicate_mcp.routing.CostAwareRouter` gains two thin helpers
      (``dump_state`` / ``load_state``) that serialise / deserialise the
      ``_stats`` dict without breaking encapsulation.

Usage::

    from replicate_mcp.routing import CostAwareRouter
    from replicate_mcp.utils.router_state import RouterStateManager

    router = CostAwareRouter()
    manager = RouterStateManager()          # default path ~/.replicate/router-state.json

    # Restore learned statistics at startup:
    manager.load_into_router(router)

    # ...run many invocations...

    # Persist statistics (call periodically or on shutdown):
    manager.save_router(router)
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from replicate_mcp.routing import CostAwareRouter, ModelStats

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default path
# ---------------------------------------------------------------------------

_DEFAULT_STATE_PATH = Path.home() / ".replicate" / "router-state.json"

# Bump this when the serialised schema changes in a backward-incompatible way.
_SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# RouterStateManager
# ---------------------------------------------------------------------------


@dataclass
class RouterStateManager:
    """Persist and restore :class:`~replicate_mcp.routing.CostAwareRouter` state.

    Args:
        path: File path for the JSON state snapshot.  Defaults to
              ``~/.replicate/router-state.json``.

    Example::

        router  = CostAwareRouter(strategy="thompson")
        manager = RouterStateManager()

        manager.load_into_router(router)   # no-op if file absent

        # ... run traffic ...

        manager.save_router(router)        # atomic write
    """

    path: Path = _DEFAULT_STATE_PATH

    def __post_init__(self) -> None:
        self.path = Path(self.path)

    # ---- public API ----

    def save_router(self, router: CostAwareRouter) -> Path:
        """Serialise *router* statistics to :attr:`path` atomically.

        Creates parent directories if they do not exist.  Writes to a
        temporary file in the same directory, then atomically renames it
        into place so a crash mid-write never corrupts the previous snapshot.

        Returns:
            The path the snapshot was written to.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)

        state = router.dump_state()
        envelope: dict[str, Any] = {
            "_meta": {
                "schema_version": _SCHEMA_VERSION,
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "strategy": router.strategy,
                "model_count": len(state),
            },
            "models": state,
        }

        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent),
            prefix=".router-state-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(envelope, fh, indent=2)
            os.replace(tmp, str(self.path))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

        logger.debug(
            "Router state saved: %d model(s) → %s",
            len(state),
            self.path,
        )
        return self.path

    def load_into_router(self, router: CostAwareRouter) -> int:
        """Deserialise statistics from :attr:`path` into *router*.

        If the file does not exist this is a safe no-op (returns 0).
        Malformed or version-incompatible files are logged as warnings and
        skipped — the router starts fresh rather than crashing.

        Returns:
            Number of model entries restored (0 if file absent or skipped).
        """
        if not self.path.exists():
            logger.debug("No router state file at %s — starting fresh", self.path)
            return 0

        try:
            raw = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read router state from %s: %s", self.path, exc)
            return 0

        schema_version = raw.get("_meta", {}).get("schema_version", 0)
        if schema_version != _SCHEMA_VERSION:
            logger.warning(
                "Router state schema version mismatch (file=%s, expected=%s) — "
                "ignoring stale snapshot",
                schema_version,
                _SCHEMA_VERSION,
            )
            return 0

        models_raw: dict[str, Any] = raw.get("models", {})
        router.load_state(models_raw)
        count = len(models_raw)
        logger.info(
            "Router state restored: %d model(s) from %s",
            count,
            self.path,
        )
        return count

    def exists(self) -> bool:
        """Return ``True`` if a snapshot file exists at :attr:`path`."""
        return self.path.exists()

    def delete(self) -> None:
        """Delete the snapshot file if it exists (useful in tests)."""
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    @contextlib.asynccontextmanager
    async def auto_save(
        self,
        router: CostAwareRouter,
        *,
        interval_s: float = 60.0,
        save_on_exit: bool = True,
    ) -> AsyncIterator[None]:
        """Async context manager that saves *router* state on a background loop.

        Starts a background :mod:`asyncio` task that calls
        :meth:`save_router` every *interval_s* seconds.  On exit the task is
        cancelled and, if *save_on_exit* is ``True``, a final save is
        performed.

        Args:
            router:       The :class:`~replicate_mcp.routing.CostAwareRouter`
                          whose state should be periodically persisted.
            interval_s:   Seconds between automatic saves (default 60).
            save_on_exit: If ``True`` (default), performs one final save when
                          the context exits — even if the loop was cancelled.

        Example::

            router  = CostAwareRouter(strategy="thompson")
            manager = RouterStateManager()
            manager.load_into_router(router)

            async with manager.auto_save(router, interval_s=30):
                # router state is saved every 30 s in the background
                await run_server(router)
            # Final save on exit guaranteed
        """
        task: asyncio.Task[None] | None = None

        async def _loop() -> None:
            while True:
                await asyncio.sleep(interval_s)
                try:
                    self.save_router(router)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Background router state save failed: %s", exc)

        try:
            task = asyncio.get_running_loop().create_task(_loop())
            yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if save_on_exit:
                try:
                    self.save_router(router)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Final router state save failed: %s", exc)


# ---------------------------------------------------------------------------
# Serialisation helpers (used by CostAwareRouter.dump_state / load_state)
# ---------------------------------------------------------------------------


def serialise_stats(stats: ModelStats) -> dict[str, Any]:
    """Convert a :class:`ModelStats` instance to a JSON-serialisable dict."""
    return asdict(stats)


def deserialise_stats(data: dict[str, Any]) -> ModelStats:
    """Reconstruct a :class:`ModelStats` instance from a raw dict.

    Unknown keys are silently dropped so that forward-compatible snapshots
    (with new fields) can be loaded by older code without error.
    """
    known_fields = {f.name for f in ModelStats.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    filtered = {k: v for k, v in data.items() if k in known_fields}
    return ModelStats(**filtered)


__all__ = [
    "RouterStateManager",
    "serialise_stats",
    "deserialise_stats",
]
