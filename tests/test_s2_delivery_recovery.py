"""Cached completed execution recovery with real HTTP/state. PYTEST_DONT_REWRITE."""
import json,threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from agentguard.management.cache import EvidenceCache,CacheError
from agentguard.management.client import Client
from agentguard.management.delivery import RecoveryPolicy,deliver_pending
from agentguard.management.runner import PrivateRunner
from agentguard.management.http import running_server
from agentguard.management.store import AccessError
from test_phase7_results import execute_http_job
pytestmark=pytest.mark.integration
TOKEN='ag7r_'+'s'*43

@pytest.fixture(autouse=True)
def private_fixture(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    secure_fixture_directory(tmp_path)

def policy():return RecoveryPolicy(max_attempts=3,total_seconds=3,request_timeout=.5,base_delay=.02,max_delay=.03)

@pytest.mark.parametrize('mode',['restart','lost_ack','cancel','withdraw','revoke','expired'])
def test_real_report_recovery_preserves_effect_and_gate_contracts(tmp_path,monkeypatch,mode):
    import agentguard.management.runner as module
    s,o,p,w,j,claim,report=execute_http_job(tmp_path);calls=[];original=s.finish
    def finish(*a,**kw):
        calls.append(1)
        if len(calls)==1 and mode in {'lost_ack','cancel','withdraw','revoke'}:
            if mode=='lost_ack':original(*a,**kw)
            if mode=='cancel':s.cancel(o,p,j['id'])
            if mode=='withdraw':s.retire_case(o,p,s.objects(o,p,'case')[0]['id'])
            if mode=='revoke':s.revoke_runner(o,p,w['id'])
            raise ConnectionResetError()
        return original(*a,**kw)
    s.finish=finish
    if mode=='expired':
        now=s.clock();s.clock=lambda:now+500
    invoked=[]
    def forbidden(*a,**kw):invoked.append(1);raise AssertionError('Recovery must not execute')
    monkeypatch.setattr(module,'execute_spec',forbidden)
    with running_server(s) as url:
        c=EvidenceCache(tmp_path/'cache',server=url,credential=w['runner_token']);c.begin(claim);c.complete(claim,report)
        worker=PrivateRunner(Client(url,w['runner_token']),source_root=tmp_path,cache_root=tmp_path/'cache',recovery_policy=policy())
        if mode in {'revoke','expired'}:
            with pytest.raises(AccessError):worker.run_once()
            assert c.scan()[0]['state']=='pending_upload'
        else:
            reply=worker.run_once();assert c.scan()[0]['state']=='acknowledged'
            assert reply['gate_decision']==('BLOCK' if mode in {'cancel','withdraw'} else 'ALLOW')
            assert reply['cleanup_status']=='verified'
        assert not invoked
        assert len(calls)==(2 if mode in {'lost_ack','cancel','withdraw','revoke'} else 1)
    with s.db() as db:
        assert db.execute('SELECT count(*) FROM artifacts WHERE job=?',(j['id'],)).fetchone()[0]==(0 if mode in {'revoke','expired'} else 1)
        assert db.execute("SELECT count(*) FROM usage WHERE job=? AND kind='admitted_run'",(j['id'],)).fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM usage WHERE job=? AND kind='lease_wall_seconds'",(j['id'],)).fetchone()[0]==(0 if mode in {'revoke','expired'} else 1)

def test_concurrent_recovery_has_one_finish_and_one_artifact(tmp_path):
    s,o,p,w,j,claim,report=execute_http_job(tmp_path);entered=threading.Event();release=threading.Event();calls=[];original=s.finish
    def finish(*a,**kw):calls.append(1);entered.set();assert release.wait(10);return original(*a,**kw)
    s.finish=finish
    with running_server(s) as url:
        first=EvidenceCache(tmp_path/'cache',server=url,credential=w['runner_token']);other=EvidenceCache(tmp_path/'cache',server=url,credential=w['runner_token'])
        first.begin(claim);first.complete(claim,report);client=Client(url,w['runner_token'])
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(deliver_pending,first,client,p,j['id'],policy=RecoveryPolicy(total_seconds=15,request_timeout=10))
            try:
                assert entered.wait(10)
                with pytest.raises(CacheError,match='CACHE_BUSY'):deliver_pending(other,client,p,j['id'],policy=policy())
            finally:release.set()
            assert future.result(10)['gate_decision']=='ALLOW'
        assert len(calls)==1

@pytest.mark.parametrize('change',['origin','runner','project','job'])
def test_wrong_delivery_binding_never_sends_rpc(tmp_path,change):
    c=EvidenceCache(tmp_path/'cache',server='http://127.0.0.1:8766',credential=TOKEN)
    q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1});c.begin(q);c.complete(q,{'failure':'RUNNER_EXECUTION_ERROR'})
    client=Client('http://localhost:8766' if change=='origin' else 'http://127.0.0.1:8766','ag7r_'+'t'*43 if change=='runner' else TOKEN)
    calls=[]
    def request(*a,**kw):calls.append(1);raise AssertionError('Must not send')
    client.request=request
    with pytest.raises(AccessError):deliver_pending(c,client,'c'*32 if change=='project' else q['project'],'c'*32 if change=='job' else q['id'],policy=policy())
    assert not calls and c.scan()[0]['state']=='pending_upload'

