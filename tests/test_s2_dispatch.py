"""Real SQLite/HTTP admission fences; synthetic state only.

PYTEST_DONT_REWRITE: assertion introspection must not echo ephemeral lease/key values.
"""
import json
import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from agentguard.management.service import ControlPlane,AccessError
from test_phase7_workflow import setup_project,approved_suite
from test_phase7_queue import runner

pytestmark=pytest.mark.integration

def fixture(tmp_path):
    service,owner,project=setup_project(tmp_path);connection,suite=approved_suite(service,owner,project)
    worker=runner(service,owner,project);job=service.enqueue(owner,project,suite['id'],'fenced')
    case=next(item for item in service.objects(owner,project,'case') if item['status']=='approved')
    return service,owner,project,connection,suite,worker,job,case

def invalidate(service,owner,project,connection,suite,case,kind):
    if kind=='retire':service.retire_case(owner,project,case['id'])
    elif kind=='revision':service.revise_case(owner,project,case['id'],case['case']['scenario'],'Synthetic newer revision')
    else:
        with service.db() as db:
            if kind=='connection':
                payload=json.loads(db.execute('SELECT payload FROM objects WHERE id=?',(connection['id'],)).fetchone()[0]);payload['artifact_digest']='b'*64
                db.execute('UPDATE objects SET payload=? WHERE id=?',(json.dumps(payload),connection['id']))
            elif kind=='suite':db.execute('DELETE FROM objects WHERE id=?',(suite['id'],))
            elif kind=='source':
                repo=service.scenario_repository(project)
                db.execute('UPDATE '+repo._table('sources')+' SET digest=?',('b'*64,))

@pytest.mark.parametrize('kind',['retire','revision','connection','suite','source'])
def test_stale_admission_is_terminal_before_claim(tmp_path,kind):
    s,o,p,c,suite,w,j,case=fixture(tmp_path);invalidate(s,o,p,c,suite,case,kind)
    assert s.claim(w['runner_token']) is None
    view=s.job(o,p,j['id'])
    assert view['state']=='error' and view['gate_decision']=='BLOCK' and view['cleanup_status']=='not_required'
    with s.db() as db:
        row=db.execute('SELECT lease,started,reserved_seconds FROM jobs WHERE id=?',(j['id'],)).fetchone()
        assert tuple(row)==(None,None,0)

@pytest.mark.parametrize('kind',['retire','revision','connection','suite','source','cancel'])
def test_first_heartbeat_refuses_changed_admission(tmp_path,kind):
    s,o,p,c,suite,w,j,case=fixture(tmp_path);claim=s.claim(w['runner_token'])
    if kind=='cancel':s.cancel(o,p,j['id'])
    else:invalidate(s,o,p,c,suite,case,kind)
    reply=s.heartbeat(w['runner_token'],j['id'],claim['lease'])
    assert reply['cancel'] is True and reply['lease_seconds']==0
    assert s.job(o,p,j['id'])['gate_decision']=='BLOCK'

@pytest.mark.parametrize('boundary',['lease','deadline'])
def test_expired_boundary_is_committed_and_never_renewed(tmp_path,boundary):
    s,o,p,c,suite,w,j,case=fixture(tmp_path);claim=s.claim(w['runner_token'])
    with s.db() as db:
        row=db.execute('SELECT started,lease_until,reserved_seconds FROM jobs WHERE id=?',(j['id'],)).fetchone()
    s.clock=lambda:row['lease_until'] if boundary=='lease' else row['started']+row['reserved_seconds']
    reply=s.heartbeat(w['runner_token'],j['id'],claim['lease'])
    assert reply['cancel'] and reply['lease_seconds']==0
    with s.db() as db:assert db.execute('SELECT state FROM jobs WHERE id=?',(j['id'],)).fetchone()[0]=='error'

@pytest.mark.parametrize('kind',['cancel','retire','revoke','connection','lease_expired','deadline_expired'])
def test_real_http_runner_waits_for_start_fence_zero_invocations(tmp_path,monkeypatch,kind):
    from agentguard.management.http import running_server
    from agentguard.management.client import Client
    from agentguard.management.runner import PrivateRunner
    import agentguard.management.runner as module
    s,o,p,c,suite,w,j,case=fixture(tmp_path);original=s.claim
    def claim(token):
        value=original(token)
        if kind=='cancel':s.cancel(o,p,j['id'])
        elif kind=='revoke':s.revoke_runner(o,p,w['id'])
        elif kind in ('lease_expired','deadline_expired'):
            with s.db() as db:row=db.execute('SELECT * FROM jobs WHERE id=?',(j['id'],)).fetchone()
            expiry=row['lease_until'] if kind=='lease_expired' else row['started']+row['reserved_seconds']
            s.clock=lambda:expiry
        else:invalidate(s,o,p,c,suite,case,kind)
        return value
    s.claim=claim;invocations=[]
    def execute(*args,**kwargs):invocations.append(1);raise RuntimeError('Execution must not start')
    monkeypatch.setattr(module,'execute_spec',execute)
    with running_server(s) as url:
        result=PrivateRunner(Client(url,w['runner_token']),source_root=tmp_path,cache_root=tmp_path/'cache').run_once()
    assert invocations==[] and result['gate_decision']=='BLOCK'

