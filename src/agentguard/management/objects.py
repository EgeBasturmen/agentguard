"""Project-scoped configuration/review service reusing the local scenario repository."""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit
from agentguard.management.store import AccessError,identity,check_id,label,encode
from agentguard.management.redaction import reject_credentials
from agentguard.management.limits import PLANS,logical_storage
from agentguard.generation.models import RuntimeTemplate,GenerationRequest,definitions
from agentguard.generation.generator import generate
from agentguard.generation.validation import validate_candidate
from agentguard.security.policy import SecurityPolicy
from agentguard.execution.agent_spec import AgentLaunchSpec
from agentguard.environments.adapters import safe_relative

def safe_runtime(value,*,allow_live_models=False):
    runtime=RuntimeTemplate.model_validate(value)
    reject_credentials(runtime.model_dump(mode='json'))
    config=runtime.agent
    if config.source_dir!='.':safe_relative(config.source_dir)
    for name in config.include_files:safe_relative(name)
    if any(not name.endswith('.py') for name in config.include_files):raise AccessError('ONLY_EXPLICIT_PYTHON_SOURCE_INCLUDES_SUPPORTED',400)
    if config.transport=='python':AgentLaunchSpec(reference=config.reference or '',source_dir=Path('.'),include_files=config.include_files)
    if config.transport=='container' and (not config.image or not re.fullmatch(r'(?:[A-Za-z0-9_./:-]+@)?sha256:[0-9a-f]{64}',config.image)):raise AccessError('PINNED_IMAGE_REQUIRED',400)
    if config.transport=='http' and config.endpoint is None:raise AccessError('ENDPOINT_REQUIRED',400)
    if runtime.model and runtime.model.mode=='live' and not allow_live_models:raise AccessError('EXPLICIT_OPERATOR_AND_RUNNER_MODEL_GRANT_REQUIRED',400)
    if runtime.model and runtime.model.provider not in {'gemini','json-http','mock'}:raise AccessError('UNSUPPORTED_GATEWAY_PROVIDER_USE_EXPLICIT_HTTP_BRIDGE',400)
    for endpoint in [config.endpoint,runtime.environment.endpoint,*[tool.endpoint for tool in runtime.tools]]:
        if endpoint is not None:
            parsed=urlsplit(endpoint.base_url)
            if parsed.username or parsed.password or parsed.query or parsed.fragment:raise AccessError('ENDPOINT_CREDENTIALS_OR_QUERY_FORBIDDEN',400)
            endpoint.endpoint() # Static URL/bounds checks only; no DNS, HTTP, discovery or code import.
    policy=SecurityPolicy.model_validate(runtime.policy)
    if policy.execution_mode!='isolated' or policy.allow_network or policy.allow_host_environment or policy.allow_host_filesystem:raise AccessError('UNSAFE_AGENT_GRANT',400)
    return runtime

def declared_capabilities(runtime):
    from agentguard.integrations.agents import PythonAgent,HTTPAgent,ContainerAgent,ContainerLaunchSpec
    if runtime.agent.transport=='python':
        available=PythonAgent(AgentLaunchSpec(reference=runtime.agent.reference,source_dir=Path('.'))).capabilities()
        available.supports_sessions=True;available.supports_memory_reset=True
    elif runtime.agent.transport=='container':available=ContainerAgent(ContainerLaunchSpec(image=runtime.agent.image,reference=runtime.agent.reference or 'agent')).capabilities()
    else:available=HTTPAgent(runtime.agent.endpoint.endpoint()).capabilities()
    if runtime.model is not None and runtime.agent.transport!='http':available.supports_model_usage=True
    return available.model_dump(mode='json')

def check_model_pricing(config,pricing):
    if not pricing or config.get('input_rate_micro_usd_per_million',0)<pricing['input_rate'] or config.get('output_rate_micro_usd_per_million',0)<pricing['output_rate'] or config.get('pricing_version')!=pricing['version']:raise AccessError('OPERATOR_PRICING_POLICY_REQUIRED',400)

