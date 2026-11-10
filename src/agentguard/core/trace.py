from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


TraceEventType = Literal[
    "run_started",
    "tool_started",
    "tool_completed",
    "state_changed",
    "run_completed",
    "error",
]


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_type: TraceEventType

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    tool_name: str | None = None

    payload: dict[str, Any] = Field(
        default_factory=dict
    )
