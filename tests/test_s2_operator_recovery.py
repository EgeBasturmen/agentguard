"""Synthetic runtime declarations test binding, not Docker attestation. PYTEST_DONT_REWRITE."""
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
import pytest
from agentguard.management.service import AccessError
from agentguard.generation.execution import SuiteExecution,CaseExecution
from agentguard.generation.repository import Snapshot
from agentguard.core.result import RunResult
from agentguard.execution.records import RuntimeManifest
from agentguard.management.cache import EvidenceCache
from agentguard.core.manifest import digest
from test_phase7_results import execute_http_job
from test_phase7_workflow import setup_project,approved_suite
from test_phase7_queue import runner

pytestmark=pytest.mark.integration

@pytest.fixture(autouse=True)
def private_fixture(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    secure_fixture_directory(tmp_path)

def declared_report(report):
    raw=json.loads(json.dumps(report['execution']))
    raw.pop('passed');raw.pop('gate_decision')
    for member in raw['cases']:
        member['result']['run_manifest']=dict(run_id='a'*32,container_id='b'*64,started=True,completed=True,
            image_id='sha256:'+'c'*64,source_digest='d'*64,worker_digest='e'*64)
    return dict(execution=SuiteExecution.model_validate_json(json.dumps(raw)).model_dump(mode='json'))

def failed_fixture(path):
    service,owner,project,worker,job,claim,report=execute_http_job(path)
    report=declared_report(report)
    service.finish(worker['runner_token'],job['id'],claim['lease'],dict(failure='RUNNER_EXECUTION_ERROR'))
    return service,owner,project,worker,job,claim,report

def test_legacy_raw_report_cannot_authorize_cleanup(tmp_path):
    s,o,p,w,j,c,r=failed_fixture(tmp_path)
    with pytest.raises(AccessError):s.recover_job(j['id'],r,operator='fixture',reason='legacy raw evidence')

def test_same_suite_other_job_raw_evidence_is_rejected(tmp_path):
    s,o,p,w,j,c,r=failed_fixture(tmp_path)
    other=s.enqueue(o,p,c['spec']['suite_id'],'other-job');claim=s.claim(w['runner_token'])
    assert claim['id']==other['id']
    s.finish(w['runner_token'],other['id'],claim['lease'],dict(failure='RUNNER_EXECUTION_ERROR'))
    with pytest.raises(AccessError):s.recover_job(other['id'],r,operator='fixture',reason='wrong job evidence')

def bound_fixture(path,*,registered=True,terminal='failure',two_cases=False):
    s,o,p=setup_project(path);connection,suite=approved_suite(s,o,p);w=runner(s,o,p)
    if two_cases:
        existing=suite['snapshot']['cases'][0]
        rows=s.objects(o,p,'case')
        first=next(row for row in rows if row['case']['id']==existing['id'] and row['case']['fingerprint']==existing['fingerprint'])
        # Explicit synthetic clone shares immutable source pins. Regenerating a
        # different task under task:main would correctly stale the first member.
        from agentguard.generation.models import Candidate
        raw=json.loads(json.dumps(first['case']));raw['id']='second-'+raw['id'];raw['scenario']['name']=raw['id'];raw['fingerprint']=None
        second=Candidate.model_validate(raw)
        with s.db() as db:
            s.scenario_repository(p).add(second)
            item=s._put(db,p,'case',dict(case=second.model_dump(mode='json'),connection=connection['id']))
        other=s.case(o,p,item['id']);assert other['ready']
        s.review_case(o,p,other['id'],decisions={key:'confirmed' for key in other['oracle_checks']},fixture=True,classification=True)
        s.approve_case(o,p,other['id'],other['case']['fingerprint'])
        suite=s.freeze_suite(o,p,'Two cases',[first['id'],other['id']])
    j=s.enqueue(o,p,suite['id'],'bound-job');c=s.claim(w['runner_token'])
    pins=c['recovery_binding']['value']['cases']
    cases=[]
    for pin in pins:
        m=RuntimeManifest(run_id=pin['run_id'],started=True,completed=True,container_id=os.urandom(32).hex(),
            image_id='sha256:'+'b'*64,worker_digest='c'*64,source_digest='a'*64)
        r=RunResult(scenario_name=pin['id'],passed=False,status='error',cleanup_status='verified',run_manifest=m)
        cases.append(CaseExecution(id=pin['id'],revision=pin['revision'],result=r))
    report=dict(execution=SuiteExecution(snapshot=Snapshot.model_validate(c['spec']['snapshot']),cases=cases).model_dump(mode='json'))
    cache=EvidenceCache(path/'cache',server='http://127.0.0.1:8766',credential=w['runner_token'])
    cache.begin(c);cache.complete(c,report);e=cache.recovery_evidence(p,j['id'])
    if registered:s.register_recovery_evidence(w['runner_token'],j['id'],c['lease'],e)
    if terminal=='failure':s.finish(w['runner_token'],j['id'],c['lease'],dict(failure='RUNNER_EXECUTION_ERROR'))
    else:
        now=s.clock();s.clock=lambda:now+61;s.reconcile()
    return s,o,p,w,j,c,report,e

def recover(f,e=None,**kw):
    s,o,p,w,j,c,r,proof=f
    return s.recover_job(j['id'],proof if e is None else e,token=o,project=p,operator='fixture',reason='isolated recovery',**kw)

def state(s,job):
    with s.db() as db:
        row=dict(db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone())
        receipts=[tuple(r) for r in db.execute('SELECT kind,reference FROM recovery_receipts WHERE job=? ORDER BY id',(job,))]
        usage=[tuple(r) for r in db.execute('SELECT * FROM usage WHERE job=? ORDER BY id',(job,))]
        artifacts=[tuple(r) for r in db.execute('SELECT * FROM artifacts WHERE job=? ORDER BY id',(job,))]
    return row,receipts,usage,artifacts

def test_bound_cleanup_is_not_behavioral_pass_or_usage_release(tmp_path):
    f=bound_fixture(tmp_path);s,o,p,w,j,c,r,e=f;before=state(s,j['id'])
    result=recover(f);after=state(s,j['id'])
    assert result['cleanup_status']=='verified' and result['gate_decision']=='BLOCK' and not result['authorizes_release']
    assert not result['independent_attestation'] and result['unknown_usage_reservations_retained']
    assert after[0]['state']=='error' and after[0]['cleanup']=='verified'
    assert after[0]['result_hash']==before[0]['result_hash']
    for k in ('reserved_seconds','reserved_bytes','reserved_model_tokens','reserved_model_cost'):assert after[0][k]==before[0][k]
    assert after[2:]==before[2:] and after[0]['seal']==s._seal(after[0])
    receipt=s.finish(w['runner_token'],j['id'],c['lease'],dict(failure='RUNNER_EXECUTION_ERROR'))
    assert receipt['delivery_accepted'] and receipt['gate_decision']=='BLOCK' and receipt['cleanup_status']=='verified'

@pytest.mark.parametrize('field',['job','project','execution','lease','lease_hash','spec_hash','owner','checksum','ticket','report','container','run_id','worker','image','report_hash'])
def test_mutated_or_cross_bound_evidence_cannot_recover(tmp_path,field):
    f=bound_fixture(tmp_path);s,_,_,_,j,_,_,e=f;e=json.loads(json.dumps(e));p=e['payload']
    if field=='checksum':e['checksum']='0'*64
    elif field=='ticket':p['recovery_binding']['value']['runner']='0'*32
    elif field=='report':p['report']['execution']['coverage_gaps'].append('changed')
    elif field in {'container','run_id','worker','image'}:
        m=p['report']['execution']['cases'][0]['result']['run_manifest']
        key={'container':'container_id','run_id':'run_id','worker':'worker_digest','image':'image_id'}[field]
        m[key]=('sha256:' if field=='image' else '')+'0'*(32 if field=='run_id' else 64)
    elif field=='lease':p[field]='different synthetic lease'
    else:p[field]='0'*(32 if field in {'job','project','execution'} else 64)
    if field in {'report','container','run_id','worker','image'}:p['report_hash']=digest(p['report'])
    if field!='checksum':e['checksum']=digest(p)
    before=state(s,j['id'])
    with pytest.raises((AccessError,ValueError)):recover(f,e)
    assert state(s,j['id'])==before

@pytest.mark.parametrize('kind',['viewer','revoked','other_project','other_tenant','no_token'])
def test_recovery_requires_current_project_admin(tmp_path,kind):
    f=bound_fixture(tmp_path);s,o,p,w,j,c,r,e=f;token=o;project=p
    if kind=='viewer':
        s.add_member(o,p,'reader','viewer');token=s.issue_key(o,p,'reader',['project:read'])['token']
    elif kind=='revoked':
        key=s.issue_key(o,p,'owner',['project:read','project:admin']);token=key['token'];s.revoke_key(o,p,key['key_id'])
    elif kind=='other_project':project=s.create_project(o,'Other')['id']
    elif kind=='other_tenant':token=s.create_workspace(o,'other-operator','Other tenant')['token']
    else:token=None
    before=state(s,j['id'])
    with pytest.raises(AccessError):s.recover_job(j['id'],e,token=token,project=project,operator='spoofed name',reason='isolated')
    assert state(s,j['id'])==before

@pytest.mark.parametrize('kind',['job_seal','intent_mac','registration_mac','registration_missing'])
def test_missing_or_untrusted_server_binding_is_rejected(tmp_path,kind):
    f=bound_fixture(tmp_path);s,_,_,_,j,_,_,_=f
    with s.db() as db:
        if kind=='job_seal':db.execute('UPDATE jobs SET seal=? WHERE id=?',('bad',j['id']))
        elif kind=='registration_missing':db.execute("DELETE FROM recovery_receipts WHERE job=? AND kind='execution_evidence_v1'",(j['id'],))
        else:
            name='execution_intent_v1' if kind=='intent_mac' else 'execution_evidence_v1'
            value=json.loads(db.execute('SELECT reference FROM recovery_receipts WHERE job=? AND kind=?',(j['id'],name)).fetchone()[0]);value['mac']='0'*64
            db.execute('UPDATE recovery_receipts SET reference=? WHERE job=? AND kind=?',(json.dumps(value),j['id'],name))
    before=state(s,j['id'])
    with pytest.raises(AccessError):recover(f)
    assert state(s,j['id'])==before

def test_duplicate_and_concurrent_recovery_have_one_receipt_and_audit(tmp_path):
    f=bound_fixture(tmp_path);s,_,_,_,j,_,_,_=f
    from agentguard.management.service import ControlPlane
    other=(ControlPlane(s.root),*f[1:])
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(recover,[f,other]))
    assert results[0]==results[1]==recover(f)
    with s.db() as db:
        assert db.execute("SELECT count(*) FROM recovery_receipts WHERE job=? AND kind='cache_cleanup'",(j['id'],)).fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM audit WHERE action='recovery.cleanup'").fetchone()[0]==1

