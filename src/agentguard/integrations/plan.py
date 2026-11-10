"""Declarative CLI plans: no dynamic host imports, source builds, or hidden live fallbacks."""
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from agentguard.core.scenario import Scenario
from agentguard.core.manifest import canonical_json
from agentguard.execution.agent_spec import AgentLaunchSpec
from agentguard.integrations.agents import PythonAgent,HTTPAgent,ContainerAgent,ContainerLaunchSpec
from agentguard.integrations.http import Endpoint,_json
from agentguard.integrations.tools import ToolContract,RoutedTool,ReplayEntry,OpenAPIConnector,MCPConnector
from agentguard.integrations.sdk import IntegrationRunner,environment_tools,preflight
from agentguard.environments import MemoryEnvironment,FilesystemEnvironment,DocumentEnvironment,HTTPEnvironment
from agentguard.security.policy import SecurityPolicy

class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
class EndpointConfig(Model):
    base_url: str
    allow_http: bool=False
    private_hosts: list[str]=Field(default_factory=list)
    timeout_seconds: float=10.0
    max_bytes: int=1024*1024
    def endpoint(self): return Endpoint(self.base_url,self.allow_http,frozenset(self.private_hosts),self.timeout_seconds,self.max_bytes)
class AgentConfig(Model):
    transport: Literal['python','http','container']
    reference: str|None=None
    source_dir: str='.'
    include_files: list[str]=Field(default_factory=list)
    image: str|None=None
    endpoint: EndpointConfig|None=None
class EnvironmentConfig(Model):
    kind: Literal['memory','filesystem','dataset','http']
    endpoint: EndpointConfig|None=None
    allow_staging_writes: bool=False
    service_version: str='1.0.0'
class ToolConfig(Model):
    connector: Literal['environment','openapi','mcp','mock','replay']
    endpoint: EndpointConfig|None=None
    document: dict|None=None
    server_version: str='1.0.0'
    approved_effects: dict[str,Literal['read','write','mixed']]=Field(default_factory=dict)
    contract: dict|None=None
    mock_result: object=None
    replay_entries: list[dict]=Field(default_factory=list)
    context: dict=Field(default_factory=dict)
class IntegrationPlan(Model):
    schema_version: Literal[1]
    scenario: Scenario
    agent: AgentConfig
    environment: EnvironmentConfig
    tools: list[ToolConfig]=Field(default_factory=list)
    policy: dict=Field(default_factory=dict)
    required_capabilities: list[str]=Field(default_factory=list)


def run_plan(path,*,cancel_event=None,preflight_only=False):
    path=Path(path).resolve()
    with path.open('rb') as stream: data=stream.read(1024*1024+1)
    if len(data)>1024*1024: raise ValueError('Integration plan exceeds 1 MiB')
    parsed=_json(data)
    if type(parsed.get('schema_version')) is not int: raise ValueError('Integer plan schema_version required')
    plan=IntegrationPlan.model_validate(parsed)
    config=plan.agent
    if config.transport=='python':
        if not config.reference or config.endpoint is not None or config.image is not None: raise ValueError('Python agent requires a source reference only')
        source=(path.parent/config.source_dir).resolve()
        agent=PythonAgent(AgentLaunchSpec(reference=config.reference,source_dir=source,include_files=config.include_files))
    elif config.transport=='container':
        if not config.image or config.endpoint is not None: raise ValueError('Container agent requires a pinned image')
        agent=ContainerAgent(ContainerLaunchSpec(image=config.image,reference=config.reference or 'agent'))
    else:
        if config.endpoint is None or config.image is not None or config.reference is not None: raise ValueError('HTTP agent requires endpoint only')
        agent=HTTPAgent(config.endpoint.endpoint())
    env=plan.environment
    if env.kind=='http':
        if env.endpoint is None: raise ValueError('HTTP environment endpoint required')
        environment=HTTPEnvironment(env.endpoint.endpoint(),env.allow_staging_writes,env.service_version)
    else:
        if env.endpoint is not None or env.allow_staging_writes: raise ValueError('Non-HTTP environment cannot declare a network grant')
        environment={'memory':MemoryEnvironment,'filesystem':FilesystemEnvironment,'dataset':DocumentEnvironment}[env.kind]()
    tools=[]
    for config in plan.tools:
        if config.connector=='environment': tools.extend(environment_tools(environment));continue
        if config.connector in {'mock','replay'}:
            if config.contract is None or config.endpoint is not None: raise ValueError('Offline routes require contract and no endpoint')
            entries=tuple(ReplayEntry(**entry) for entry in config.replay_entries)
            tools.append(RoutedTool(ToolContract.model_validate(config.contract),mode=config.connector,mock_result=config.mock_result,
                replay_entries=entries,context=config.context));continue
        if config.endpoint is None: raise ValueError('Discovery endpoint required')
        if config.connector=='openapi':
            connector=OpenAPIConnector(config.endpoint.endpoint())
            contracts=connector.discover(config.document)
        else:
            connector=MCPConnector(config.endpoint.endpoint(),config.server_version)
            contracts=connector.discover()
        for contract in contracts:
            approved=config.approved_effects.get(contract.name)
            contract=contract.model_copy(update={'effect_type':approved or 'unknown'})
            tools.append(RoutedTool(contract,mode='staging',target=connector,approved_effect=approved is not None))
    if preflight_only:
        return preflight(plan.scenario,agent,environment,tools,SecurityPolicy.model_validate(plan.policy),plan.required_capabilities)
    return IntegrationRunner().run(plan.scenario,agent,environment,tools,policy=SecurityPolicy.model_validate(plan.policy),
        required_capabilities=plan.required_capabilities,cancel_event=cancel_event)
