from typing import Any, Protocol


class AgentProtocol(Protocol):

    def run(
        self,
        task: str,
        sandbox: Any,
    ) -> str:
        ...