def test_transaction_rollback_preserves_original_state(tmp_path,monkeypatch):
    f=bound_fixture(tmp_path);s,_,_,_,j,_,_,_=f;before=state(s,j['id']);original=s.event
    def event(*a,**kw):
        if a[4]=='recovery.cleanup':raise RuntimeError('synthetic rollback')
        return original(*a,**kw)
    monkeypatch.setattr(s,'event',event)
    with pytest.raises(RuntimeError):recover(f)
    assert state(s,j['id'])==before

def crash_recovery(root,owner,project,job,evidence):
    from agentguard.management.service import ControlPlane
    s=ControlPlane(root)
    def event(*a,**kw):os._exit(63)
    s.event=event
    s.recover_job(job,evidence,token=owner,project=project,operator='fixture',reason='crash before commit')

def test_real_process_crash_rolls_back_cleanup_receipt(tmp_path):
    f=bound_fixture(tmp_path);s,o,p,_,j,_,_,e=f;before=state(s,j['id'])
    child=multiprocessing.get_context('spawn').Process(target=crash_recovery,args=(s.root,o,p,j['id'],e));child.start();child.join(20)
    assert not child.is_alive() and child.exitcode==63
    assert state(s,j['id'])==before
    assert recover(f)['gate_decision']=='BLOCK'

