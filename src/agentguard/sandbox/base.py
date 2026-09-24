from typing import Any, Protocol

from agentguard.tracing.collector import TraceCollector


class SandboxProtocol(Protocol):

    def get_state(self) -> dict[str, Any]:
        ...


class SandboxFactory(Protocol):

    def __call__(
        self,
        initial_state: dict[str, Any],
        tracer: TraceCollector,
    ) -> SandboxProtocol:
        ...