def test_two_independent_controllers_claim_one_lease(tmp_path):
    s,o,p,c,suite,w,j,case=fixture(tmp_path);second=runner(s,o,p)
    a=ControlPlane(s.root);b=ControlPlane(s.root);barrier=threading.Barrier(2)
    def claim(service,token):barrier.wait();return service.claim(token)
    with ThreadPoolExecutor(2) as pool:
        results=[f.result(15) for f in [pool.submit(claim,a,w['runner_token']),pool.submit(claim,b,second['runner_token'])]]
    assert sum(r is not None for r in results)==1

def test_withdrawal_transaction_serializes_with_claim(tmp_path):
    s,o,p,c,suite,w,j,case=fixture(tmp_path);other=ControlPlane(s.root);entered=threading.Event();attempted=threading.Event();release=threading.Event()
    def withdraw():
        with s.db() as db:
            payload=json.loads(s._object(db,p,case['id'],'case')['payload'])
            s.scenario_repository(p).retire(payload['case']['id'],payload['case']['revision'])
            entered.set();assert release.wait(10)
    def claim():attempted.set();return other.claim(w['runner_token'])
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(withdraw);assert entered.wait(10);second=pool.submit(claim);assert attempted.wait(10)
        assert not second.done();release.set();first.result(10);assert second.result(10) is None

@pytest.mark.parametrize('change',['cancel','retire','connection'])
def test_running_fence_blocks_pass_but_accepts_verified_cleanup(tmp_path,change):
    from test_phase7_results import execute_http_job
    s,o,p,w,j,claim,report=execute_http_job(tmp_path)
    assert not s.heartbeat(w['runner_token'],j['id'],claim['lease'])['cancel']
    if change=='cancel':s.cancel(o,p,j['id'])
    else:
        suite=s.objects(o,p,'suite')[0];case=next(c for c in s.objects(o,p,'case') if c['status']=='approved')
        connection=s.objects(o,p,'connection')[0]
        invalidate(s,o,p,connection,suite,case,change)
    assert s.heartbeat(w['runner_token'],j['id'],claim['lease'])['cancel']
    done=s.finish(w['runner_token'],j['id'],claim['lease'],report)
    assert done['gate_decision']=='BLOCK' and done['cleanup_status']=='verified'

def test_finish_rechecks_exact_binding_without_an_intervening_heartbeat(tmp_path):
    from test_phase7_results import execute_http_job
    s,o,p,w,j,claim,report=execute_http_job(tmp_path)
    connection=s.objects(o,p,'connection')[0]
    with s.db() as db:
        payload=json.loads(db.execute('SELECT payload FROM objects WHERE id=?',(connection['id'],)).fetchone()[0])
        payload['artifact_digest']='b'*64;db.execute('UPDATE objects SET payload=? WHERE id=?',(json.dumps(payload),connection['id']))
    assert s.finish(w['runner_token'],j['id'],claim['lease'],report)['gate_decision']=='BLOCK'

def test_wrong_runner_and_lease_do_not_consume_valid_lease(tmp_path):
    s,o,p,c,suite,w,j,case=fixture(tmp_path);other=runner(s,o,p);claim=s.claim(w['runner_token'])
    for token,lease in [(other['runner_token'],claim['lease']),(w['runner_token'],'wrong')]:
        with pytest.raises(AccessError):s.heartbeat(token,j['id'],lease)
        with pytest.raises(AccessError):s.finish(token,j['id'],lease,{'failure':'RUNNER_EXECUTION_ERROR'})
    assert s.heartbeat(w['runner_token'],j['id'],claim['lease'])['cancel'] is False

def test_claim_transaction_rolls_back_lease_and_event_together(tmp_path,monkeypatch):
    s,o,p,c,suite,w,j,case=fixture(tmp_path)
    def crash(*args):raise RuntimeError('Synthetic transaction interruption')
    monkeypatch.setattr(s,'_job_event',crash)
    with pytest.raises(RuntimeError):s.claim(w['runner_token'])
    with s.db() as db:
        row=db.execute('SELECT state,lease,started,runner FROM jobs WHERE id=?',(j['id'],)).fetchone()
        assert tuple(row)==('queued',None,None,None)
        assert db.execute("SELECT count(*) FROM events WHERE job=? AND kind='leased'",(j['id'],)).fetchone()[0]==0
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'

def test_claim_wins_race_withdrawal_then_start_ack_refuses(tmp_path,monkeypatch):
    s,o,p,c,suite,w,j,case=fixture(tmp_path);other=ControlPlane(s.root)
    checked=threading.Event();release=threading.Event();attempted=threading.Event();original=s._admission_error
    def admission(db,job):
        reason=original(db,job);checked.set();assert release.wait(10);return reason
    monkeypatch.setattr(s,'_admission_error',admission)
    def withdraw():attempted.set();other.retire_case(o,p,case['id'])
    with ThreadPoolExecutor(2) as pool:
        claim=pool.submit(s.claim,w['runner_token']);assert checked.wait(10)
        retired=pool.submit(withdraw);assert attempted.wait(10);assert not retired.done()
        release.set();value=claim.result(10);retired.result(10)
    assert value is not None
    assert other.heartbeat(w['runner_token'],j['id'],value['lease'])['cancel'] is True

