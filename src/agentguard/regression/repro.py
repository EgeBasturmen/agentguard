"""Data-only, opt-in offline repro. Exported content never grants suite approval."""
import json
import re
import tempfile
from pathlib import Path
from typing import Literal
from pydantic import Field,model_validator
from agentguard.generation.models import Model
from agentguard.core.manifest import digest,canonical_json
from agentguard.core.outcome import effective_pass
from agentguard.integrations.plan import IntegrationPlan,run_integration_plan
from agentguard.security.policy import SecurityPolicy
from agentguard.execution.agent_spec import AgentLaunchSpec
from agentguard.environments.adapters import safe_relative

FIELDS={'scenario','agent','environment','tools','policy','required_capabilities'}
SECRET=re.compile(r'(?:AIza[0-9A-Za-z_-]{35}|AQ\.[0-9A-Za-z_-]{45,}|gh[pousr]_[A-Za-z0-9]{30,}|sk-(?:proj-)?[A-Za-z0-9_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)')
SECRET_KEYS={'apikey','api_key','authorization','password','secret','token','access_token','refresh_token','credential','credentials'}

class ExportPolicy(Model):
    mode: Literal['metadata','replay']='metadata'
    confirm_case_data: bool=False
    allow_fields: list[str]=Field(default_factory=list,max_length=6)
    redact_paths: list[str]=Field(default_factory=list,max_length=100)
    @model_validator(mode='after')
    def valid(self):
        if len(self.allow_fields)!=len(set(self.allow_fields)) or set(self.allow_fields)-FIELDS:raise ValueError('Unknown export field')
        if self.mode=='replay' and (not self.confirm_case_data or set(self.allow_fields)!=FIELDS):raise ValueError('Explicit per-field case-data approval required')
        return self


def sanitize(value,paths):
    changed=[False]
    def walk(item,path=''):
        if path in paths:changed[0]=True;return '[REDACTED]'
        if isinstance(item,dict):
            result={}
            for key,child in item.items():
                pointer=path+'/'+key.replace('~','~0').replace('/','~1')
                normalized=re.sub('[^a-z0-9]','',key.lower())
                if any(word in normalized for word in ('apikey','password','secret','token','authorization','credential')):result[key]='[REDACTED]';changed[0]=True
                else:result[key]=walk(child,pointer)
            return result
        if isinstance(item,list):return [walk(child,path+'/'+str(index)) for index,child in enumerate(item)]
        if isinstance(item,str):
            output=SECRET.sub('[REDACTED]',item)
            if output!=item:changed[0]=True
            return output
        return item
    # Redaction typos must not create a false guarantee.
    for pointer in paths:
        if not pointer.startswith('/') or len(pointer)>1024:raise ValueError('JSON pointer redaction path required')
        node=value
        for part in pointer[1:].split('/'):
            if re.search(r'~(?![01])',part):raise ValueError('Invalid JSON pointer escape')
            part=part.replace('~1','/').replace('~0','~')
            if isinstance(node,list) and not re.fullmatch(r'0|[1-9][0-9]*',part):raise ValueError('Canonical array index required')
            try:node=node[int(part)] if isinstance(node,list) else node[part]
            except (ValueError,KeyError,IndexError,TypeError):raise ValueError('Unknown redaction path') from None
    return walk(value),changed[0]


def offline_plan(plan):
    plan=IntegrationPlan.model_validate(plan.model_dump(mode='json'))
    policy=SecurityPolicy.model_validate(plan.policy)
    if plan.agent.transport!='python' or plan.agent.source_dir!='.' or plan.agent.endpoint is not None or plan.agent.image is not None:
        raise ValueError('Repro profile requires explicit local Python source; no remote agent execution')
    if plan.environment.kind=='http' or plan.environment.endpoint is not None or plan.model is not None:
        raise ValueError('Repro cannot reach external environments or models')
    if policy.allow_network or policy.allow_host_filesystem or policy.allow_host_environment:raise ValueError('Offline isolation required')
    if any(tool.connector not in {'environment','replay'} or tool.endpoint is not None for tool in plan.tools):raise ValueError('Only local environment and pinned replay tools are allowed')
    for name in plan.agent.include_files:safe_relative(name)
    # Validate module/reference without importing it or reading source on the host.
    AgentLaunchSpec(reference=plan.agent.reference or '',source_dir=Path('.'),include_files=plan.agent.include_files)
    if plan.scenario.generation is not None:raise ValueError('Repro must not carry release approval')
    return plan


