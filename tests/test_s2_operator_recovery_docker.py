"""Actual Docker cleanup and real HTTP operator recovery. PYTEST_DONT_REWRITE."""
import json
import pytest
from agentguard.core.manifest import digest
from agentguard.management.service import AccessError
from agentguard.management.runner import PrivateRunner,execute_spec
from agentguard.management.cache import EvidenceCache
from agentguard.management.client import Client
from agentguard.management.http import running_server
from test_phase7_workflow import setup_project
from test_phase7_queue import runner
from test_phase7_docker import prepare

pytestmark=pytest.mark.docker

@pytest.fixture(autouse=True)
def private_fixture(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    secure_fixture_directory(tmp_path)

def fixture(path):
    s,o,p=setup_project(path);source=path/'source';source.mkdir()
    (source/'agent.py').write_text("class Agent:\n def run(self,task,sandbox):\n  sandbox.call('file.write',{'filename':'note.txt','content':'done'})\n  return 'done'\n")
    _,suite=prepare(s,o,p,source);w=runner(s,o,p);j=s.enqueue(o,p,suite['id'],'operator-proof')
    return s,o,p,source,suite,w,j

@pytest.mark.parametrize('mode',['valid','other_job','container_changed'])
def test_actual_cleanup_recovery_keeps_failure_blocked_and_never_executes_twice(tmp_path,monkeypatch,mode):
    from agentguard.orchestration.controller import RunController
    s,o,p,source,suite,w,j=fixture(tmp_path);invocations=[];finish_receives=[];finish_acceptances=[];registration_acceptances=[];client_attempts={}
    run=RunController.run;finish=s.finish;register=s.register_recovery_evidence
    def observed(self,*a,**kw):invocations.append(1);return run(self,*a,**kw)
    def failed(token,job_id,lease,report):
        finish_receives.append(1);reply=finish(token,job_id,lease,dict(failure='RUNNER_EXECUTION_ERROR'))
        if reply.get('delivery_accepted') is True:finish_acceptances.append(1)
        return reply
    def registered(*a,**kw):
        value=register(*a,**kw);registration_acceptances.append(1);return value
    class ObservedClient(Client):
        def request(self,path,*a,**kw):
            client_attempts[path]=client_attempts.get(path,0)+1
            return super().request(path,*a,**kw)
    monkeypatch.setattr(RunController,'run',observed);s.finish=failed;s.register_recovery_evidence=registered
    with running_server(s) as url:
        private=PrivateRunner(ObservedClient(url,w['runner_token']),source_root=source,cache_root=tmp_path/'cache')
        with pytest.raises(AccessError,match='CACHE_ACKNOWLEDGEMENT_UNVERIFIED'):private.run_once()
        e=private.evidence.recovery_evidence(p,j['id']);original=json.loads(json.dumps(e));job=j['id']
        assert private.evidence.scan()[0]['state']=='pending_upload'
        if mode=='other_job':
            other=s.enqueue(o,p,suite['id'],'second-job');claim=s.claim(w['runner_token']);assert claim['id']==other['id']
            finish(w['runner_token'],claim['id'],claim['lease'],dict(failure='RUNNER_EXECUTION_ERROR'));job=other['id']
        elif mode=='container_changed':
            e['payload']['report']['execution']['cases'][0]['result']['run_manifest']['container_id']='0'*64
            e['payload']['report_hash']=digest(e['payload']['report']);e['checksum']=digest(e['payload'])
        operator=Client(url,o);body=dict(report=e,operator='synthetic fixture',reason='Actual container cleanup report')
        path='/api/projects/'+p+'/runs/'+job+'/recover'
        if mode=='valid':
            a=operator.request(path,body);b=operator.request(path,body)
            assert a==b and a['cleanup_status']=='verified' and a['gate_decision']=='BLOCK' and not a['authorizes_release']
            receipt=finish(w['runner_token'],j['id'],original['payload']['lease'],dict(failure='RUNNER_EXECUTION_ERROR'))
            assert receipt['delivery_accepted'] and receipt['cleanup_status']=='verified' and receipt['gate_decision']=='BLOCK'
        else:
            with pytest.raises(AccessError):operator.request(path,body)
            assert s.job(o,p,job)['cleanup_status']=='unverified'
        assert len(invocations)==1 and len(finish_receives)==1 and len(finish_acceptances)==1 and len(registration_acceptances)==1
        assert client_attempts['/api/runner/finish']==1 and client_attempts['/api/runner/recovery-evidence']==1
        assert private.evidence.scan()[0]['state']=='pending_upload'
        assert s.job(o,p,job)['gate_decision']=='BLOCK'
        with s.db() as db:
            assert db.execute('SELECT count(*) FROM artifacts WHERE job=?',(job,)).fetchone()[0]==0
            assert db.execute("SELECT count(*) FROM usage WHERE job=? AND kind='admitted_run'",(job,)).fetchone()[0]==1
            assert db.execute("SELECT count(*) FROM usage WHERE job=? AND kind='lease_wall_seconds'",(job,)).fetchone()[0]==0

def test_old_runner_report_still_finishes_but_cannot_authorize_operator_cleanup(tmp_path):
    s,o,p,source,suite,w,j=fixture(tmp_path);claim=s.claim(w['runner_token'])
    report=execute_spec(claim['spec'],source) # Old runner: no issued runtime IDs.
    cache=EvidenceCache(tmp_path/'cache',server='http://127.0.0.1:8766',credential=w['runner_token'])
    cache.begin(claim);cache.complete(claim,report);e=cache.recovery_evidence(p,j['id'])
    with pytest.raises(AccessError,match='RECOVERY_RUNTIME_IDENTITY_MISMATCH'):s.register_recovery_evidence(w['runner_token'],j['id'],claim['lease'],e)
    assert s.finish(w['runner_token'],j['id'],claim['lease'],report)['gate_decision']=='ALLOW'
    with pytest.raises(AccessError):s.recover_job(j['id'],e,token=o,project=p,operator='fixture',reason='legacy identity is unverified')
