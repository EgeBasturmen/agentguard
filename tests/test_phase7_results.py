import json
import pytest
from phase3_service import Service
from test_generated_execution import http_request
from test_phase7_workflow import setup_project
from test_phase7_queue import runner

pytestmark=pytest.mark.integration

def execute_http_job(tmp_path):
    from agentguard.management.runner import execute_spec
    with Service() as remote:
        service,token,project=setup_project(tmp_path)
        request=http_request(remote)
        connection=service.connect(token,project,'HTTP',request.runtime.model_dump(mode='json'),artifact_digest='a'*64)
        batch=service.generate_cases(token,project,connection['id'],task=request.task,fixture=request.fixture,rules=[r.model_dump() for r in request.rules])
        case=service.case(token,project,batch['cases'][0]['id'])
        service.review_case(token,project,case['id'],decisions={key:'confirmed' for key in case['oracle_checks']},fixture=True,classification=True)
        service.approve_case(token,project,case['id'],case['case']['fingerprint'])
        suite=service.freeze_suite(token,project,'Release',[case['id']])
        worker=runner(service,token,project);job=service.enqueue(token,project,suite['id'],'one')
        claim=service.claim(worker['runner_token'])
        result=execute_spec(claim['spec'],tmp_path,allow_external=True)
        return service,token,project,worker,job,claim,result

def test_real_http_result_is_recomputed_idempotent_redacted_and_tenant_scoped(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project,worker,job,claim,result=execute_http_job(tmp_path)
    done=service.finish(worker['runner_token'],job['id'],claim['lease'],result)
    assert done['gate_decision']=='ALLOW' and not done['authorizes_release']
    assert done['assurance'].startswith('customer-private-runner-reported')
    assert service.finish(worker['runner_token'],job['id'],claim['lease'],result)==done
    assert service.usage(token,project)['reserved_seconds']==0
    grant=service.artifact_grant(token,project,done['artifact']['id'])
    artifact=service.artifact(token,project,done['artifact']['id'],grant['grant'])
    assert artifact['decision']['gate_decision']=='ALLOW' and artifact['recomputable'] is False
    other=service.create_project(token,'Other')
    with pytest.raises(AccessError):service.artifact(token,other['id'],done['artifact']['id'],grant['grant'])
    now=service.clock();service.clock=lambda:now+61
    with pytest.raises(AccessError):service.artifact(token,project,done['artifact']['id'],grant['grant'])

def test_cancel_fence_and_report_tampering_cannot_pass(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project,worker,job,claim,result=execute_http_job(tmp_path)
    changed=json.loads(json.dumps(result));changed['execution']['cases'][0]['result']['comparison_manifest']['scenario']['task']='forged'
    with pytest.raises((ValueError,AccessError)):service.finish(worker['runner_token'],job['id'],claim['lease'],changed)
    service.cancel(token,project,job['id'])
    done=service.finish(worker['runner_token'],job['id'],claim['lease'],result)
    assert done['gate_decision']=='BLOCK'

def test_comparison_requires_original_private_evidence_and_current_project(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project,worker,job,claim,result=execute_http_job(tmp_path)
    service.finish(worker['runner_token'],job['id'],claim['lease'],result)
    comparison=service.compare_reports(token,project,baseline_job=job['id'],candidate_job=job['id'],baseline_report=result,candidate_report=result)
    assert comparison['decision']['gate_decision']=='ALLOW' and not comparison['decision']['authorizes_release']
    forged=json.loads(json.dumps(result));forged['execution']['cases'][0]['result']['worker_output']='forged'
    with pytest.raises(AccessError):service.compare_reports(token,project,baseline_job=job['id'],candidate_job=job['id'],baseline_report=result,candidate_report=forged)

def test_retention_redaction_and_download_require_current_authorization(tmp_path):
    from agentguard.management.service import AccessError
    from agentguard.management.redaction import redact
    assert 'person@example.test' not in str(redact({'email':'person@example.test','message':'sent to person@example.test'},['email']))
    service,token,project,worker,job,claim,result=execute_http_job(tmp_path)
    service.set_data_policy(token,project,retention_days=1,redact_fields=['email'])
    done=service.finish(worker['runner_token'],job['id'],claim['lease'],result)
    service.add_member(token,project,'reader','viewer');key=service.issue_key(token,project,'reader',['project:read','artifact:read'])
    grant=service.artifact_grant(key['token'],project,done['artifact']['id'])
    service.remove_member(token,project,'reader')
    with pytest.raises(AccessError):service.artifact(key['token'],project,done['artifact']['id'],grant['grant'])
    now=service.clock();service.clock=lambda:now+86401
    expired=service.job(token,project,job['id'])
    assert expired['gate_decision']=='BLOCK' and not expired['evidence_retained']
    assert service.purge_expired()['artifacts_deleted']==1
    with pytest.raises(AccessError):service.artifact_grant(token,project,done['artifact']['id'])

def test_deletion_blocks_current_gate_and_cannot_cross_project(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project,worker,job,claim,result=execute_http_job(tmp_path)
    done=service.finish(worker['runner_token'],job['id'],claim['lease'],result)
    other=service.create_project(token,'Other')
    with pytest.raises(AccessError):service.delete_artifact(token,other['id'],done['artifact']['id'])
    service.delete_artifact(token,project,done['artifact']['id'])
    removed=service.job(token,project,job['id'])
    assert removed['gate_decision']=='BLOCK' and removed['historical_decision']['gate_decision']=='ALLOW'