def signature(result):
    return digest(dict(assertions=[dict(id=a.assertion_id,passed=a.passed) for a in result.assertions],
        output_digest=digest(result.worker_output),mutations=[m.model_dump(mode='json') for m in result.mutations],
        policy=result.policy_status,cleanup=result.cleanup_status,status=result.status,error_type=result.error_type,
        audit=[dict(tool=e.tool_name,status=e.status,effect=e.effect_status) for e in result.audit]))


class ReproBundle(Model):
    schema_version: int=1
    kind: Literal['agentguard.repro']='agentguard.repro'
    source_digest: str=Field(pattern=r'^[0-9a-f]{64}$')
    signature: str=Field(pattern=r'^[0-9a-f]{64}$')
    evidence_digest: str=Field(pattern=r'^[0-9a-f]{64}$')
    plan: dict|None=None
    redacted: bool=False
    fingerprint: str|None=None
    @model_validator(mode='after')
    def seal(self):
        if self.plan is not None:
            offline_plan(IntegrationPlan.model_validate(self.plan))
            if self.redacted:raise ValueError('Redacted data cannot be automatically replayed')
            _,detected=sanitize(self.plan,[])
            if detected:raise ValueError('Repro includes detected secret content')
        value=digest(self.model_dump(mode='json',exclude={'fingerprint'}))
        if len(canonical_json(self.model_dump(mode='json')).encode())>8*1024*1024:raise ValueError('Repro exceeds 8 MiB')
        if self.fingerprint is not None and value!=self.fingerprint:raise ValueError('Repro content changed')
        self.fingerprint=value;return self


def make_bundle(plan,result,policy):
    result=result.validated_copy();policy=ExportPolicy.model_validate(policy.model_dump(mode='json'))
    if result.run_manifest is None or not re.fullmatch(r'[0-9a-f]{64}',result.run_manifest.source_digest):raise ValueError('Observed source identity is unavailable')
    if result.comparison_manifest is None or result.comparison_manifest.scenario.model_dump(mode='json',exclude={'generation'})!=plan.scenario.model_dump(mode='json',exclude={'generation'}):raise ValueError('Plan and observation oracle differ')
    included=None;redacted=False
    if policy.mode=='replay':
        raw=plan.model_dump(mode='json');raw['agent']['source_dir']='.';raw['scenario']['generation']=None
        included,redacted=sanitize(raw,policy.redact_paths)
        if redacted:included=None
        else:included=offline_plan(IntegrationPlan.model_validate(included)).model_dump(mode='json')
    return ReproBundle(source_digest=result.run_manifest.source_digest,signature=signature(result),
        evidence_digest=digest(result.model_dump(mode='json')),plan=included,redacted=redacted)


def reproduce(bundle,source_dir):
    bundle=ReproBundle.model_validate(bundle.model_dump(mode='json'))
    if bundle.plan is None or bundle.redacted:raise ValueError('Metadata/redacted bundle is not runnable; re-review synthetic case data')
    plan=offline_plan(IntegrationPlan.model_validate(bundle.plan))
    files,observed=AgentLaunchSpec(reference=plan.agent.reference,source_dir=Path(source_dir),include_files=plan.agent.include_files).sources()
    if observed!=bundle.source_digest:raise ValueError('Supplied source does not match pinned artifact')
    # Snapshot checked bytes before execution; source changes cannot race the hash check.
    with tempfile.TemporaryDirectory(prefix='agentguard-repro-') as temporary:
        root=Path(temporary)
        for name,data in files.items():
            path=root/safe_relative(name);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        result=run_integration_plan(plan,base_dir=root)
    source_verified=result.run_manifest is not None and result.run_manifest.source_digest==bundle.source_digest
    reproduced=source_verified and result.cleanup_status=='verified' and result.evaluation_status=='complete' and (
        result.status in {'passed','failed'} or result.policy_status=='violation') and signature(result)==bundle.signature
    return dict(reproduced=reproduced,source_verified=source_verified,cleanup_status=result.cleanup_status,
        execution_gate='ALLOW' if effective_pass(result) else 'BLOCK',release_eligible=False,
        signature=signature(result),expected_signature=bundle.signature)