@pytest.mark.parametrize('state',['incomplete','corrupt','legacy'])
def test_unverified_records_never_upload_or_claim(tmp_path,state):
    client=Client('http://127.0.0.1:8766',TOKEN)
    c=EvidenceCache(tmp_path/'cache',server=client.url,credential=TOKEN)
    q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1});c.begin(q)
    if state=='corrupt':(c.root/q['project']/(q['id']+'.entry.json')).write_text('{')
    if state=='legacy':(c.root/'.agentguard-runner-cache').write_text('private-runner-cache-v1')
    calls=[]
    def request(*a,**kw):calls.append(1);raise AssertionError('Must not send')
    client.request=request
    worker=PrivateRunner(client,source_root=tmp_path,cache_root=c.root,recovery_policy=policy())
    with pytest.raises(AccessError,match='CACHE_RECOVERY_REQUIRED'):worker.run_once()
    assert not calls

def test_cli_explicit_recovery_delivers_without_agent_execution(tmp_path,monkeypatch):
    from typer.testing import CliRunner
    from agentguard.cli.main import app
    from agentguard.management.credentials import private_file
    import agentguard.management.runner as module
    s,o,p,w,j,claim,report=execute_http_job(tmp_path)
    key=tmp_path/'runner.key';private_file(key,lambda:dict(runner_token=w['runner_token']))
    invoked=[]
    def forbidden(*a,**kw):invoked.append(1);raise AssertionError('Must not execute')
    monkeypatch.setattr(module,'execute_spec',forbidden)
    with running_server(s) as url:
        cache=EvidenceCache(tmp_path/'cache',server=url,credential=w['runner_token']);cache.begin(claim);cache.complete(claim,report)
        result=CliRunner().invoke(app,['management','runner','--server',url,'--credential-file',str(key),'--workspace',str(tmp_path),'--cache',str(cache.root),'--once','--recover-pending'])
        assert result.exit_code==0 and not invoked and w['runner_token'] not in result.output and claim['lease'] not in result.output
        assert cache.scan()[0]['state']=='acknowledged'

def test_transaction_rollback_before_reply_is_safe_to_retry(tmp_path,monkeypatch):
    s,o,p,w,j,claim,report=execute_http_job(tmp_path);original=s._job_event;crashed=[]
    def event(db,job,project,kind):
        if kind=='completed' and not crashed:crashed.append(1);raise RuntimeError('Synthetic pre-commit rollback')
        return original(db,job,project,kind)
    monkeypatch.setattr(s,'_job_event',event)
    with running_server(s) as url:
        cache=EvidenceCache(tmp_path/'cache',server=url,credential=w['runner_token']);cache.begin(claim);cache.complete(claim,report)
        assert deliver_pending(cache,Client(url,w['runner_token']),p,j['id'],policy=policy())['gate_decision']=='ALLOW'
    with s.db() as db:
        assert len(crashed)==1 and db.execute('SELECT count(*) FROM artifacts WHERE job=?',(j['id'],)).fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM usage WHERE job=? AND kind='lease_wall_seconds'",(j['id'],)).fetchone()[0]==1
