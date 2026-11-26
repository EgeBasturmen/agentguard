"""Customer-side execution. The management server never imports/builds customer agent code."""
from pathlib import Path
import tempfile
import json
import os
import re
import threading
import time
from agentguard.generation.repository import ScenarioRepository,Snapshot
from agentguard.generation.models import Candidate
from agentguard.generation.execution import SuiteRunner
from agentguard.integrations.plan import run_integration_plan,IntegrationPlan
from agentguard.execution.agent_spec import AgentLaunchSpec
from agentguard.management.objects import safe_runtime
from agentguard.management.store import AccessError

def execute_spec(spec,source_root,*,cancel_event=None,allow_external=False,allow_live_models=False):
    if cancel_event is not None and cancel_event.is_set():raise AccessError('RUNNER_EXECUTION_CANCELLED',409)
    root=Path(source_root).resolve(strict=True)
    snapshot=Snapshot.model_validate(spec['snapshot']);connection=spec['connection']
    for member in spec['cases']:
        case=Candidate.model_validate(member['case']);runtime=safe_runtime(case.runtime.model_dump(mode='json'),allow_live_models=allow_live_models)
        if runtime.model_dump(mode='json')!=connection['runtime']:raise AccessError('CONNECTION_BINDING_CHANGED',400)
        if (runtime.agent.transport=='http' or runtime.environment.kind=='http' or any(t.endpoint is not None for t in runtime.tools) or runtime.model and runtime.model.mode=='live') and not allow_external:raise AccessError('RUNNER_EXTERNAL_GRANT_REQUIRED',400)
        source=root/runtime.agent.source_dir
        if source.is_symlink() or not source.resolve().is_relative_to(root):raise AccessError('SOURCE_ROOT_ESCAPE',400)
        if runtime.agent.transport=='python':
            _,observed=AgentLaunchSpec(reference=runtime.agent.reference,source_dir=source,include_files=runtime.agent.include_files).sources()
            if observed!=connection['artifact_digest']:raise AccessError('SOURCE_VERSION_MISMATCH',400)
        elif runtime.agent.transport=='container' and runtime.agent.image.split('sha256:')[-1]!=connection['artifact_digest']:raise AccessError('IMAGE_VERSION_MISMATCH',400)
    with tempfile.TemporaryDirectory(prefix='agentguard-private-runner-') as directory:
        repository=ScenarioRepository(Path(directory)/'admission.sqlite')
        for member in spec['cases']:
            case=Candidate.model_validate(member['case']);review=member['review'];repository.add(case)
            repository.review(case.id,case.revision,reviewer=review['reviewer'],decisions=review['decisions'],
                fixture_confirmed=review['fixture_confirmed'],classification_confirmed=review['classification_confirmed'],notes=review['notes'])
            repository.approve(case.id,case.revision,reviewer=review['approver'],expected_digest=case.fingerprint)
        actual=repository.freeze(snapshot.id,[(p['id'],p['revision']) for p in snapshot.cases],requirements=snapshot.requirements)
        if actual!=snapshot:raise AccessError('APPROVAL_BINDING_CHANGED',400)
        if spec['kind']=='preflight':
            checks=[]
            for member in spec['cases']:
                if cancel_event is not None and cancel_event.is_set():raise AccessError('RUNNER_EXECUTION_CANCELLED',409)
                case=Candidate.model_validate(member['case']);result=run_integration_plan(IntegrationPlan.model_validate(case.plan()),base_dir=root,preflight_only=True)
                checks.append(dict(case_id=case.id,supported=result.supported,issues=result.issues,coverage_gaps=result.coverage_gaps))
            return dict(preflight=dict(schema_version=1,checks=checks,agent_invoked=False,verification_level='configuration/source-pin/preflight; not behavior'))
        if spec['kind']!='suite':raise AccessError('UNKNOWN_JOB_KIND',400)
        if cancel_event is not None and cancel_event.is_set():raise AccessError('RUNNER_EXECUTION_CANCELLED',409)
        execution=SuiteRunner().run(repository,snapshot,base_dir=root,cancel_event=cancel_event)
        return dict(execution=execution.model_dump(mode='json'))


