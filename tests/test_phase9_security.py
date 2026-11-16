import hashlib
import json
import sqlite3
import zipfile
import pytest
from test_phase7_workflow import setup_project,approved_suite

pytestmark=pytest.mark.integration

def test_future_scenario_schema_is_rejected_without_modification(tmp_path):
    from agentguard.generation.repository import ScenarioRepository
    path=tmp_path/'future.sqlite'
    with sqlite3.connect(path) as db:db.executescript('CREATE TABLE agentguard_meta(version INTEGER); INSERT INTO agentguard_meta VALUES(99);')
    before=path.read_bytes()
    with pytest.raises(ValueError):ScenarioRepository(path)
    assert path.read_bytes()==before

def test_quota_recovery_never_fabricates_missing_usage(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    job=service.enqueue(token,project,suite['id'],'lost')
    with service.db() as db:db.execute('DELETE FROM usage WHERE job=?',(job['id'],))
    assert service.operations_health(token,project)['status']=='blocked'
    with pytest.raises(AccessError):service.enqueue(token,project,suite['id'],'new')
    with service.db() as db:assert db.execute('SELECT COUNT(*) FROM usage WHERE job=?',(job['id'],)).fetchone()[0]==0

def test_backup_wrong_key_traversal_and_journal_forgery_are_rejected(tmp_path):
    from agentguard.management.operations import backup,restore,signature
    from agentguard.management.service import AccessError
    service,_,_=setup_project(tmp_path);archive=tmp_path/'valid.agbackup';backup(service,archive)
    key=tmp_path/'wrong.key';key.write_bytes(b'x'*32)
    with pytest.raises(AccessError):restore(archive,tmp_path/'wrong',signing_key=key,deletions=service.root/'deletions.json')
    assert not (tmp_path/'wrong').exists()
    with zipfile.ZipFile(archive) as zipped:manifest=json.loads(zipped.read('manifest.json'));payload=zipped.read('control.sqlite')
    manifest['files']['../../escape.scenarios.sqlite']={"size":len(payload),"sha256":hashlib.sha256(payload).hexdigest()}
    manifest['mac']=signature(service.signing_key,{k:v for k,v in manifest.items() if k!='mac'})
    malicious=tmp_path/'malicious.agbackup'
    with zipfile.ZipFile(malicious,'w') as zipped:
        zipped.writestr('manifest.json',json.dumps(manifest));zipped.writestr('control.sqlite',payload);zipped.writestr('../../escape.scenarios.sqlite',payload)
    with pytest.raises(AccessError):restore(malicious,tmp_path/'escape',signing_key=service.key_path,deletions=service.root/'deletions.json')
    assert not (tmp_path/'escape').exists() and not (tmp_path/'escape.scenarios.sqlite').exists()

def test_project_deletion_rejects_other_tenant_wrong_confirmation_and_active_jobs(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    other=service.create_workspace(token,'other-fixture','Other')
    with pytest.raises(AccessError):service.delete_project(other['token'],project,confirmation='Generic agent')
    with pytest.raises(AccessError):service.delete_project(token,project,confirmation='wrong')
    job=service.enqueue(token,project,suite['id'],'active')
    with pytest.raises(AccessError):service.delete_project(token,project,confirmation='Generic agent')
    service.cancel(token,project,job['id']);assert service.delete_project(token,project,confirmation='Generic agent')['deleted']

def test_pending_enrollment_has_server_side_abuse_bound(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path)
    for index in range(20):service.enroll_runner(token,project,'Fixture '+str(index))
    with pytest.raises(AccessError,match='ENROLLMENT_LIMIT'):service.enroll_runner(token,project,'Overflow')

def test_missing_authoritative_deletion_journal_is_not_silently_recreated(tmp_path):
    from agentguard.management.service import ControlPlane,AccessError
    service,_,_=setup_project(tmp_path)
    path=service.root/'deletions.json';path.rename(service.root/'lost-journal.json')
    with pytest.raises(AccessError,match='DELETION_JOURNAL_UNVERIFIED'):ControlPlane(service.root)
    assert not path.exists()

def test_key_rotation_cannot_reset_principal_rate_budget(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);service.rate_limit=3
    rotated=service.rotate_key(token);rotated=service.rotate_key(rotated['token'])
    with pytest.raises(AccessError,match='RATE_LIMITED'):service.project(rotated['token'],project)

def test_scoped_key_capacity_still_allows_rotation(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path)
    keys=[service.issue_key(token,project,'owner',['project:read']) for _ in range(32)]
    with pytest.raises(AccessError,match='KEY_LIMIT'):service.issue_key(token,project,'owner',['project:read'])
    assert service.rotate_key(keys[0]['token'])['token']!=keys[0]['token']

def test_old_signed_journal_cannot_replace_committed_deletion_state(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);before=(service.root/'deletions.json').read_bytes()
    service.delete_project(token,project,confirmation='Generic agent')
    (service.root/'deletions.json').write_bytes(before)
    with pytest.raises(AccessError,match='DELETION_JOURNAL_REGRESSION'):service.projects(token)

def test_account_erase_requires_workspace_owner_and_verified_quiescence(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    scoped=service.issue_key(token,project,'owner',['project:admin','project:read'])
    with pytest.raises(AccessError,match='WORKSPACE_OWNER_REQUIRED'):service.delete_workspace(scoped['token'],confirmation='Initial workspace')
    job=service.enqueue(token,project,suite['id'],'pending-account-delete')
    with pytest.raises(AccessError,match='VERIFIED_QUIESCENCE_REQUIRED'):service.delete_workspace(token,confirmation='Initial workspace')
    assert service.project(token,project)
    service.cancel(token,project,job['id'])