def test_recovery_receipt_storage_is_accounted(tmp_path):
    from agentguard.management.limits import logical_storage
    s,o,p=setup_project(tmp_path);_,suite=approved_suite(s,o,p);w=runner(s,o,p)
    s.enqueue(o,p,suite['id'],'storage')
    with s.db() as db:before=logical_storage(db,p)
    s.claim(w['runner_token'])
    with s.db() as db:
        assert logical_storage(db,p)>before

@pytest.mark.parametrize('kind',['other_runner','revoked_runner','wrong_lease','wrong_run','other_job'])
def test_registration_is_current_runner_and_issued_execution_bound(tmp_path,kind):
    f=bound_fixture(tmp_path,registered=False);s,o,p,w,j,c,r,e=f;token=w['runner_token'];lease=c['lease'];job=j['id']
    if kind=='other_runner':token=runner(s,o,p)['runner_token']
    elif kind=='revoked_runner':s.revoke_runner(o,p,w['id'])
    elif kind=='wrong_lease':lease='another synthetic lease'
    elif kind=='other_job':job=s.enqueue(o,p,c['spec']['suite_id'],'different-job')['id']
    else:
        e=json.loads(json.dumps(e));e['payload']['report']['execution']['cases'][0]['result']['run_manifest']['run_id']='f'*32
        e['payload']['report_hash']=digest(e['payload']['report']);e['checksum']=digest(e['payload'])
    before=state(s,j['id'])
    with pytest.raises(AccessError):s.register_recovery_evidence(token,job,lease,e)
    assert state(s,j['id'])==before

