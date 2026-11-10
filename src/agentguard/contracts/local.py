"""A thin trusted-local bridge; no agent loading, new runner or isolated backend."""

from agentguard.contracts.compatibility import assess_compatibility, local_context
from agentguard.contracts.models import ScenarioVersion
from agentguard.core.manifest import digest
from agentguard.core.result import RunResult
from agentguard.runner.runner import ScenarioRunner
from agentguard.sandbox.base import SandboxFactory


def run_local_definition(definition: ScenarioVersion, agent, sandbox_factory: SandboxFactory) -> RunResult:
    definition = ScenarioVersion.model_validate(definition.model_dump())
    compatibility = assess_compatibility(definition)
    if not compatibility.supported:
        return RunResult(
            scenario_name=definition.scenario.name, passed=False, status="error",
            execution_context=local_context(), error_stage="validation",
            error_type="UnsupportedContractError",
            error_message="; ".join(compatibility.issues + compatibility.missing_capabilities),
        )
    # Candidate agent artifacts may differ; test/evaluator/tool/permission semantics may not.
    identity = definition.model_dump()
    identity["agent"] = definition.agent.model_dump(include={"adapter", "transport", "execution_mode"})
    environment_id = f"{definition.environment.id}@{definition.environment.version}/{digest(identity)}"
    result = ScenarioRunner(sandbox_factory, environment_id=environment_id).run(definition.scenario, agent)
    result.execution_context.limitations = compatibility.limitations
    return result
