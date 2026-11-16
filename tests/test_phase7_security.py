from concurrent.futures import ThreadPoolExecutor
import json
import pytest
from test_phase7_workflow import setup_project,approved_suite,runtime
from test_phase7_queue import runner
from test_phase7_results import execute_http_job

pytestmark=pytest.mark.integration

def test_concurrent_idempotent_create_and_claim_are_atomic_across_instances(tmp_path):
    from agentguard.management.service import ControlPlane
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    root=service.root
    def enqueue(_):return ControlPlane(root).enqueue(token,project,suite['id'],'same-request')['id']
    with ThreadPoolExecutor(max_workers=4) as pool:jobs=list(pool.map(enqueue,range(8)))
    assert len(set(jobs))==1 and service.usage(token,project)['admitted_runs']==1
    a=runner(service,token,project);b=runner(service,token,project)
    with ThreadPoolExecutor(max_workers=2) as pool:claims=list(pool.map(lambda key:ControlPlane(root).claim(key),[a['runner_token'],b['runner_token']]))
    assert sum(result is not None for result in claims)==1

def test_concurrent_quota_cannot_be_oversubscribed(tmp_path):
    from agentguard.management.service import ControlPlane,AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    def enqueue(index):
        try:return ControlPlane(service.root).enqueue(token,project,suite['id'],'request-'+str(index))
        except AccessError:return None
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(enqueue,range(8)))
    assert sum(result is not None for result in results)==2
    assert service.usage(token,project)['admitted_runs']==2

def test_result_and_artifact_corruption_cannot_preserve_allow(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project,worker,job,claim,result=execute_http_job(tmp_path)
    done=service.finish(worker['runner_token'],job['id'],claim['lease'],result)
    grant=service.artifact_grant(token,project,done['artifact']['id'])
    with service.db() as db:db.execute('UPDATE jobs SET cleanup=? WHERE id=?',('unverified',job['id']))
    assert service.job(token,project,job['id'])['gate_decision']=='BLOCK'
    with pytest.raises(AccessError):service.artifact(token,project,done['artifact']['id'],grant['grant'])

@pytest.mark.parametrize('payload',[{}, {'passed':True}, {'execution':{}}, {'preflight':{'supported':True}}])
def test_incomplete_or_wrong_kind_runner_report_is_never_pass(tmp_path,payload):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    worker=runner(service,token,project);job=service.enqueue(token,project,suite['id'],'one');claim=service.claim(worker['runner_token'])
    with pytest.raises((ValueError,AccessError)):service.finish(worker['runner_token'],job['id'],claim['lease'],payload)
    assert service.job(token,project,job['id'])['gate_decision']=='BLOCK'

def test_runner_credential_cannot_read_management_data(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);worker=runner(service,token,project)
    with pytest.raises(AccessError):service.project(worker['runner_token'],project)
    with pytest.raises(AccessError):service.claim(token)

def test_tenant_cannot_forge_operator_plan_or_pricing_policy(tmp_path):
    from agentguard.management.http import running_server
    from agentguard.management.client import Client
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path)
    with running_server(service) as url:
        client=Client(url,token)
        for action in ['operator-model-budget','activate-plan','operator/recover-owner']:
            with pytest.raises(AccessError):client.request('/api/projects/'+project+'/'+action,{})
    assert service.project(token,project)['model_tokens']==0

def test_user_concurrency_cannot_be_bypassed_with_another_key(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    payment=service.request_plan(token,project,'pilot');service.operator_activate_plan(payment['id'],operator='operator',receipt_reference='technical-fixture-only')
    service.enqueue(token,project,suite['id'],'one');service.enqueue(token,project,suite['id'],'two')
    key=service.issue_key(token,project,'owner',['project:read','run:write'])
    with pytest.raises(AccessError):service.enqueue(key['token'],project,suite['id'],'three')

@pytest.mark.parametrize('state',['error','cancelled','queued'])
def test_noncompleted_state_cannot_expose_stored_allow(tmp_path,state):
    from agentguard.regression.gate import GateDecision
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    job=service.enqueue(token,project,suite['id'],'one')
    with service.db() as db:db.execute('UPDATE jobs SET state=?,decision=? WHERE id=?',(state,json.dumps(GateDecision().model_dump(mode='json')),job['id']))
    assert service.job(token,project,job['id'])['gate_decision']=='BLOCK'

@pytest.mark.parametrize('offboard',['membership','key'])
def test_pending_enrollment_does_not_outlive_issuer_authority(tmp_path,offboard):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path)
    service.add_member(token,project,'admin','admin')
    key=service.issue_key(token,project,'admin',['project:read','project:admin'])
    invitation=service.enroll_runner(key['token'],project,'Pending')
    if offboard=='membership':service.remove_member(token,project,'admin')
    else:service.revoke_key(token,project,key['key_id'])
    with pytest.raises(AccessError):service.exchange_enrollment(invitation['enrollment_token'])

def test_removed_admins_runner_cannot_keep_claiming_tenant_data(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    service.add_member(token,project,'admin','admin')
    key=service.issue_key(token,project,'admin',['project:read','project:admin'])
    worker=runner(service,key['token'],project)
    service.enqueue(token,project,suite['id'],'work')
    service.remove_member(token,project,'admin')
    with pytest.raises(AccessError):service.claim(worker['runner_token'])

def test_future_database_is_rejected_before_installing_tables(tmp_path):
    import sqlite3
    import hashlib
    from agentguard.management.service import ControlPlane,AccessError
    root=tmp_path/'future';root.mkdir();database=root/'control.sqlite'
    with sqlite3.connect(database) as db:
        db.executescript('CREATE TABLE control_meta(version INTEGER); INSERT INTO control_meta VALUES(99); CREATE TABLE future_data(value TEXT);')
    before=hashlib.sha256(database.read_bytes()).hexdigest()
    with pytest.raises(AccessError):ControlPlane(root)
    assert hashlib.sha256(database.read_bytes()).hexdigest()==before
