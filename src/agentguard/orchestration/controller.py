"""Host-side orchestration. Agent references remain data until the Docker worker imports them."""
import hashlib
import json
import uuid
import time
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
            policy: SecurityPolicy | None = None, cancel_event=None, state_observer=None, environment_identity=None, execution_deadline=None,model_gateway=None):
        scenario = Scenario.model_validate(scenario.model_dump())
        evaluation_deadline=execution_deadline or time.monotonic()+(policy.timeout_seconds if policy is not None else 30)
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
        if scenario.turns and isinstance(agent,ContainerLaunchSpec):raise ValueError('Session lifecycle requires the built-in Python worker')
        if model_gateway is not None:
            from agentguard.model_gateway.gateway import ModelArguments
            if registry.get('model.generate') is not None:raise ValueError('Reserved model gateway route cannot be overridden')
            registry.register('model.generate',ModelArguments,_reserved_model_handler,stateful=True)
        manifest = RuntimeManifest(run_id=uuid.uuid4().hex)
        runtime = ControlledDockerRuntime()
        holders = {}
        class StateView:
            def __init__(self, initial_state, tracer):
                self.broker = ToolBroker(registry=registry, state=initial_state, policy=policy,
                    forbidden_changes=scenario.forbidden_changes, tracer=tracer, cancel_event=cancel_event,model_gateway=model_gateway,faults=scenario.faults)
                holders["broker"] = self.broker
            def get_state(self): return state_observer() if state_observer is not None else self.broker.state
        class Relay:
            def run(self, task, sandbox):
                runtime.execute(agent, task, sandbox.broker, policy, manifest, cancel_event, execution_deadline=execution_deadline,turns=[turn.task for turn in scenario.turns] or None)
                return runtime.output
        policy_data = policy.model_dump(mode="json")
        policy_data["allowed_tools"] = sorted(policy_data["allowed_tools"])
        for scopes in policy_data["resource_scopes"].values():
            for key in scopes: scopes[key] = sorted(scopes[key])
        digest = hashlib.sha256(json.dumps(policy_data, sort_keys=True).encode()).hexdigest()
        evidence=lambda sandbox:dict(audit=sandbox.broker.audit,authoritative=True,output=runtime.output,turn_outputs=runtime.turn_outputs,gateway=model_gateway,cancel_event=cancel_event,deadline=evaluation_deadline)
        result = ScenarioRunner(StateView, environment_id=f"controlled.simulator.v1:{environment_identity or environment}:{digest}",evidence_provider=evidence).run(scenario, Relay())
        broker = holders.get("broker")
        result.execution_context = controlled_context("container.ipc" if isinstance(agent,ContainerLaunchSpec) else "python.container")
        if scenario.turns:result.execution_context.capabilities.supports_sessions=True;result.execution_context.capabilities.supports_memory_reset=True
        if model_gateway is not None:result.execution_context.capabilities.supports_model_usage=True
        result.cleanup_status = runtime.cleanup_status
        result.run_manifest = manifest
        result.worker_output = runtime.output
        result.audit = broker.audit if broker else []
        result.policy_status = "violation" if broker and broker.policy_violated else "passed" if manifest.started else "unknown"
        if model_gateway is not None:result.model_evidence=model_gateway.evidence().model_dump(mode='json')
        return result

def _reserved_model_handler(state,**arguments):
    raise RuntimeError('Model gateway must execute in the trusted broker, never a tool subprocess')
