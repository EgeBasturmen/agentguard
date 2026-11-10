"""Capability-aware integration orchestration using the existing evaluator and broker."""
from copy import deepcopy
from dataclasses import dataclass,field,replace
import hashlib
import time
import uuid
from agentguard.core.manifest import canonical_json
from agentguard.core.result import RunResult,AssertionResult
from agentguard.core.scenario import Scenario
from agentguard.contracts.models import ExecutionContext,AdapterIdentity
from agentguard.environments import MemoryEnvironment,FilesystemEnvironment,DocumentEnvironment,HTTPEnvironment
from agentguard.integrations.agents import PythonAgent,ContainerAgent,HTTPAgent
from agentguard.integrations.tools import RoutedTool
from agentguard.execution.tool_registry import ToolRegistry
from agentguard.orchestration.controller import RunController
from agentguard.runner.runner import ScenarioRunner
from agentguard.security.policy import SecurityPolicy

@dataclass
class PreflightResult:
    issues: list=field(default_factory=list)
    coverage_gaps: list=field(default_factory=list)
    @property
    def supported(self): return not self.issues


def preflight(scenario,agent,environment,tools,policy,required_capabilities=()):
    result=PreflightResult()
    if not isinstance(agent,(PythonAgent,ContainerAgent,HTTPAgent)):
        result.issues.append('Unsupported agent adapter/protocol');return result
    available=agent.capabilities()
    for name in required_capabilities:
        if not hasattr(available,name) or not getattr(available,name): result.issues.append('Missing capability: '+name)
    names=[tool.contract.name for tool in tools]
    if len(names)!=len(set(names)): result.issues.append('Duplicate tool identities')
    if not set(policy.allowed_tools).issubset(names): result.issues.append('Policy references undiscovered tools')
    if any(check.tool_name not in names for check in scenario.tool_expectations): result.issues.append('Expected tool was not discovered')
    if policy.allow_network or policy.allow_host_environment or policy.allow_host_filesystem or policy.execution_mode!='isolated':
        result.issues.append('Agent host/network grants are not supported; broker endpoints need separate grants')
    if isinstance(agent,HTTPAgent):
        result.coverage_gaps.append('Observed HTTP agent: internal/direct tools, isolation and model usage are not observable or intercepted.')
        if not isinstance(environment,HTTPEnvironment): result.issues.append('HTTP agents require an independent HTTP staging observer')
        if scenario.tool_expectations or tools: result.issues.append('HTTP endpoint has no authoritative tool telemetry; tool assertions/routes are unsupported')
        if policy.allowed_tools or policy.resource_scopes: result.issues.append('Remote HTTP agent tool policy cannot be enforced')
    if not isinstance(environment,(MemoryEnvironment,FilesystemEnvironment,DocumentEnvironment,HTTPEnvironment)):
        result.issues.append('Unsupported environment lifecycle')
    if isinstance(environment,HTTPEnvironment) and not environment.allow_staging_writes:
        result.issues.append('Staging fixture writes require an explicit grant')
    for tool in tools:
        try: tool.__post_init__()
        except Exception as exc: result.issues.append('Invalid tool contract: '+str(exc));continue
        if tool.mode=='mock':
            result.coverage_gaps.append('Schema mock '+tool.contract.name+': interface evidence only; business semantics are not verified.')
            if scenario.expected_state or scenario.forbidden_changes or scenario.invariants:
                result.issues.append('Schema mock cannot verify business state/effect checks')
        if tool.mode=='replay':
            result.coverage_gaps.append('Replay '+tool.contract.name+': approved recorded cases only; no live-system claim or fallback.')
            if environment.external: result.issues.append('Replay requires an isolated memory environment')
        if tool.mode=='staging':
            if not isinstance(environment,HTTPEnvironment) or not tool.approved_effect or tool.contract.effect_type not in {'read','write','mixed'}:
                result.issues.append('Staging requires approved effects and a verified namespaced HTTP observer')
            if tool.target is None or not hasattr(tool.target,'bound'): result.issues.append('Staging connector lacks namespace binding')
            elif isinstance(environment,HTTPEnvironment):
                suffix='/mcp' if tool.contract.adapter.startswith('mcp@') else ''
                expected=environment.endpoint.base_url.rstrip('/')+suffix
                if tool.target.endpoint.base_url.rstrip('/')!=expected:
                    result.issues.append('Connector origin/path does not match the explicitly namespaced environment')
        if tool.mode=='simulator' and environment.external and not isinstance(environment,(FilesystemEnvironment,DocumentEnvironment)):
            result.issues.append('External HTTP systems must use explicit staging routes')
        if tool.contract.output_schema is None: result.coverage_gaps.append('No declared output schema for '+tool.contract.name)
    return result