class Objects:
    def _object(self,db,project,object_id,kind=None):
        row=db.execute('SELECT * FROM objects WHERE id=? AND project=?',(check_id(object_id),project)).fetchone()
        if row is None or kind is not None and row['kind']!=kind:raise AccessError('NOT_FOUND',404)
        return dict(row)

    def _put(self,db,project,kind,value):
        reject_credentials(value);payload=encode(value)
        plan=PLANS[db.execute('SELECT plan FROM projects WHERE id=?',(project,)).fetchone()[0]]
        if len(payload.encode())>128*1024:raise AccessError('DEFINITION_TOO_LARGE',413)
        if db.execute('SELECT count(*) FROM objects WHERE project=?',(project,)).fetchone()[0]>=plan['objects'] or logical_storage(db,project)+len(payload.encode())>plan['storage']:raise AccessError('STORAGE_QUOTA',409)
        object_id=identity();db.execute('INSERT INTO objects VALUES(?,?,?,?,?)',(object_id,project,kind,payload,self.clock()))
        return dict(id=object_id,**value)

    def connect(self,token,project,name,runtime,*,artifact_digest):
        with self.lock,self.db() as db:
            actor=self._auth(db,token,project,'agent:write')
            grants=db.execute('SELECT model_tokens,model_cost,model_pricing FROM projects WHERE id=?',(project,)).fetchone()
            value=safe_runtime(runtime,allow_live_models=grants['model_tokens']>0 and grants['model_cost']>0)
            if value.model and value.model.mode=='live':check_model_pricing(value.model.model_dump(mode='json'),json.loads(grants['model_pricing']))
            if not isinstance(artifact_digest,str) or not re.fullmatch('[0-9a-f]{64}',artifact_digest):raise AccessError('ARTIFACT_DIGEST_REQUIRED',400)
            result=self._put(db,project,'connection',dict(name=label(name),runtime=value.model_dump(mode='json'),artifact_digest=artifact_digest,
                connection_status='configuration_saved',verified=False,automatic_discovery=False,
                declared_capabilities=declared_capabilities(value),verified_capabilities=[],
                coverage_gaps=['Runner preflight required; HTTP internals cannot be intercepted.' if value.agent.transport=='http' else 'Runner workspace/source and tool/environment mapping require verification.']))
            self.event(db,actor['workspace'],project,actor['user'],'agent.connect',result['id']);return result

    def objects(self,token,project,kind,*,offset=0,limit=50):
        if kind not in {'connection','case','suite','comparison'} or type(offset) is not int or type(limit) is not int or offset<0 or not 1<=limit<=100:raise AccessError('INVALID_PAGE',400)
        with self.db() as db:
            self._auth(db,token,project)
            rows=db.execute('SELECT * FROM objects WHERE project=? AND kind=? ORDER BY created,id LIMIT ? OFFSET ?',(project,kind,limit,offset)).fetchall()
            result=[]
            for row in rows:
                value=json.loads(row['payload'])
                if kind=='case':value=self._case_view(project,row['id'],value)
                else:value=dict(id=row['id'],**value)
                result.append(value)
            return result

    def _case_view(self,project,object_id,payload):
        case,status,review=self.scenario_repository(project).get(payload['case']['id'],payload['case']['revision'])
        validation=validate_candidate(case)
        return dict(id=object_id,case=case.model_dump(mode='json'),status=status,review=review,
            oracle_checks={key:definitions(case.scenario)[key] for key in case.oracle_checks},ready=validation.ready,issues=validation.issues,connection=payload['connection'])

    def case(self,token,project,case_id):
        with self.db() as db:
            self._auth(db,token,project);row=self._object(db,project,case_id,'case')
            return self._case_view(project,case_id,json.loads(row['payload']))

    def generate_cases(self,token,project,connection,*,task,fixture,rules,contracts=None,traces=None):
        with self.lock,self.db() as db:
            actor=self._auth(db,token,project,'suite:write');source=json.loads(self._object(db,project,connection,'connection')['payload'])
            request=GenerationRequest.model_validate(dict(task=task,fixture=fixture,rules=rules,runtime=source['runtime'],contracts=contracts or [],traces=traces or [],max_cases=20))
            reject_credentials(request.model_dump(mode='json'));batch=generate(request);repo=self.scenario_repository(project);items=[]
            for candidate in batch.cases:
                item=self._put(db,project,'case',dict(case=candidate.model_dump(mode='json'),connection=connection))
                repo.add(candidate);items.append(item)
            self.event(db,actor['workspace'],project,actor['user'],'scenario.generate',connection)
            return dict(cases=items,approved=0,review_required=True,gaps=batch.gaps)

    def review_case(self,token,project,case_id,*,decisions,fixture,classification,notes=''):
        with self.lock,self.db() as db:
            actor=self._auth(db,token,project,'suite:review');payload=json.loads(self._object(db,project,case_id,'case')['payload']);reject_credentials(notes)
            case=payload['case'];review=self.scenario_repository(project).review(case['id'],case['revision'],reviewer=actor['user'],decisions=decisions,
                fixture_confirmed=fixture,classification_confirmed=classification,notes=notes)
            self.event(db,actor['workspace'],project,actor['user'],'scenario.review',case_id);return review

    def approve_case(self,token,project,case_id,fingerprint):
        with self.lock,self.db() as db:
            actor=self._auth(db,token,project,'suite:review');payload=json.loads(self._object(db,project,case_id,'case')['payload']);case=payload['case']
            self.scenario_repository(project).approve(case['id'],case['revision'],reviewer=actor['user'],expected_digest=fingerprint)
            self.event(db,actor['workspace'],project,actor['user'],'scenario.approve',case_id)
            return dict(id=case_id,status='approved')

    def revise_case(self,token,project,case_id,scenario,reason):
        with self.lock,self.db() as db:
            actor=self._auth(db,token,project,'suite:write');payload=json.loads(self._object(db,project,case_id,'case')['payload']);reject_credentials(scenario);reject_credentials(reason)
            old=payload['case'];new=self.scenario_repository(project).revise(old['id'],old['revision'],scenario=scenario,reason=label(reason))
            result=self._put(db,project,'case',dict(case=new.model_dump(mode='json'),connection=payload['connection']))
            self.event(db,actor['workspace'],project,actor['user'],'scenario.revise',result['id']);return dict(**result,status='draft')

    def retire_case(self,token,project,case_id):
        with self.lock,self.db() as db:
            actor=self._auth(db,token,project,'suite:review');payload=json.loads(self._object(db,project,case_id,'case')['payload']);case=payload['case']
            self.scenario_repository(project).retire(case['id'],case['revision'])
            self.event(db,actor['workspace'],project,actor['user'],'scenario.retire',case_id);return dict(retired=True)

    def freeze_suite(self,token,project,name,cases):
        if not isinstance(cases,list) or not 1<=len(cases)<=20 or len(cases)!=len(set(cases)):raise AccessError('INVALID_SUITE_MEMBERS',400)
        with self.lock,self.db() as db:
            actor=self._auth(db,token,project,'suite:review');pins=[];connections=set()
            for case_id in cases:
                payload=json.loads(self._object(db,project,case_id,'case')['payload']);case=payload['case'];connections.add(payload['connection']);pins.append((case['id'],case['revision']))
            if len(connections)!=1:raise AccessError('SUITE_REQUIRES_ONE_AGENT_VERSION',400)
            snapshot=self.scenario_repository(project).freeze(label(name),pins)
            result=self._put(db,project,'suite',dict(name=name,snapshot=snapshot.model_dump(mode='json'),connection=connections.pop()))
            self.event(db,actor['workspace'],project,actor['user'],'suite.freeze',result['id']);return result

    def _suite_spec(self,db,project,suite):
        value=json.loads(self._object(db,project,suite,'suite')['payload'])
        from agentguard.generation.repository import Snapshot
        snapshot=Snapshot.model_validate(value['snapshot']);repo=self.scenario_repository(project);repo.check_snapshot(snapshot);cases=[]
        for pin in snapshot.cases:
            case,review=repo.admission(snapshot,pin);cases.append(dict(case=case.model_dump(mode='json'),review=review))
        connection=json.loads(self._object(db,project,value['connection'],'connection')['payload'])
        return dict(snapshot=value['snapshot'],cases=cases,connection=connection)

    def suite_spec(self,token,project,suite):
        with self.db() as db:
            self._auth(db,token,project);return self._suite_spec(db,project,suite)

    def bind_agent_version(self,token,project,suite,connection):
        with self.lock,self.db() as db:
            actor=self._auth(db,token,project,'suite:review');original=json.loads(self._object(db,project,suite,'suite')['payload'])
            old=self._suite_spec(db,project,suite)['connection'];new=json.loads(self._object(db,project,connection,'connection')['payload'])
            if old['runtime']!=new['runtime']:raise AccessError('RUNTIME_CONTRACT_CHANGED_REVIEW_NEW_CASES',409)
            original['connection']=connection;result=self._put(db,project,'suite',original)
            self.event(db,actor['workspace'],project,actor['user'],'suite.bind_agent_version',result['id']);return result
