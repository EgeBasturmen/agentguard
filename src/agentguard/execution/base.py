from typing import Any, Protocol

from agentguard.core.result import RunResult
from agentguard.core.scenario import Scenario
from agentguard.security.policy import SecurityPolicy


class ExecutionBackend(Protocol):

    def run(
        self,
        scenario: Scenario,
        agent: Any,
        policy: SecurityPolicy,
    ) -> RunResult:
        ...