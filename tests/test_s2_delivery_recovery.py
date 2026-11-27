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
from s2_transport_support import MeasuredClient,require
pytestmark=pytest.mark.integration
TOKEN='ag7r_'+'s'*43

@pytest.fixture(autouse=True)
def private_fixture(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    secure_fixture_directory(tmp_path)

def policy():return RecoveryPolicy(max_attempts=3,total_seconds=3,request_timeout=.5,base_delay=.02,max_delay=.03)

def claimed_case_object(service,owner,project,claim):
    members=claim['spec']['cases']
    assert len(members)==1,'ONE_PINNED_CASE_FIXTURE_REQUIRED'
    case=members[0]['case'];pin=tuple(case[k] for k in ('id','revision','fingerprint'))
    matches=[row for row in service.objects(owner,project,'case') if tuple(row['case'][k] for k in ('id','revision','fingerprint'))==pin]
    assert len(matches)==1,json.dumps(dict(category='CLAIM_CASE_BINDING_UNVERIFIED',matches=len(matches),expected=1))
    return matches[0]['id']

@pytest.mark.parametrize('mode',['restart','lost_ack','cancel','withdraw','revoke','expired'])
def test_real_report_recovery_preserves_effect_and_gate_contracts(tmp_path,monkeypatch,mode):
    import agentguard.management.runner as module
    s,o,p,w,j,claim,report=execute_http_job(tmp_path);calls=[];original=s.finish
    withdrawn=claimed_case_object(s,o,p,claim) if mode=='withdraw' else None
    def finish(*a,**kw):
        calls.append(1)
        if len(calls)==1 and mode in {'lost_ack','cancel','withdraw','revoke'}:
            if mode=='lost_ack':original(*a,**kw)
            if mode=='cancel':s.cancel(o,p,j['id'])
            if mode=='withdraw':s.retire_case(o,p,withdrawn)
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
        client=MeasuredClient(url,w['runner_token']);limits=policy();terminal=None
        worker=PrivateRunner(client,source_root=tmp_path,cache_root=tmp_path/'cache',recovery_policy=limits)
        if mode in {'revoke','expired'}:
            with pytest.raises(AccessError) as caught:worker.run_once()
            terminal=caught.value.code
            assert c.scan()[0]['state']=='pending_upload'
            require(terminal in ({'UNAUTHENTICATED'} if mode=='revoke' else {'STALE_LEASE','CACHE_ACKNOWLEDGEMENT_UNVERIFIED'}),mode=mode,terminal_code=terminal,rpc=client.measurements)
        else:
            reply=worker.run_once();assert c.scan()[0]['state']=='acknowledged'
            expected='BLOCK' if mode in {'cancel','withdraw'} else 'ALLOW'
            assert reply['gate_decision']==expected,json.dumps(dict(mode=mode,expected=expected,actual=reply['gate_decision'],claim_member_count=len(claim['spec']['cases']),cleanup=reply['cleanup_status']))
            assert reply['cleanup_status']=='verified'
        assert not invoked
        metrics=dict(mode=mode,client_attempts=len(client.measurements),server_received=len(calls),max_attempts=limits.max_attempts,terminal_code=terminal,rpc=client.measurements)
        required=2 if mode in {'lost_ack','cancel','withdraw','revoke'} else 1
        require(required<=len(calls)<=len(client.measurements)<=limits.max_attempts,**metrics)
        transient={'RPC_TRANSPORT_UNVERIFIED','OPERATION_FAILED_NO_RESULT_VERIFIED','REMOTE_OPERATION_FAILED','SERVICE_UNAVAILABLE','SERVER_BUSY','UPSTREAM_UNAVAILABLE','GATEWAY_TIMEOUT'}
        require(all(row['code'] in transient for row in client.measurements[:-1]),**metrics)
        require(client.measurements[-1]['code'] in {'HTTP_JSON_COMPLETE',terminal},**metrics)
        require(all(0<row['deadline_seconds']<=limits.request_timeout for row in client.measurements),**metrics)
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
        first.begin(claim);first.complete(claim,report);client=MeasuredClient(url,w['runner_token']);blocked=MeasuredClient(url,w['runner_token'])
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(deliver_pending,first,client,p,j['id'],policy=RecoveryPolicy(total_seconds=15,request_timeout=10))
            try:
                assert entered.wait(10)
                with pytest.raises(CacheError,match='CACHE_BUSY'):deliver_pending(other,blocked,p,j['id'],policy=policy())
            finally:release.set()
            assert future.result(10)['gate_decision']=='ALLOW'
        require(not blocked.measurements,blocked_client_attempts=len(blocked.measurements))
        require(1<=len(calls)<=len(client.measurements)<=3,server_received=len(calls),client_attempts=len(client.measurements),rpc=client.measurements)
        with s.db() as db:assert db.execute('SELECT count(*) FROM artifacts WHERE job=?',(j['id'],)).fetchone()[0]==1

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

def test_withdrawal_uses_claim_member_when_nonmember_is_first(tmp_path,monkeypatch):
    import sys
    original=execute_http_job
    def reordered_fixture(path):
        result=original(path);service,owner,project,_,_,claim,_=result
        member=claim['spec']['cases'][0]['case'];objects=service.objects
        def reordered(token,p,kind,**kw):
            rows=objects(token,p,kind,**kw)
            if kind=='case':
                rows.sort(key=lambda row:row['case']['id']==member['id'])
                assert len(rows)>1 and rows[0]['case']['id']!=member['id'],'Controlled nonmember ordering not established'
            return rows
        service.objects=reordered
        return result
    monkeypatch.setattr(sys.modules[__name__],'execute_http_job',reordered_fixture)
    test_real_report_recovery_preserves_effect_and_gate_contracts(tmp_path,monkeypatch,'withdraw')

@pytest.mark.parametrize('change',['missing','wrong_revision','duplicate'])
def test_claimed_case_selection_rejects_unverified_mapping(tmp_path,monkeypatch,change):
    service,owner,project,_,_,claim,_=execute_http_job(tmp_path)
    selected=claimed_case_object(service,owner,project,claim)
    rows=service.objects(owner,project,'case');member=next(row for row in rows if row['id']==selected)
    if change=='missing':rows=[row for row in rows if row['id']!=selected]
    elif change=='wrong_revision':member['case']['revision']+=1
    else:rows.append(json.loads(json.dumps(member)))
    monkeypatch.setattr(service,'objects',lambda *a,**kw:rows)
    with pytest.raises(AssertionError,match='CLAIM_CASE_BINDING_UNVERIFIED'):claimed_case_object(service,owner,project,claim)

def test_two_unverified_replies_do_not_duplicate_acceptance(tmp_path,monkeypatch):
    import sys
    original=execute_http_job;observed=[]
    def twice_lost(path):
        result=original(path);service=result[0];finish=service.finish;seen=observed
        def second_fault(*a,**kw):
            seen.append(1)
            if len(seen)==2:raise ConnectionResetError()
            return finish(*a,**kw)
        service.finish=second_fault
        return result
    monkeypatch.setattr(sys.modules[__name__],'execute_http_job',twice_lost)
    test_real_report_recovery_preserves_effect_and_gate_contracts(tmp_path,monkeypatch,'lost_ack')
    require(len(observed)==3,server_received=len(observed),injected_unverified_replies=2)
