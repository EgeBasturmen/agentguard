from collections.abc import Iterable
from copy import deepcopy
from typing import Any, Callable
from agentguard.tracing.collector import TraceCollector


ToolHandler = Callable[..., Any]

class ToolPermissionError(PermissionError):
    pass

class GenericSandbox:

    def __init__(
        self,
        initial_state: dict[str, Any],
        tracer: TraceCollector,
        allowed_tools: Iterable[str] | None = None,
    ) -> None:
        self.state = deepcopy(initial_state)
        self.tracer = tracer
        self.tools: dict[str, ToolHandler] = {}

        self.allowed_tools = (
            frozenset(allowed_tools)
            if allowed_tools is not None
            else None
        )

    def get_state(self) -> dict[str, Any]:
        return deepcopy(self.state)

    def register_tool(
        self,
        name: str,
        handler: ToolHandler,
    ) -> None:
        if not name:
            raise ValueError("Tool name cannot be empty.")

        if name in self.tools:
            raise ValueError(
                f"Tool '{name}' is already registered."
            )

        if not callable(handler):
            raise TypeError(
                f"Tool '{name}' must be callable."
            )

        self.tools[name] = handler

    def call_tool(
        self,
        name: str,
        **kwargs: Any,
    ) -> Any:
        if name not in self.tools:
            raise ValueError(
                f"Tool '{name}' is not registered."
            )

        if (
            self.allowed_tools is not None
            and name not in self.allowed_tools
        ):
            self.tracer.emit(
                event_type="error",
                tool_name=name,
                payload={
                    "error_type": "ToolPermissionError",
                    "error_message": (
                        f"Tool '{name}' is not permitted."
                    ),
                },
            )

            raise ToolPermissionError(
                f"Tool '{name}' is not permitted."
            )

        self.tracer.emit(
            event_type="tool_started",
            tool_name=name,
            payload={"arguments": deepcopy(kwargs)},
        )

        try:
            result = self.tools[name](
                self.state,
                **kwargs,
            )

        except Exception as exc:
            self.tracer.emit(
                event_type="error",
                tool_name=name,
                payload={
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                },
            )
            raise

        self.tracer.emit(
            event_type="tool_completed",
            tool_name=name,
            payload={"result": deepcopy(result)},
        )

        return deepcopy(result)