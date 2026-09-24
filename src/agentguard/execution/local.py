from typing import Any

from agentguard.core.result import RunResult
from agentguard.core.scenario import Scenario
from agentguard.runner.runner import ScenarioRunner
from agentguard.sandbox.base import SandboxFactory
from agentguard.sandbox.commerce import CommerceSandbox
from agentguard.security.policy import SecurityPolicy


class UnsafeExecutionModeError(RuntimeError):
    pass


class LocalExecutionBackend:

    def __init__(
        self,
        sandbox_factory: SandboxFactory = CommerceSandbox,
    ) -> None:
        self.runner = ScenarioRunner(
            sandbox_factory=sandbox_factory,
        )

    def run(
        self,
        scenario: Scenario,
        agent: Any,
        policy: SecurityPolicy,
    ) -> RunResult:

        if policy.execution_mode != "local":
            raise UnsafeExecutionModeError(
                "LocalExecutionBackend cannot satisfy "
                "an isolated execution policy."
            )

        if (
            policy.allow_network
            or policy.allow_host_filesystem
            or policy.allow_host_environment
        ):
            raise UnsafeExecutionModeError(
                "LocalExecutionBackend does not enforce "
                "host resource access restrictions. "
                "Use it only with trusted local code."
            )

        return self.runner.run(
            scenario=scenario,
            agent=agent,
        )