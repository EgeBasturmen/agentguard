import pytest
from test_phase7_workflow import setup_project,approved_suite

pytestmark=pytest.mark.integration

def runner(service,token,project):
    invitation=service.enroll_runner(token,project,'Private CI')
    return service.exchange_enrollment(invitation['enrollment_token'])

def test_durable_idempotency_fencing_and_runner_revocation(tmp_path):
    from agentguard.management.service import ControlPlane,AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    worker=runner(service,token,project)
    job=service.enqueue(token,project,suite['id'],'one')
    assert service.enqueue(token,project,suite['id'],'one')['id']==job['id']
    service=ControlPlane(service.root)
    claimed=service.claim(worker['runner_token'])
    assert claimed['id']==job['id'] and service.claim(worker['runner_token']) is None
    with pytest.raises(AccessError):service.heartbeat(worker['runner_token'],job['id'],'wrong')
    assert not service.heartbeat(worker['runner_token'],job['id'],claimed['lease'])['cancel']
    service.revoke_runner(token,project,worker['id'])
    with pytest.raises(AccessError):service.heartbeat(worker['runner_token'],job['id'],claimed['lease'])
    result=service.job(token,project,job['id'])
    assert result['state']=='error' and result['gate_decision']=='BLOCK' and result['cleanup_status']=='unverified'

def test_expired_lease_is_not_requeued_or_passed_and_quota_is_reserved(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    worker=runner(service,token,project);job=service.enqueue(token,project,suite['id'],'one')
    claimed=service.claim(worker['runner_token']);now=service.clock();service.clock=lambda:now+100
    assert service.claim(worker['runner_token']) is None
    result=service.job(token,project,job['id']);assert result['state']=='error' and result['gate_decision']=='BLOCK'
    with pytest.raises(AccessError):service.heartbeat(worker['runner_token'],job['id'],claimed['lease'])
    assert service.usage(token,project)['reserved_seconds']>0

def test_atomic_project_quota_and_queued_cancel(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    first=service.enqueue(token,project,suite['id'],'one');service.enqueue(token,project,suite['id'],'two')
    with pytest.raises(AccessError):service.enqueue(token,project,suite['id'],'three')
    service.cancel(token,project,first['id'])
    assert service.enqueue(token,project,suite['id'],'three')['state']=='queued'
    assert service.usage(token,project)['admitted_runs']==3

def test_enrollment_one_time_and_cross_project_job_denied(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    invitation=service.enroll_runner(token,project,'Worker');worker=service.exchange_enrollment(invitation['enrollment_token'])
    with pytest.raises(AccessError):service.exchange_enrollment(invitation['enrollment_token'])
    other=service.create_project(token,'Other');other_worker=runner(service,token,other['id'])
    job=service.enqueue(token,project,suite['id'],'one');claim=service.claim(worker['runner_token'])
    assert service.claim(other_worker['runner_token']) is None
    with pytest.raises(AccessError):service.heartbeat(other_worker['runner_token'],job['id'],claim['lease'])