class IntegrationRunner:
    def run(self,scenario,agent,environment,tools=(),*,policy=None,required_capabilities=(),cancel_event=None):
        scenario=Scenario.model_validate(scenario.model_dump())
        policy=SecurityPolicy.model_validate((policy or SecurityPolicy()).model_dump())
        tools=deepcopy(list(tools))
        readiness=preflight(scenario,agent,environment,tools,policy,required_capabilities)
        if not readiness.supported:
            return RunResult(scenario_name=scenario.name,passed=False,status='error',error_stage='validation',
                error_type='IntegrationPreflightError',error_message='; '.join(readiness.issues),integration_status='unsupported',
                coverage_gaps=readiness.coverage_gaps+readiness.issues)
        modes=sorted({tool.mode for tool in tools}) or ['staging' if isinstance(agent,HTTPAgent) else 'simulator']
        manifest=dict(schema_version=1,agent_transport=agent.transport,environment=environment.identity(),
            tools=[tool.identity() for tool in sorted(tools,key=lambda row:row.contract.name)],modes=modes,
            required_capabilities=sorted(required_capabilities))
        fingerprint=hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
        manifest['fingerprint']=fingerprint
        result=None;provisioned=False
        deadline=time.monotonic()+policy.timeout_seconds
        try:
            if cancel_event is not None and cancel_event.is_set():
                from agentguard.execution.errors import ExecutionCancelled
                raise ExecutionCancelled('Integration cancelled before provisioning')
            if isinstance(environment,HTTPEnvironment): environment.deadline=deadline
            environment.provision(scenario.initial_state);provisioned=True
            if canonical_json(environment.snapshot())!=canonical_json(scenario.initial_state): raise ValueError('Environment fixture mismatch')
            bound=[]
            for tool in tools:
                if tool.mode=='staging':
                    suffix='/mcp' if tool.contract.adapter.startswith('mcp@') else ''
                    tool.target=tool.target.bound(environment.bound_endpoint(suffix))
                    tool.observer=environment
                elif tool.mode=='simulator' and environment.external:
                    tool.target=environment
                bound.append(tool)
            remaining=deadline-time.monotonic()
            if remaining<=0:
                from agentguard.execution.errors import ExecutionTimeout
                raise ExecutionTimeout('Integration provisioning exceeded run deadline')

            if isinstance(agent,(PythonAgent,ContainerAgent)):
                registry=ToolRegistry()
                for tool in bound: tool.register(registry)
                result=RunController().run(scenario,agent.launch,environment='integration',registry=registry,policy=policy,
                    cancel_event=cancel_event,state_observer=environment.snapshot if environment.external else None,environment_identity=fingerprint,execution_deadline=deadline)
            else:
                agent=replace(agent,endpoint=replace(agent.endpoint,timeout_seconds=min(agent.endpoint.timeout_seconds,remaining)))
                output={}
                run_id=uuid.uuid4().hex
                class View:
                    def __init__(self,initial_state,tracer): pass
                    def get_state(self): return environment.snapshot()
                class Invoke:
                    def run(self,task,sandbox): output['value']=agent.invoke(task,run_id,environment.namespace,cancel_event)
                result=ScenarioRunner(View,environment_id='integration:'+fingerprint).run(scenario,Invoke())
                result.worker_output=output.get('value')
                result.execution_context=ExecutionContext(execution_mode='observed',adapter=AdapterIdentity(id='http.endpoint',version='1.0.0'),
                    capabilities=agent.capabilities(),limitations=readiness.coverage_gaps)
            result.integration_status='verified' if result.error_type is None and result.evaluation_status=='complete' else 'unverified'
        except Exception as exc:
            result=RunResult(scenario_name=scenario.name,passed=False,status='error',error_stage='provision',
                error_type=type(exc).__name__,error_message=str(exc),integration_status='unverified')
        finally:
            try: cleanup=environment.cleanup()
            except Exception: cleanup='unverified'
            if result is not None:
                if result.cleanup_status=='unverified' or result.error_type=='ExecutionCleanupError' or cleanup!='verified':
                    result.cleanup_status='unverified';result.passed=False;result.status='error';result.integration_status='unverified'
                    result.error_type=result.error_type or 'EnvironmentCleanupError'
                    result.error_message=result.error_message or 'Environment cleanup was not verified'
                    result.error_stage=result.error_stage or 'execution'
                elif provisioned or getattr(environment,'namespace',''): result.cleanup_status='verified'
        result.integration_manifest=manifest
        result.coverage_gaps=list(dict.fromkeys(result.coverage_gaps+readiness.coverage_gaps))
        return result


def environment_tools(environment):
    return [RoutedTool(contract,mode='simulator',target=environment) for contract in environment.contracts()]


def contract_test(environment,fixture):
    """Reusable real lifecycle probe for adapter authors; never runs a user agent."""
    try:
        environment.provision(deepcopy(fixture))
        if canonical_json(environment.snapshot())!=canonical_json(fixture): raise AssertionError('Fixture snapshot mismatch')
        environment.reset(deepcopy(fixture))
        if canonical_json(environment.snapshot())!=canonical_json(fixture): raise AssertionError('Reset snapshot mismatch')
    finally:
        if environment.cleanup()!='verified': raise AssertionError('Cleanup not verified')
    return True
