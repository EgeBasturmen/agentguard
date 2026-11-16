import json
import pytest

pytestmark=pytest.mark.integration

def test_membership_scope_rotation_and_tenant_isolation(tmp_path):
    from agentguard.management.service import ControlPlane,AccessError
    service=ControlPlane(tmp_path/'server')
    owner=service.bootstrap('owner-a')
    a=service.create_project(owner['token'],'A')
    service.add_member(owner['token'],a['id'],'reader','viewer')
    reader=service.issue_key(owner['token'],a['id'],'reader',['project:read','artifact:read'])
    assert service.project(reader['token'],a['id'])['name']=='A'
    with pytest.raises(AccessError):service.add_member(reader['token'],a['id'],'attacker','owner')
    other=service.create_workspace(owner['token'],'owner-b','B')
    b=service.create_project(other['token'],'B')
    with pytest.raises(AccessError):service.project(reader['token'],b['id'])
    assert [p['id'] for p in service.projects(reader['token'])]==[a['id']]
    rotated=service.rotate_key(reader['token'])
    with pytest.raises(AccessError):service.project(reader['token'],a['id'])
    assert service.project(rotated['token'],a['id'])['id']==a['id']
    service.revoke_key(owner['token'],a['id'],rotated['key_id'])
    with pytest.raises(AccessError):service.project(rotated['token'],a['id'])
    assert owner['token'].encode() not in service.db_path.read_bytes()
    assert reader['token'].encode() not in service.db_path.read_bytes()

def test_expiry_escalation_and_rate_are_server_side(tmp_path):
    from agentguard.management.service import ControlPlane,AccessError
    service=ControlPlane(tmp_path/'server',rate_limit=100)
    owner=service.bootstrap('owner');project=service.create_project(owner['token'],'A')
    service.add_member(owner['token'],project['id'],'developer','developer')
    with pytest.raises(AccessError):service.issue_key(owner['token'],project['id'],'developer',['suite:review'])
    key=service.issue_key(owner['token'],project['id'],'developer',['project:read'],ttl_seconds=1)
    service.clock=lambda:10**12
    with pytest.raises(AccessError):service.project(key['token'],project['id'])
    with pytest.raises(AccessError):service.bootstrap('another-owner')

def test_revoked_membership_invalidates_existing_keys(tmp_path):
    from agentguard.management.service import ControlPlane,AccessError
    service=ControlPlane(tmp_path/'server');owner=service.bootstrap('owner')
    project=service.create_project(owner['token'],'A')
    service.add_member(owner['token'],project['id'],'reader','viewer')
    key=service.issue_key(owner['token'],project['id'],'reader',['project:read'])
    service.remove_member(owner['token'],project['id'],'reader')
    with pytest.raises(AccessError):service.project(key['token'],project['id'])

def test_management_credentials_cannot_be_saved_as_project_names(tmp_path):
    from agentguard.management.service import ControlPlane,AccessError
    from agentguard.management.redaction import redact
    service=ControlPlane(tmp_path/'server');owner=service.bootstrap('owner')
    assert owner['token'] not in str(redact({'output':owner['token']}))
    with pytest.raises((ValueError,AccessError)):service.create_project(owner['token'],owner['token'])

def test_narrow_keys_can_rotate_without_gaining_read_scope(tmp_path):
    from agentguard.management.service import ControlPlane,AccessError
    service=ControlPlane(tmp_path/'server');owner=service.bootstrap('owner')
    project=service.create_project(owner['token'],'A')
    service.add_member(owner['token'],project['id'],'developer','developer')
    key=service.issue_key(owner['token'],project['id'],'developer',['run:write'])
    rotated=service.rotate_key(key['token'])
    with pytest.raises(AccessError):service.project(rotated['token'],project['id'])

def test_operator_recovery_revokes_old_keys_and_runners(tmp_path):
    from agentguard.management.service import ControlPlane,AccessError
    from test_phase7_queue import runner
    service=ControlPlane(tmp_path/'server');owner=service.bootstrap('owner');project=service.create_project(owner['token'],'A')['id']
    worker=runner(service,owner['token'],project)
    recovered=service.operator_recover_owner(owner['workspace'],'owner',operator='local-operator',reason='Lost credential; operator verified access')
    with pytest.raises(AccessError):service.project(owner['token'],project)
    with pytest.raises(AccessError):service.claim(worker['runner_token'])
    assert service.project(recovered['token'],project)['id']==project