def test_same_suite_report_cannot_be_rebound_to_new_execution(tmp_path):
    f=bound_fixture(tmp_path);s,o,p,w,j,c,r,e=f
    other=s.enqueue(o,p,c['spec']['suite_id'],'other-execution');new=s.claim(w['runner_token'])
    assert new['id']==other['id']
    cache=EvidenceCache(tmp_path/'other-cache',server='http://127.0.0.1:8766',credential=w['runner_token']);cache.begin(new);cache.complete(new,r)
    proof=cache.recovery_evidence(p,new['id'])
    with pytest.raises(AccessError,match='RECOVERY_RUNTIME_IDENTITY_MISMATCH'):s.register_recovery_evidence(w['runner_token'],new['id'],new['lease'],proof)
    # Even copying only the previous container identity into a new issued run
    # cannot register a second ownership claim for that resource.
    r=json.loads(json.dumps(r));r['execution']['cases'][0]['result']['run_manifest']['run_id']=new['recovery_binding']['value']['cases'][0]['run_id']
    proof['payload']['report']=r;proof['payload']['report_hash']=digest(r);proof['checksum']=digest(proof['payload'])
    with pytest.raises(AccessError,match='RECOVERY_RUNTIME_REPLAY'):s.register_recovery_evidence(w['runner_token'],new['id'],new['lease'],proof)

