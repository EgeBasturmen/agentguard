import json
import sqlite3
import pytest
from test_phase7_workflow import setup_project,approved_suite

pytestmark=pytest.mark.integration

def test_failed_authenticated_operations_consume_rate_budget(tmp_path):
    from agentguard.management.service import ControlPlane,AccessError
    service,token,project=setup_project(tmp_path);service.rate_limit=3
    now=service.clock();service.clock=lambda:now
    for _ in range(3):
        with pytest.raises(AccessError):service.create_project(token,'')
    with pytest.raises(AccessError,match='RATE_LIMITED'):service.project(token,project)
    service.clock=lambda:now+60
    assert service.project(token,project)['id']==project

def test_quiescent_signed_backup_restore_revokes_credentials_and_preserves_data(tmp_path):
    from agentguard.management.operations import backup,restore
    from agentguard.management.service import ControlPlane,AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    archive=tmp_path/'backup.agbackup';backup(service,archive)
    target=tmp_path/'restored'
    restore(archive,target,signing_key=service.key_path,deletions=service.root/'deletions.json')
    restored=ControlPlane(target)
    with pytest.raises(AccessError):restored.project(token,project)
    with restored.db() as db:
        assert db.execute('SELECT name FROM projects WHERE id=?',(project,)).fetchone()[0]=='Generic agent'
    assert restored.scenario_repository(project).get(suite['snapshot']['cases'][0]['id'],1)[1]=='approved'
    with pytest.raises((ValueError,AccessError,FileExistsError)):restore(archive,target,signing_key=service.key_path,deletions=service.root/'deletions.json')

def test_delete_intent_prevents_backup_resurrection_and_needs_current_authority(tmp_path):
    from agentguard.management.operations import backup,restore
    from agentguard.management.service import ControlPlane,AccessError
    service,token,project=setup_project(tmp_path);approved_suite(service,token,project)
    archive=tmp_path/'backup.agbackup';backup(service,archive)
    service.delete_project(token,project,confirmation='Generic agent')
    assert not service.scenario_repository_path(project).exists()
    with pytest.raises(AccessError):service.project(token,project)
    restore(archive,tmp_path/'restored',signing_key=service.key_path,deletions=service.root/'deletions.json')
    with ControlPlane(tmp_path/'restored').db() as db:assert db.execute('SELECT 1 FROM projects WHERE id=?',(project,)).fetchone() is None

def test_backup_rejects_active_work_and_tampering_without_target_files(tmp_path):
    from agentguard.management.operations import backup,restore
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    service.enqueue(token,project,suite['id'],'pending')
    with pytest.raises(AccessError):backup(service,tmp_path/'active.agbackup')
    assert not (tmp_path/'active.agbackup').exists()
    bad=tmp_path/'bad.agbackup';bad.write_bytes(b'not a verified archive')
    with pytest.raises((ValueError,AccessError)):restore(bad,tmp_path/'target',signing_key=service.key_path,deletions=service.root/'deletions.json')
    assert not (tmp_path/'target').exists()

def test_manual_payment_reconciliation_is_idempotent_and_contradictions_block(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path)
    request=service.request_plan(token,project,'pilot')
    service.operator_activate_plan(request['id'],operator='fixture-operator',receipt_reference='synthetic-offline-receipt')
    first=service.reconcile_billing(project,operator='fixture-operator',reference='synthetic-offline-receipt',paid=True)
    assert first['status']=='matched' and not first['payment_independently_verified']
    assert service.reconcile_billing(project,operator='fixture-operator',reference='synthetic-offline-receipt',paid=True)==first
    with pytest.raises(AccessError):service.reconcile_billing(project,operator='fixture-operator',reference='synthetic-offline-receipt',paid=False)
    cancel=service.request_cancellation(token,project);service.operator_cancel_plan(cancel['id'],operator='fixture-operator',reason='Fixture cancellation')
    assert service.operations_health(token,project)['billing']['plan']=='developer'

