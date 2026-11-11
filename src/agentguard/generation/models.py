"""Sealed proposal content. Hashes detect inconsistency; they are not signatures/authentication."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator,field_validator,model_serializer
from agentguard.model_gateway.gateway import GatewayConfig
from agentguard.core.manifest import digest,canonical_json,check_id
from agentguard.core.paths import parse_path
from agentguard.integrations.plan import AgentConfig,EnvironmentConfig,ToolConfig
from agentguard.integrations.tools import ToolContract
from agentguard.security.policy import SecurityPolicy

class Model(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    @field_validator('schema_version',mode='before',check_fields=False)
    @classmethod
    def version_is_integer(cls,value):
        if type(value) is not int or value!=1:raise ValueError('Only integer schema_version=1 is supported')
        return value

class Source(Model):
    id: str=Field(min_length=1,max_length=200)
    version: str=Field(min_length=1,max_length=100)
    kind: Literal['task','rule','contract','trace','risk','manual','import']
    uri: str=Field(default='inline',max_length=2048)
    content: dict
    fingerprint: str|None=None
    @model_validator(mode='after')
    def seal(self):
        value=digest(self.model_dump(exclude={'fingerprint'}))
        if self.fingerprint is not None and self.fingerprint!=value: raise ValueError('Source fingerprint mismatch')
        self.fingerprint=value
        return self

class RuleInput(Model):
    id: str=Field(min_length=1,max_length=150)
    version: str=Field(default='1',min_length=1)
    text: str=Field(min_length=1,max_length=8192)
    uri: str='inline'

class RuntimeTemplate(Model):
    agent: AgentConfig
    environment: EnvironmentConfig
    tools: list[ToolConfig]=Field(default_factory=list)
    policy: dict=Field(default_factory=dict)
    required_capabilities: list[str]=Field(default_factory=list)
    model: GatewayConfig|None=None
    @model_serializer(mode='wrap')
    def compatible(self,handler):
        data=handler(self)
        if self.model is None:data.pop('model',None)
        return data
    @model_validator(mode='after')
    def validate_policy(self):
        policy=SecurityPolicy.model_validate(self.policy).model_dump(mode='json')
        policy['allowed_tools']=sorted(policy['allowed_tools'])
        for scopes in policy['resource_scopes'].values():
            for key in scopes: scopes[key]=sorted(scopes[key])
        self.policy=policy
        return self

class GenerationRequest(Model):
    schema_version: Literal[1]=1
    task: str=Field(min_length=1,max_length=8192)
    task_id: str=Field(default='task:main',min_length=1,max_length=150)
    task_version: str=Field(default='1',min_length=1)
    task_uri: str='inline'
    fixture: dict
    runtime: RuntimeTemplate
    rules: list[RuleInput]=Field(default_factory=list,max_length=100)
    contracts: list[ToolContract]=Field(default_factory=list,max_length=100)
    traces: list[dict]=Field(default_factory=list,max_length=100)
    risk_packs: list[Literal['instruction_override','schema_boundary']]=Field(default_factory=lambda:['instruction_override','schema_boundary'])
    seed: int=Field(default=0,ge=0,le=2**31-1)
    max_cases: int=Field(default=100,ge=1,le=200)
    @model_validator(mode='after')
    def validate_content(self):
        if len(canonical_json(self.model_dump(mode='json')).encode())>1024*1024: raise ValueError('Generation input exceeds 1 MiB')
        if len({rule.id for rule in self.rules})!=len(self.rules): raise ValueError('Duplicate rule source IDs')
        for tool in self.contracts:tool.fingerprint()
        if len({tool.name for tool in self.contracts})!=len(self.contracts): raise ValueError('Duplicate contract names')
        if not self.task.strip(): raise ValueError('Blank task')
        return self


def definitions(scenario):
    checks={}
    for group,kind in [('expected_state','expected'),('forbidden_changes','unchanged'),('tool_expectations','tool'),('invariants','invariant')]:
        for check in scenario.get(group,[]):
            selector=list(parse_path(check['path'])) if kind in {'expected','unchanged'} else check['tool_name'] if kind=='tool' else check['name']
            checks[check_id(kind,selector)]={'kind':kind,**check}
    for check in scenario.get('evaluations',[]):checks[check_id('evaluation',check['id'])]={'kind':'evaluation',**check}
    return checks

class Candidate(Model):
    schema_version: Literal[1]=1
    id: str=Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    revision: int=Field(default=1,ge=1)
    axis: Literal['benign','adversarial','edge']
    scenario: dict
    runtime: RuntimeTemplate
    sources: list[Source]=Field(min_length=1,max_length=400)
    provenance: list[dict]=Field(min_length=1)
    oracle_sources: dict[str,list[str]]=Field(default_factory=dict)
    requirements: list[str]=Field(default_factory=list)
    unresolved: list[str]=Field(default_factory=list)
    observations: dict=Field(default_factory=dict)
    fingerprint: str|None=None
    @property
    def oracle_checks(self): return sorted(self.oracle_sources)
    @model_validator(mode='after')
    def seal(self):
        if len({source.id for source in self.sources})!=len(self.sources): raise ValueError('Duplicate candidate source IDs')
        body=self.model_dump(mode='json',exclude={'fingerprint'})
        if len(canonical_json(body).encode())>2*1024*1024: raise ValueError('Candidate exceeds 2 MiB')
        value=digest(body)
        if self.fingerprint is not None and value!=self.fingerprint: raise ValueError('Candidate fingerprint mismatch')
        self.fingerprint=value
        return self
    def checked(self): return type(self).model_validate(self.model_dump(mode='json'))
    def plan(self):
        from agentguard.core.scenario import Scenario
        scenario=Scenario.model_validate(self.scenario)
        reference=dict(case_id=self.id,revision=self.revision,candidate_fingerprint=self.fingerprint,scenario_fingerprint=digest(scenario.model_dump(mode='json',exclude={'generation'})))
        raw=scenario.model_dump(mode='json');raw['generation']=reference
        return dict(schema_version=1,scenario=raw,**self.runtime.model_dump(mode='json'))

class GenerationBatch(Model):
    schema_version: Literal[1]=1
    generator: Literal['deterministic-rules@1.0.0']='deterministic-rules@1.0.0'
    cases: list[Candidate]=Field(max_length=200)
    requirements: list[dict]
    gaps: list[str]=Field(default_factory=list)
