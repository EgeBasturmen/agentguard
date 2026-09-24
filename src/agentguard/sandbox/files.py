from copy import deepcopy
from typing import Any

from agentguard.tracing.collector import TraceCollector


class FileSandbox:

    def __init__(
        self,
        initial_state: dict[str, Any],
        tracer: TraceCollector,
    ) -> None:
        self.state = deepcopy(initial_state)
        self.tracer = tracer

    def get_state(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def read_file(self, filename: str) -> str:
        self.tracer.emit(
            event_type="tool_started",
            tool_name="file.read",
            payload={"filename": filename},
        )

        content = self.state["files"][filename]["content"]

        self.tracer.emit(
            event_type="tool_completed",
            tool_name="file.read",
            payload={"filename": filename},
        )

        return content

    def delete_file(self, filename: str) -> None:
        self.tracer.emit(
            event_type="tool_started",
            tool_name="file.delete",
            payload={"filename": filename},
        )

        del self.state["files"][filename]

        self.tracer.emit(
            event_type="tool_completed",
            tool_name="file.delete",
            payload={"filename": filename},
        )