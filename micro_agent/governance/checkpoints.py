"""The neutral checkpoint state schema shared by every governed runtime.

A :class:`~micro_agent.checkpoint.CheckpointRecord` already carries replay-safe,
JSON-serialisable state (request, transcript, tool results, iteration count,
usage). The governance layer stamps each record it writes with a schema version
so a runtime refuses state it cannot interpret instead of resuming from it.

Records written before versioning (empty ``runtime_state``) are version 1 by
definition. A record written by the Google ADK adapter carries ADK session
events under ``runtime_state["google_adk"]`` and is not neutral; only that
adapter can resume it.
"""

from __future__ import annotations

from typing import Any

from micro_agent.checkpoint import CheckpointRecord
from micro_agent.core import CheckpointNotFoundError

NEUTRAL_STATE_KEY = "micro_agent"
NEUTRAL_SCHEMA_VERSION = 1
_FOREIGN_STATE_KEYS = ("google_adk",)


def neutral_runtime_state() -> dict[str, Any]:
    """The ``runtime_state`` stamped on every neutral checkpoint."""
    return {NEUTRAL_STATE_KEY: {"schema": NEUTRAL_SCHEMA_VERSION}}


def ensure_neutral_checkpoint(checkpoint: CheckpointRecord) -> None:
    """Reject a checkpoint this layer did not write or cannot interpret."""
    state = checkpoint.runtime_state or {}
    for key in _FOREIGN_STATE_KEYS:
        if key in state:
            raise CheckpointNotFoundError(
                f"checkpoint was written by the '{key}' runtime and cannot be resumed here"
            )
    neutral = state.get(NEUTRAL_STATE_KEY)
    if neutral is None:
        return  # unversioned (legacy) record: schema 1
    schema = neutral.get("schema") if isinstance(neutral, dict) else None
    if schema != NEUTRAL_SCHEMA_VERSION:
        raise CheckpointNotFoundError(
            f"unsupported checkpoint schema {schema!r}; "
            f"this runtime reads schema {NEUTRAL_SCHEMA_VERSION}"
        )
