from typing import Any

from agentguard.sandbox.generic import GenericSandbox
from agentguard.tracing.collector import TraceCollector

from examples.generic_tools import (
    delete_document,
    read_document,
)


class DocumentSandbox(GenericSandbox):

    def __init__(
        self,
        initial_state: dict[str, Any],
        tracer: TraceCollector,
    ) -> None:
        super().__init__(
            initial_state=initial_state,
            tracer=tracer,
            allowed_tools={"document.read"},
        )

        self.register_tool(
            "document.read",
            read_document,
        )

        self.register_tool(
            "document.delete",
            delete_document,
        )