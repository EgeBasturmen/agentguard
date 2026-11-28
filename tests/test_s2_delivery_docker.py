"""Real Docker agent execution exactly once, retry only finish. PYTEST_DONT_REWRITE."""
import pytest
from test_phase7_workflow import setup_project
from test_phase7_queue import runner
from test_phase7_docker import prepare
from s2_transport_support import MeasuredClient,require
pytestmark=pytest.mark.docker
@pytest.fixture(autouse=True)
def private_fixture(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    secure_fixture_directory(tmp_path)

@pytest.mark.parametrize('mode',['before_commit','after_commit','runner_restart'])
def test_real_agent_does_not_repeat_when_delivering_cached_report(tmp_path,monkeypatch,mode):
    from agentguard.management.http import running_server
    from agentguard.management.client import Client
    from agentguard.management.runner import PrivateRunner
    from agentguard.management.delivery import RecoveryPolicy
    from agentguard.management.store import AccessError
    from agentguard.orchestration.controller import RunController
    class FinishMeasuredClient(MeasuredClient):
        def request(self,path,*a,**kw):
            if path.endswith('/finish'):return super().request(path,*a,**kw)
            return Client.request(self,path,*a,**kw)
    s,o,p=setup_project(tmp_path);source=tmp_path/'source';source.mkdir()
    (source/'agent.py').write_text("class Agent:\n def run(self,task,sandbox):\n  sandbox.call('file.write',{'filename':'note.txt','content':'done'})\n  return 'done'\n")
    _,suite=prepare(s,o,p,source);w=runner(s,o,p);j=s.enqueue(o,p,suite['id'],'retry-proof');invoked=[];original=RunController.run
    def run(self,*a,**k):invoked.append(1);return original(self,*a,**k)
    monkeypatch.setattr(RunController,'run',run)
    finish=s.finish;calls=[]
    def lost(*a,**kw):
        calls.append(1)
        if len(calls)==1:
            if mode!='before_commit':finish(*a,**kw)
            raise ConnectionResetError()
        return finish(*a,**kw)
    s.finish=lost;policy=RecoveryPolicy(total_seconds=5,request_timeout=2,base_delay=.05,max_delay=.1)
    with running_server(s) as url:
        client=FinishMeasuredClient(url,w['runner_token']);initial=None
        private=PrivateRunner(client,source_root=source,cache_root=tmp_path/'cache',recovery_policy=None if mode=='runner_restart' else policy)
        if mode=='runner_restart':
            with pytest.raises(AccessError):private.run_once()
            initial=client;client=FinishMeasuredClient(url,w['runner_token'])
            private=PrivateRunner(client,source_root=source,cache_root=tmp_path/'cache',recovery_policy=policy)
        reply=private.run_once()
        assert reply['gate_decision']=='ALLOW' and reply['cleanup_status']=='verified'
        assert private.evidence.scan()[0]['state']=='acknowledged' and len(invoked)==1
        measurements=(initial.measurements if initial else [])+client.measurements
        metrics=dict(mode=mode,client_attempts=len(measurements),server_received=len(calls),recovery_attempts=len(client.measurements),rpc=measurements)
        require(1<=len(client.measurements)<=policy.max_attempts,**metrics)
        require(2<=len(calls)<=len(measurements),**metrics)
        if initial:require(len(initial.measurements)==1,**metrics)
    with s.db() as db:
        assert db.execute('SELECT count(*) FROM artifacts WHERE job=?',(j['id'],)).fetchone()[0]==1
        for kind in ('admitted_run','lease_wall_seconds'):
            assert db.execute('SELECT count(*) FROM usage WHERE job=? AND kind=?',(j['id'],kind)).fetchone()[0]==1

def test_existing_operator_cleanup_preserves_signed_failure_receipt(tmp_path):
    from agentguard.management.runner import execute_spec
    s,o,p=setup_project(tmp_path);source=tmp_path/'source';source.mkdir()
    (source/'agent.py').write_text("class Agent:\n def run(self,task,sandbox):\n  sandbox.call('file.write',{'filename':'note.txt','content':'done'})\n")
    _,suite=prepare(s,o,p,source);w=runner(s,o,p);j=s.enqueue(o,p,suite['id'],'cleanup-receipt');claim=s.claim(w['runner_token'])
    proof=execute_spec(claim['spec'],source,execution_binding=claim['recovery_binding'])
    from agentguard.management.cache import EvidenceCache
    cache=EvidenceCache(tmp_path/'cache',server='http://127.0.0.1:8766',credential=w['runner_token'])
    cache.begin(claim);cache.complete(claim,proof);evidence=cache.recovery_evidence(p,j['id'])
    s.register_recovery_evidence(w['runner_token'],j['id'],claim['lease'],evidence)
    failure={'failure':'RUNNER_EXECUTION_ERROR'}
    assert s.finish(w['runner_token'],j['id'],claim['lease'],failure)['gate_decision']=='BLOCK'
    assert s.recover_job(j['id'],evidence,token=o,project=p,operator='synthetic-operator',reason='Existing cleanup contract')['cleanup_status']=='verified'
    replay=s.finish(w['runner_token'],j['id'],claim['lease'],failure)
    assert replay['delivery_accepted'] and replay['gate_decision']=='BLOCK' and replay['cleanup_status']=='verified'
