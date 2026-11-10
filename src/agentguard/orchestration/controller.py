"""Host-side orchestration. Agent references remain data until the Docker worker imports them."""
import hashlib
import json
import uuid
from agentguard.core.scenario import Scenario
from agentguard.execution.agent_spec import AgentLaunchSpec
from agentguard.execution.controlled_runtime import ControlledDockerRuntime
from agentguard.execution.controlled_tools import registry_for
from agentguard.execution.records import RuntimeManifest
from agentguard.execution.tool_broker import ToolBroker
from agentguard.runner.runner import ScenarioRunner
from agentguard.security.policy import SecurityPolicy
from agentguard.contracts.compatibility import controlled_context

class RunController:
    def run(self, scenario: Scenario, agent: AgentLaunchSpec, *, environment="commerce", registry=None,
            policy: SecurityPolicy | None = None, cancel_event=None, state_observer=None, environment_identity=None, execution_deadline=None):
        scenario = Scenario.model_validate(scenario.model_dump())
        from agentguard.integrations.agents import ContainerLaunchSpec
        agent_type=ContainerLaunchSpec if isinstance(agent,ContainerLaunchSpec) else AgentLaunchSpec
        agent = agent_type.model_validate(agent.model_dump())
        if registry is None:
            registry, names = registry_for(environment)
        else:
            # Custom simulator handlers are trusted controller code, never worker-provided objects.
            if policy is None: raise ValueError("Custom registries require an explicit policy")
            names = frozenset()
        policy = SecurityPolicy.model_validate((policy or SecurityPolicy(allowed_tools=names)).model_dump())
        if policy.execution_mode != "isolated" or policy.allow_network or policy.allow_host_filesystem or policy.allow_host_environment:
            raise ValueError("Controlled execution does not support host access or networking")
        if len(json.dumps(scenario.initial_state, allow_nan=False).encode()) > 1024*1024:
            raise ValueError("Initial simulator state exceeds 1 MiB")
        manifest = RuntimeManifest(run_id=uuid.uuid4().hex)
        runtime = ControlledDockerRuntime()
        holders = {}
        class StateView:
            def __init__(self, initial_state, tracer):
                self.broker = ToolBroker(registry=registry, state=initial_state, policy=policy,
                    forbidden_changes=scenario.forbidden_changes, tracer=tracer, cancel_event=cancel_event)
                holders["broker"] = self.broker
            def get_state(self): return state_observer() if state_observer is not None else self.broker.state
        class Relay:
            def run(self, task, sandbox):
                runtime.execute(agent, task, sandbox.broker, policy, manifest, cancel_event, execution_deadline=execution_deadline)
        policy_data = policy.model_dump(mode="json")
        policy_data["allowed_tools"] = sorted(policy_data["allowed_tools"])
        for scopes in policy_data["resource_scopes"].values():
            for key in scopes: scopes[key] = sorted(scopes[key])
        digest = hashlib.sha256(json.dumps(policy_data, sort_keys=True).encode()).hexdigest()
        result = ScenarioRunner(StateView, environment_id=f"controlled.simulator.v1:{environment_identity or environment}:{digest}").run(scenario, Relay())
        broker = holders.get("broker")
        result.execution_context = controlled_context("container.ipc" if isinstance(agent,ContainerLaunchSpec) else "python.container")
        result.cleanup_status = runtime.cleanup_status
        result.run_manifest = manifest
        result.worker_output = runtime.output
        result.audit = broker.audit if broker else []
        result.policy_status = "violation" if broker and broker.policy_violated else "passed" if manifest.started else "unknown"
        return result
