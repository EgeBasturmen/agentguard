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
        *, environment="commerce", registry=None, cancel_event=None,
    ) -> RunResult:
        from agentguard.execution.agent_spec import AgentLaunchSpec
        from agentguard.orchestration.controller import RunController
        if not isinstance(agent, AgentLaunchSpec):
            # Preserve refusal for the old object API: never serialize/import live host agents.
            raise IsolationNotImplementedError(
                "Live host agent objects are unsupported; use AgentLaunchSpec for controlled execution."
            )
        return RunController().run(scenario, agent, environment=environment, registry=registry,
            policy=policy, cancel_event=cancel_event)
