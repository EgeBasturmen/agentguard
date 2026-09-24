from typing import Any

from agentguard.core.trace import TraceEvent


class TraceCollector:

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def emit(
        self,
        event_type: str,
        tool_name: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:

        event = TraceEvent(
            event_type=event_type,
            tool_name=tool_name,
            payload=payload or {},
        )

        self.events.append(event)