def test_onboarding_counts_do_not_turn_configuration_into_verified_success(tmp_path):
    service,token,project=setup_project(tmp_path)
    funnel=service.onboarding_funnel(token,project)
    assert funnel['verified_current_runs']==0 and not funnel['self_service_success_verified']
    approved_suite(service,token,project)
    funnel=service.onboarding_funnel(token,project)
    assert funnel['configured_connections']==1 and funnel['approved_cases']>0 and funnel['verified_current_runs']==0
    assert not funnel['external_pilot_verified']

def test_legacy_backup_without_upgrade_restores_through_migration(tmp_path):
    from agentguard.management.store import Store
    from agentguard.management.service import ControlPlane
    from agentguard.management.operations import backup,restore
    service,_,project=setup_project(tmp_path)
    with sqlite3.connect(service.db_path) as db:
        for table in ('schema_history','billing_reconciliation','quota_state','recovery_receipts','deletion_applied'):db.execute('DROP TABLE '+table)
        for index in ('jobs_project_state','artifacts_expiry','usage_project_period','events_job_cursor','audit_project_cursor'):db.execute('DROP INDEX '+index)
        db.execute('UPDATE control_meta SET version=1')
    legacy=Store(service.root,migrate_schema=False);archive=tmp_path/'legacy.agbackup';backup(legacy,archive)
    with sqlite3.connect(service.db_path) as db:assert db.execute('SELECT version FROM control_meta').fetchone()[0]==1
    restore(archive,tmp_path/'restored',signing_key=service.key_path,deletions=service.root/'deletions.json')
    with ControlPlane(tmp_path/'restored').db() as db:
        assert db.execute('SELECT version FROM control_meta').fetchone()[0]==2
        assert db.execute('SELECT 1 FROM projects WHERE id=?',(project,)).fetchone()

def test_expired_evidence_is_not_resurrected_as_allow_by_restore(tmp_path,monkeypatch):
    from test_phase7_results import execute_http_job
    from agentguard.management.service import ControlPlane
    from agentguard.management.operations import backup,restore
    service,token,project,worker,job,claim,report=execute_http_job(tmp_path)
    assert service.finish(worker['runner_token'],job['id'],claim['lease'],report)['gate_decision']=='ALLOW'
    archive=tmp_path/'before-expiry.agbackup';backup(service,archive);now=service.clock()
    with monkeypatch.context() as patch:
        patch.setattr('agentguard.management.store.time.time',lambda:now+8*86400)
        restore(archive,tmp_path/'restored',signing_key=service.key_path,deletions=service.root/'deletions.json')
        restored=ControlPlane(tmp_path/'restored')
        with restored.db() as db:
            owner=db.execute("SELECT workspace,user FROM workspace_members WHERE role='owner'").fetchone()
        recovered=restored.operator_recover_owner(owner[0],owner[1],operator='fixture',reason='Restored credential reset')
        assert restored.job(recovered['token'],project,job['id'])['gate_decision']=='BLOCK'

def test_account_primary_erasure_and_restore_preserve_other_workspace(tmp_path):
    from agentguard.management.operations import backup,restore
    from agentguard.management.service import ControlPlane,AccessError
    service,token,project=setup_project(tmp_path);approved_suite(service,token,project)
    other=service.create_workspace(token,'other-technical-user','Other workspace')
    other_project=service.create_project(other['token'],'Other project')['id']
    archive=tmp_path/'before-account-delete.agbackup';backup(service,archive)
    assert service.delete_workspace(token,confirmation='Initial workspace')['deleted']
    with pytest.raises(AccessError):service.projects(token)
    assert service.project(other['token'],other_project)['name']=='Other project'
    assert not service.scenario_repository_path(project).exists()
    restore(archive,tmp_path/'restored-account-delete',signing_key=service.key_path,deletions=service.root/'deletions.json')
    with ControlPlane(tmp_path/'restored-account-delete').db() as db:
        assert db.execute('SELECT count(*) FROM workspaces').fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM workspace_members WHERE user='owner'").fetchone()[0]==0
