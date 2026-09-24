from typing import Any

from agentguard.core.result import RunResult
from agentguard.core.scenario import Scenario
from agentguard.security.policy import SecurityPolicy


class IsolationNotImplementedError(RuntimeError):
    pass


class IsolatedExecutionBackend:

    def run(
        self,
        scenario: Scenario,
        agent: Any,
        policy: SecurityPolicy,
    ) -> RunResult:
        raise IsolationNotImplementedError(
            "Isolated agent execution is not implemented. "
            "Agent execution has been refused."
        )