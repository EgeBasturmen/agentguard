"""Static readiness is not oracle truth. Approval always requires explicit operator review."""
from dataclasses import dataclass,field
from agentguard.core.scenario import Scenario
from agentguard.core.paths import values_equal,parse_path
from agentguard.core.manifest import canonical_json
from agentguard.contracts.compatibility import controlled_capabilities
from agentguard.contracts.models import Capabilities
from agentguard.integrations.schema import validate
from agentguard.generation.models import Candidate,definitions
from agentguard.environments.adapters import safe_relative

@dataclass
class ValidationResult:
    issues: list=field(default_factory=list)
    oracle_ids: list=field(default_factory=list)
    @property
    def ready(self): return not self.issues


def validate_candidate(candidate):
    result=ValidationResult()
    try: candidate=Candidate.model_validate(candidate.model_dump(mode='json'))
    except Exception as exc:
        result.issues.append('Candidate/source integrity error: '+str(exc));return result
    result.issues.extend(candidate.unresolved)
    raw=candidate.scenario
    # Conflicts keep their source IDs rather than selecting an arbitrary winning rule.
    expected={}
    for check in raw.get('expected_state',[]):
        try:key=tuple(parse_path(check['path']))
        except Exception:continue
        if key in expected and not values_equal(expected[key],check.get('value')):
            result.issues.append('Oracle conflict at '+check['path']+'; sources: '+', '.join(s.id for s in candidate.sources if s.kind=='rule'))
        expected[key]=check.get('value')
    try:
        if raw.get('generation') is not None:raise ValueError('Stored drafts cannot contain release admission claims')
        scenario=Scenario.model_validate(raw)
        checks=definitions(raw)
        if set(checks)!=set(candidate.oracle_sources):raise ValueError('Every check needs independent oracle provenance')
        sources={s.id:s for s in candidate.sources}
        for identity,check in checks.items():
            refs=candidate.oracle_sources[identity]
            if not refs or any(ref not in sources for ref in refs):raise ValueError('Oracle source reference is missing')
            if not all(sources[ref].kind in {'rule','manual'} and values_equal(sources[ref].content.get('checks',{}).get(identity),check) for ref in refs):
                raise ValueError('Oracle lacks matching independent rule/manual evidence; trace output is not truth')
        result.oracle_ids=sorted(checks)
        kind=candidate.runtime.environment.kind
        fixture=scenario.initial_state
        if len(canonical_json(fixture).encode())>1024*1024:raise ValueError('Fixture exceeds 1 MiB')
        if kind=='filesystem':
            if set(fixture)!={'files'} or not isinstance(fixture['files'],dict) or len(fixture['files'])>128:raise ValueError('Invalid filesystem fixture')
            for name,content in fixture['files'].items():
                safe_relative(name)
                if not isinstance(content,str):raise ValueError('Filesystem contents must be strings')
        if kind=='dataset':
            if set(fixture)!={'documents'} or not isinstance(fixture['documents'],dict):raise ValueError('Invalid dataset fixture')
            for name,doc in fixture['documents'].items():
                safe_relative(name)
                if '/' in name or not isinstance(doc,dict) or not isinstance(doc.get('content'),str):raise ValueError('Invalid document fixture')
        agent=candidate.runtime.agent.transport
        capabilities=Capabilities(supports_output=True,supports_state_snapshot=True) if agent=='http' else controlled_capabilities()
        for name in candidate.runtime.required_capabilities:
            if not getattr(capabilities,name,False):raise ValueError('Missing capability: '+name)
        if agent=='http' and (kind!='http' or scenario.tool_expectations):raise ValueError('Observed HTTP agent cannot verify tool expectations or non-HTTP state')
        names=set()
        for source in candidate.sources:
            if source.kind=='contract':
                contract=source.content['contract'];names.add(contract['name'])
        if kind=='filesystem':names.update({'file.read','file.write','file.delete'})
        if kind=='dataset':names.update({'document.get','document.search'})
        for config in candidate.runtime.tools:
            if config.contract:names.add(config.contract['name'])
            if config.connector=='mock' and (scenario.expected_state or scenario.forbidden_changes or scenario.invariants):
                raise ValueError('Mock cannot verify business-state oracle')
        allowed=set(candidate.runtime.policy['allowed_tools'])
        if not allowed.issubset(names):raise ValueError('Policy references an undiscovered tool contract')
        for check in scenario.tool_expectations:
            if check.tool_name not in names:raise ValueError('Unknown expected tool: '+check.tool_name)
            if check.times and check.tool_name not in allowed:raise ValueError('Required tool is not permitted')
        if candidate.observations.get('arguments') is not None:
            if not capabilities.supports_tool_observation:raise ValueError('Boundary argument coverage requires authoritative tool observation')
            tool=candidate.observations.get('tool')
            if tool not in allowed:raise ValueError('Boundary tool is not permitted by the policy')
            for field,scopes in candidate.runtime.policy['resource_scopes'].get(tool,{}).items():
                if candidate.observations['arguments'].get(field) not in scopes:raise ValueError('Boundary input is outside the permitted resource scope')
            contracts=[s.content['contract'] for s in candidate.sources if s.kind=='contract' and s.content['contract']['name']==tool]
            if not contracts:raise ValueError('Boundary case has no pinned contract')
            validate(contracts[0]['input_schema'],candidate.observations['arguments'])
    except Exception as exc:result.issues.append('Schema/fixture/oracle/capability validation: '+str(exc))
    return result