def test_registration_duplicate_is_idempotent_but_replacement_is_denied(tmp_path):
    f=bound_fixture(tmp_path);s,_,_,w,j,c,_,e=f;before=state(s,j['id'])
    a=s.register_recovery_evidence(w['runner_token'],j['id'],c['lease'],e)
    b=s.register_recovery_evidence(w['runner_token'],j['id'],c['lease'],e)
    assert a==b and state(s,j['id'])==before
    e=json.loads(json.dumps(e));e['payload']['owner']='f'*64;e['checksum']=digest(e['payload'])
    with pytest.raises(AccessError,match='RECOVERY_EVIDENCE_CONFLICT'):s.register_recovery_evidence(w['runner_token'],j['id'],c['lease'],e)
    assert state(s,j['id'])==before

def test_unknown_model_reservations_survive_verified_cleanup(tmp_path):
    f=bound_fixture(tmp_path);s,_,_,_,j,_,_,_=f
    with s.db() as db:db.execute('UPDATE jobs SET reserved_model_tokens=123,reserved_model_cost=456 WHERE id=?',(j['id'],))
    before=state(s,j['id']);recover(f);after=state(s,j['id'])
    assert after[0]['reserved_model_tokens']==123 and after[0]['reserved_model_cost']==456
    assert after[2:]==before[2:]

def test_revoked_runner_cannot_register_but_admin_can_use_prior_registered_proof(tmp_path):
    f=bound_fixture(tmp_path);s,o,p,w,j,c,_,e=f;s.revoke_runner(o,p,w['id'])
    with pytest.raises(AccessError,match='UNAUTHENTICATED'):s.register_recovery_evidence(w['runner_token'],j['id'],c['lease'],e)
    assert recover(f)['gate_decision']=='BLOCK'

def test_retention_missing_artifact_is_not_recreated_by_cleanup(tmp_path):
    f=bound_fixture(tmp_path);s,_,_,_,j,_,_,_=f
    now=s.clock();s.clock=lambda:now+31*86400
    # Existing credentials would expire here; inspect recovery with a separately
    # issued current local operator key through the explicit existing recovery.
    with s.db() as db:actor=db.execute("SELECT workspace,user FROM workspace_members WHERE role='owner'").fetchone()
    key=s.operator_recover_owner(actor[0],actor[1],operator='fixture',reason='synthetic expired credential')
    result=s.recover_job(j['id'],f[-1],token=key['token'],project=f[2],operator='fixture',reason='historical cleanup only')
    assert result['gate_decision']=='BLOCK' and state(s,j['id'])[3]==[]

def test_real_http_registration_and_operator_authorization(tmp_path):
    from agentguard.management.http import running_server
    from agentguard.management.client import Client
    f=bound_fixture(tmp_path,registered=False);s,o,p,w,j,c,_,e=f
    with running_server(s) as url:
        operator=Client(url,o);worker=Client(url,w['runner_token'])
        reply=worker.request('/api/runner/recovery-evidence',dict(job_id=j['id'],lease=c['lease'],evidence=e))
        assert reply['recovery_evidence_registered']
        body=dict(report=e,operator='fixture',reason='HTTP cleanup')
        result=operator.request('/api/projects/'+p+'/runs/'+j['id']+'/recover',body)
        assert result['gate_decision']=='BLOCK' and result['cleanup_status']=='verified'
        with pytest.raises(AccessError):worker.request('/api/projects/'+p+'/runs/'+j['id']+'/recover',body)

def test_cli_reads_private_envelope_without_echoing_secrets(tmp_path):
    from typer.testing import CliRunner
    from agentguard.cli.main import app
    from agentguard.management.credentials import private_file
    f=bound_fixture(tmp_path);s,o,p,w,j,c,_,e=f;credential=tmp_path/'owner.key'
    private_file(credential,lambda:dict(token=o))
    path=tmp_path/'cache'/p/(j['id']+'.entry.json')
    result=CliRunner().invoke(app,['management','recover-job','--state',str(s.root),'--project',p,'--job',j['id'],'--report',str(path),'--credential-file',str(credential),'--reason','synthetic cleanup'])
    assert result.exit_code==0
    for secret in (o,w['runner_token'],c['lease'],e['payload']['lease_hash']):assert secret not in result.output
    assert json.loads(result.output)['gate_decision']=='BLOCK'

