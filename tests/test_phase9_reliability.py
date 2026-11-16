import multiprocessing
import os
import sqlite3
import pytest
from test_phase7_workflow import setup_project,approved_suite

pytestmark=pytest.mark.integration

def _claim_and_crash(root,runner):
    from agentguard.management.service import ControlPlane
    ControlPlane(root,clock=lambda:1000).claim(runner)
    os._exit(17)

def _transaction_crash(root):
    from agentguard.management.service import ControlPlane
    service=ControlPlane(root)
    with service.db() as db:
        db.execute("UPDATE projects SET plan='pilot'")
        os._exit(18)

def test_process_crash_rolls_back_and_releases_state_lock(tmp_path):
    from agentguard.management.service import ControlPlane
    service,token,project=setup_project(tmp_path)
    process=multiprocessing.get_context('spawn').Process(target=_transaction_crash,args=(service.root,));process.start();process.join(timeout=15)
    assert not process.is_alive() and process.exitcode==18
    reopened=ControlPlane(service.root);assert reopened.project(token,project)['plan']=='developer'
    with reopened.db() as db:assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'

def test_claimed_job_is_not_redispatched_after_actual_process_death(tmp_path):
    from agentguard.management.service import ControlPlane
    from test_phase7_queue import runner
    service=ControlPlane(tmp_path/'server',clock=lambda:1000);owner=service.bootstrap('fixture-owner');token=owner['token'];project=service.create_project(token,'Fixture')['id']
    _,suite=approved_suite(service,token,project);worker=runner(service,token,project)
    job=service.enqueue(token,project,suite['id'],'crash')
    process=multiprocessing.get_context('spawn').Process(target=_claim_and_crash,args=(service.root,worker['runner_token']));process.start();process.join(timeout=15)
    assert not process.is_alive() and process.exitcode==17
    reopened=ControlPlane(service.root,clock=lambda:1031)
    assert reopened.claim(worker['runner_token']) is None
    result=reopened.job(token,project,job['id'])
    assert result['gate_decision']=='BLOCK' and result['cleanup_status']=='unverified'
    with reopened.db() as db:assert db.execute('SELECT reserved_seconds FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]>0

def test_bounded_soak_restart_quota_and_retention(tmp_path):
    from agentguard.management.service import ControlPlane
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    for index in range(40):
        if index%5==0:service=ControlPlane(service.root)
        job=service.enqueue(token,project,suite['id'],'soak-'+str(index))
        if index%5==0:assert service.enqueue(token,project,suite['id'],'soak-'+str(index))['id']==job['id']
        service.cancel(token,project,job['id'])
    usage=service.usage(token,project)
    assert usage['admitted_runs']==40
    assert service.operations_health(token,project)['status']=='ready'
    with service.db() as db:
        assert db.execute('SELECT SUM(reserved_seconds) FROM jobs WHERE project=?',(project,)).fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM jobs WHERE project=?',(project,)).fetchone()[0]==40

def test_migration_failure_rolls_back_and_then_recovers(tmp_path,monkeypatch):
    from agentguard.management.service import ControlPlane
    from agentguard.management import migrations
    service,_,_=setup_project(tmp_path)
    with sqlite3.connect(service.db_path) as db:
        for table in ('schema_history','billing_reconciliation','quota_state','recovery_receipts','deletion_applied'):db.execute('DROP TABLE '+table)
        for index in ('jobs_project_state','artifacts_expiry','usage_project_period','events_job_cursor','audit_project_cursor'):db.execute('DROP INDEX '+index)
        db.execute('UPDATE control_meta SET version=1')
    original=migrations.apply_step;count=[0]
    def fail(db,statement):
        original(db,statement);count[0]+=1
        if count[0]==2:raise RuntimeError('Injected migration crash')
    with monkeypatch.context() as patch:
        patch.setattr(migrations,'apply_step',fail)
        with pytest.raises(RuntimeError):ControlPlane(service.root)
    with sqlite3.connect(service.db_path) as db:
        assert db.execute('SELECT version FROM control_meta').fetchone()[0]==1
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_history'").fetchone() is None
    with ControlPlane(service.root).db() as db:assert db.execute('SELECT version FROM control_meta').fetchone()[0]==2

def test_corrupt_quota_ledger_never_grants_another_run(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    job=service.enqueue(token,project,suite['id'],'initial');service.cancel(token,project,job['id'])
    with service.db() as db:db.execute('UPDATE usage SET amount=0 WHERE job=?',(job['id'],))
    with pytest.raises(AccessError,match='QUOTA_LEDGER_UNVERIFIED'):service.enqueue(token,project,suite['id'],'bypass')
    assert service.operations_health(token,project)['status']=='blocked'

def _duplicate_admission(arguments):
    from agentguard.management.service import ControlPlane
    root,token,project,suite=arguments
    return ControlPlane(root).enqueue(token,project,suite,'same-process-load')['id']

def test_real_multiprocess_load_preserves_single_admission(tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    arguments=(service.root,token,project,suite['id'])
    with ProcessPoolExecutor(max_workers=4,mp_context=multiprocessing.get_context('spawn')) as pool:
        ids=list(pool.map(_duplicate_admission,[arguments]*24))
    assert len(set(ids))==1 and service.usage(token,project)['admitted_runs']==1
    assert service.operations_health(token,project)['status']=='ready'