class PrivateRunner:
    def __init__(self,client,*,source_root,cache_root,allow_external=False,allow_live_models=False):
        self.client=client;self.source_root=Path(source_root).resolve(strict=True);self.cache=Path(cache_root).absolute();self.allow_external=allow_external;self.allow_live_models=allow_live_models
        if self.cache.is_symlink():raise ValueError('Symlink cache is forbidden')
        self.cache.mkdir(parents=True,exist_ok=True,mode=0o700)
        marker=self.cache/'.agentguard-runner-cache'
        if not marker.exists():
            if any(self.cache.iterdir()):raise ValueError('Use a new empty directory for the private evidence cache')
            with marker.open('x',encoding='utf-8') as stream:stream.write('private-runner-cache-v1')
        elif marker.is_symlink() or marker.read_text(encoding='utf-8')!='private-runner-cache-v1':raise ValueError('Invalid private cache marker')

    def _cache(self,claim,result):
        from agentguard.management.store import check_id,encode
        folder=self.cache/check_id(claim['project'])
        if folder.is_symlink():raise ValueError('Invalid cache folder')
        folder.mkdir(mode=0o700,exist_ok=True)
        path=folder/(check_id(claim['id'])+'.json')
        descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:stream.write(encode(result))
        meta=folder/(claim['id']+'.meta.json')
        descriptor=os.open(meta,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:json.dump(dict(expires=time.time()+min(30,claim['spec'].get('retention_days',7))*86400),stream)

    def purge_cache(self):
        for folder in self.cache.iterdir():
            if not re.fullmatch('[0-9a-f]{32}',folder.name) or folder.is_symlink() or not folder.is_dir():continue
            for meta in folder.glob('*.meta.json'):
                if meta.is_symlink() or not re.fullmatch('[0-9a-f]{32}\\.meta\\.json',meta.name) or meta.stat().st_size>1024:continue
                try:expires=json.loads(meta.read_text(encoding='utf-8'))['expires']
                except (ValueError,KeyError):continue
                if type(expires) not in {int,float} or expires>time.time():continue
                evidence=folder/(meta.name[:32]+'.json')
                if evidence.is_symlink():raise ValueError('Unsafe private evidence')
                if evidence.exists():evidence.unlink()
                meta.unlink()

    def run_once(self):
        self.purge_cache();claim=self.client.request('/api/runner/claim',{})
        if claim is None:return None
        cancel=threading.Event();stop=threading.Event();deadline=time.monotonic()+claim['max_seconds']
        def denied():
            # Local operational summary, never a forged server completion/cleanup receipt.
            return dict(id=claim['id'],project=claim['project'],state='error',gate_decision='BLOCK',cleanup_status='unverified',authorizes_release=False,assurance='runner pre-execution fence denied; consult server job')
        def permitted(reply):
            return isinstance(reply,dict) and reply.get('cancel') is False and type(reply.get('lease_seconds')) is int and 0<reply['lease_seconds']<=30
        # Existing heartbeat is the start fence; a background thread is not an ACK.
        try:
            if not permitted(self.client.request('/api/runner/heartbeat',dict(job_id=claim['id'],lease=claim['lease']))):return denied()
        except Exception:return denied()
        def heartbeat():
            while not stop.wait(2):
                if time.monotonic()>=deadline:cancel.set();return
                try:
                    result=self.client.request('/api/runner/heartbeat',dict(job_id=claim['id'],lease=claim['lease']))
                    if not permitted(result):cancel.set();return
                except Exception:cancel.set();return
        thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
        try:
            if cancel.is_set() or time.monotonic()>=deadline:return denied()
            try:result=execute_spec(claim['spec'],self.source_root,cancel_event=cancel,allow_external=self.allow_external,allow_live_models=self.allow_live_models)
            except AccessError as error:
                result=dict(failure=error.code if error.code in {'SOURCE_VERSION_MISMATCH','RUNNER_EXTERNAL_GRANT_REQUIRED'} else 'RUNNER_EXECUTION_ERROR')
            except Exception:result=dict(failure='RUNNER_EXECUTION_ERROR')
            self._cache(claim,result)
            return self.client.request('/api/runner/finish',dict(job_id=claim['id'],lease=claim['lease'],report=result))
        finally:stop.set();thread.join(timeout=12)