@pytest.mark.parametrize('kind',['truncated','checksum','unsafe_mode'])
def test_private_evidence_file_rejects_corruption_and_unsafe_permissions(tmp_path,kind):
    from agentguard.management.operator_recovery import read_private_evidence,envelope as checked_envelope
    from agentguard.management.credentials import private_file
    path=tmp_path/'evidence.json';private_file(path,lambda:dict(token='ag7_'+'s'*43))
    path.write_bytes(b'{"payload":{},"checksum":"bad"}')
    if kind=='unsafe_mode':
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes as W
            adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            p=ctypes.c_void_p()
            adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[W.LPCWSTR,W.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
            adv.SetFileSecurityW.argtypes=[W.LPCWSTR,W.DWORD,ctypes.c_void_p]
            kernel.LocalFree.argtypes=[ctypes.c_void_p]
            assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;;FR;;;WD)',1,ctypes.byref(p),None)
            try:assert adv.SetFileSecurityW(str(path),0x80000004,p)
            finally:kernel.LocalFree(p)
        else:os.chmod(path,0o644)
        with pytest.raises(AccessError):read_private_evidence(path)
    elif kind=='truncated':
        path.write_bytes(b'{')
        with pytest.raises(AccessError,match='RECOVERY_PRIVATE_FILE_UNVERIFIED'):read_private_evidence(path)
    else:
        with pytest.raises(AccessError):checked_envelope(read_private_evidence(path))

def test_duplicate_case_cannot_replace_missing_cleanup_member(tmp_path):
    f=bound_fixture(tmp_path,registered=False,two_cases=True);s,_,_,w,j,c,_,e=f
    members=e['payload']['report']['execution']['cases'];first=members[0];second=members[1]
    second['id']=first['id'];second['revision']=first['revision']
    second['result']['run_manifest']['run_id']=first['result']['run_manifest']['run_id']
    e['payload']['report_hash']=digest(e['payload']['report']);e['checksum']=digest(e['payload'])
    with pytest.raises(AccessError):s.register_recovery_evidence(w['runner_token'],j['id'],c['lease'],e)

def test_lost_execution_cleanup_is_sealed_without_fabricating_delivery(tmp_path):
    f=bound_fixture(tmp_path,terminal='lost');s,_,_,w,j,c,_,_=f
    assert state(s,j['id'])[0]['result_hash'] is None
    result=recover(f);row=state(s,j['id'])[0]
    assert result['gate_decision']=='BLOCK' and row['seal']==s._seal(row) and row['result_hash'] is None
    with pytest.raises(AccessError,match='STALE_LEASE'):s.finish(w['runner_token'],j['id'],c['lease'],dict(failure='RUNNER_EXECUTION_ERROR'))

def test_recovery_rechecks_authorization_after_evidence_validation(tmp_path,monkeypatch):
    f=bound_fixture(tmp_path);s,o,p,_,j,_,_,e=f;now=s.clock();s.clock=lambda:now
    key=s.issue_key(o,p,'owner',['project:read','project:admin'],ttl_seconds=60)
    original=s._evaluate_report
    def delayed(*a,**kw):
        result=original(*a,**kw);s.clock=lambda:now+61;return result
    monkeypatch.setattr(s,'_evaluate_report',delayed);before=state(s,j['id'])
    with pytest.raises(AccessError):s.recover_job(j['id'],e,token=key['token'],project=p,operator='fixture',reason='expiry during validation')
    assert state(s,j['id'])==before

def test_corrupt_error_cleanup_seal_is_not_presented_as_verified(tmp_path):
    f=bound_fixture(tmp_path);s,o,p,_,j,_,_,_=f;recover(f)
    with s.db() as db:db.execute('UPDATE jobs SET seal=? WHERE id=?',('corrupt',j['id']))
    view=s.job(o,p,j['id'])
    assert view['gate_decision']=='BLOCK' and view['cleanup_status']=='unverified'