def test_enqueue_creator_role_withdrawal_is_rechecked(tmp_path):
    s,o,p,c,suite,w,j,case=fixture(tmp_path);s.cancel(o,p,j['id'])
    s.add_member(o,p,'synthetic-developer','developer')
    key=s.issue_key(o,p,'synthetic-developer',['run:write'])['token']
    j=s.enqueue(key,p,suite['id'],'developer-job')
    s.add_member(o,p,'synthetic-developer','viewer')
    assert s.claim(w['runner_token']) is None
    assert 'DISPATCH_CREATOR_AUTHORITY_REVOKED' in s.job(o,p,j['id'])['decision']['reasons']

def test_revoked_runner_cannot_finish_or_heartbeat(tmp_path):
    from test_phase7_results import execute_http_job
    s,o,p,w,j,claim,report=execute_http_job(tmp_path);s.revoke_runner(o,p,w['id'])
    with pytest.raises(AccessError):s.heartbeat(w['runner_token'],j['id'],claim['lease'])
    with pytest.raises(AccessError):s.finish(w['runner_token'],j['id'],claim['lease'],report)
    assert s.job(o,p,j['id'])['gate_decision']=='BLOCK'

@pytest.mark.parametrize('boundary',['lease','deadline'])
def test_time_expiring_during_admission_cannot_ack_start(tmp_path,monkeypatch,boundary):
    s,o,p,c,suite,w,j,case=fixture(tmp_path);claim=s.claim(w['runner_token'])
    with s.db() as db:row=db.execute('SELECT * FROM jobs WHERE id=?',(j['id'],)).fetchone()
    original=s._admission_error
    def check(db,job):
        reason=original(db,job)
        expired=row['lease_until'] if boundary=='lease' else row['started']+row['reserved_seconds']
        s.clock=lambda:expired
        return reason
    monkeypatch.setattr(s,'_admission_error',check)
    assert s.heartbeat(w['runner_token'],j['id'],claim['lease'])['cancel'] is True

def test_issuer_expiring_during_claim_cannot_receive_spec(tmp_path,monkeypatch):
    s,o,p,c,suite,w,j,case=fixture(tmp_path)
    with s.db() as db:expiry=db.execute('SELECT expires FROM keys WHERE user=?',('owner',)).fetchone()[0]
    original=s._admission_error
    def check(db,job):
        reason=original(db,job);s.clock=lambda:expiry;return reason
    monkeypatch.setattr(s,'_admission_error',check)
    with pytest.raises(AccessError):s.claim(w['runner_token'])

def test_current_source_revision_invalidates_queued_approval(tmp_path):
    s,o,p,c,suite,w,j,case=fixture(tmp_path)
    s.generate_cases(o,p,c['id'],task='Independent changed task',fixture={'files':{'note.txt':'draft'}},
        rules=[dict(id='goal',text='expect files.note\\.txt = "done"')])
    assert s.claim(w['runner_token']) is None

@pytest.mark.parametrize('boundary',['lease','deadline'])
def test_expired_finish_commits_block_without_artifact(tmp_path,boundary):
    from test_phase7_results import execute_http_job
    s,o,p,w,j,claim,report=execute_http_job(tmp_path)
    with s.db() as db:row=db.execute('SELECT * FROM jobs WHERE id=?',(j['id'],)).fetchone()
    s.clock=lambda:row['lease_until'] if boundary=='lease' else row['started']+row['reserved_seconds']
    result=s.finish(w['runner_token'],j['id'],claim['lease'],report)
    assert result['state']=='error' and result['gate_decision']=='BLOCK'
    with s.db() as db:assert db.execute('SELECT count(*) FROM artifacts WHERE job=?',(j['id'],)).fetchone()[0]==0

def _process_claim(root,token,barrier,output):
    try:
        service=ControlPlane(root);barrier.wait(20)
        output.put(('ok',service.claim(token) is not None))
    except Exception as error:output.put(('error',type(error).__name__))

def test_independent_processes_cannot_dispatch_same_job_twice(tmp_path):
    s,o,p,c,suite,w,j,case=fixture(tmp_path);second=runner(s,o,p)
    context=multiprocessing.get_context('spawn');barrier=context.Barrier(2);output=context.Queue()
    processes=[context.Process(target=_process_claim,args=(s.root,token,barrier,output)) for token in (w['runner_token'],second['runner_token'])]
    for process in processes:process.start()
    for process in processes:
        process.join(40);assert not process.is_alive() and process.exitcode==0
    results=[output.get(timeout=5) for _ in processes]
    assert all(status=='ok' for status,_ in results) and sum(value for _,value in results)==1