def test_claim_intent_and_lease_rollback_together(tmp_path,monkeypatch):
    import agentguard.management.operator_recovery as module
    s,o,p=setup_project(tmp_path);_,suite=approved_suite(s,o,p);w=runner(s,o,p);j=s.enqueue(o,p,suite['id'],'atomic-intent')
    before=state(s,j['id']);original=module.insert
    def fault(*a,**kw):
        original(*a,**kw)
        if a[3]==module.INTENT:raise RuntimeError('synthetic claim rollback')
    monkeypatch.setattr(module,'insert',fault)
    with pytest.raises(RuntimeError):s.claim(w['runner_token'])
    assert state(s,j['id'])==before
    with s.db() as db:assert not db.execute('SELECT 1 FROM events WHERE job=? AND kind=?',(j['id'],'leased')).fetchone()

def test_concurrent_resource_registration_has_one_owner(tmp_path):
    from agentguard.management.service import ControlPlane
    import threading
    f=bound_fixture(tmp_path,registered=False);s,o,p,w,j,c,r,e=f
    second=s.enqueue(o,p,c['spec']['suite_id'],'resource-race');second_runner=runner(s,o,p);claim=s.claim(second_runner['runner_token']);assert claim['id']==second['id']
    report=json.loads(json.dumps(r));report['execution']['cases'][0]['result']['run_manifest']['run_id']=claim['recovery_binding']['value']['cases'][0]['run_id']
    cache=EvidenceCache(tmp_path/'second-cache',server='http://127.0.0.1:8766',credential=second_runner['runner_token']);cache.begin(claim);cache.complete(claim,report)
    proof=cache.recovery_evidence(p,claim['id']);barrier=threading.Barrier(2);other=ControlPlane(s.root)
    def attempt(args):
        service,token,job,lease,evidence=args;barrier.wait(10)
        try:
            service.register_recovery_evidence(token,job,lease,evidence);return 'accepted'
        except AccessError as error:return error.code
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(attempt,[(s,w['runner_token'],j['id'],c['lease'],e),(other,second_runner['runner_token'],claim['id'],claim['lease'],proof)]))
    assert sorted(results)==['RECOVERY_RUNTIME_REPLAY','accepted']
    with s.db() as db:assert db.execute("SELECT count(*) FROM recovery_receipts WHERE kind='execution_evidence_v1'").fetchone()[0]==1

@pytest.mark.parametrize('kind',['receipt_without_cleanup','cleanup_without_receipt','intent_project','registration_project','cleanup_project'])
def test_receipt_and_job_state_contradictions_fail_closed(tmp_path,kind):
    import agentguard.management.operator_recovery as module
    f=bound_fixture(tmp_path);s,_,p,_,j,_,_,_=f
    if kind in {'cleanup_without_receipt','cleanup_project'}:recover(f)
    with s.db() as db:
        if kind=='receipt_without_cleanup':
            raw=json.loads(db.execute('SELECT reference FROM recovery_receipts WHERE job=? AND kind=?',(j['id'],module.EVIDENCE)).fetchone()[0])['value']
            reference=digest(dict(binding=raw['binding'],report_hash=raw['report_hash'],runtime=raw['runtime']))
            row=db.execute('SELECT * FROM jobs WHERE id=?',(j['id'],)).fetchone();module.insert(s,db,row,module.CLEANUP,reference)
        elif kind=='cleanup_without_receipt':db.execute('DELETE FROM recovery_receipts WHERE job=? AND kind=?',(j['id'],module.CLEANUP))
        else:
            name={'intent_project':module.INTENT,'registration_project':module.EVIDENCE,'cleanup_project':module.CLEANUP}[kind]
            db.execute('UPDATE recovery_receipts SET project=? WHERE job=? AND kind=?',('0'*32,j['id'],name))
    before=state(s,j['id'])
    with pytest.raises(AccessError):recover(f)
    assert state(s,j['id'])==before
