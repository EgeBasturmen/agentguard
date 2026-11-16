import pytest
from test_phase7_workflow import setup_project,runtime

pytestmark=pytest.mark.integration

def test_receipt_cannot_activate_multiple_projects_and_operations_are_idempotent(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path)
    request=service.request_plan(token,project,'pilot');service.operator_activate_plan(request['id'],operator='fixture',receipt_reference='synthetic-reference')
    assert service.operator_activate_plan(request['id'],operator='fixture',receipt_reference='synthetic-reference')['activated']=='pilot'
    other=service.create_project(token,'Other')['id'];second=service.request_plan(token,other,'pilot')
    with pytest.raises(AccessError):service.operator_activate_plan(second['id'],operator='fixture',receipt_reference='synthetic-reference')
    assert service.project(token,other)['plan']=='developer'
    cancel=service.request_cancellation(token,project)
    first=service.operator_cancel_plan(cancel['id'],operator='fixture',reason='Synthetic cancellation')
    assert service.operator_cancel_plan(cancel['id'],operator='fixture',reason='Synthetic cancellation')==first
    with pytest.raises(AccessError):service.operator_activate_plan(request['id'],operator='fixture',receipt_reference='synthetic-reference')

def test_revoked_model_budget_cancels_unstarted_network_work(tmp_path):
    from agentguard.management.service import AccessError
    from test_phase7_queue import runner
    from phase5_service import ModelService,model_config
    service,token,project=setup_project(tmp_path)
    with ModelService() as endpoint:
        service.operator_model_budget(project,max_tokens=2500,max_cost_micro_usd=15000,operator='fixture',reason='Offline protocol fixture')
        value=runtime();value['policy']['allowed_tools'].append('model.generate');value['model']=model_config(endpoint,max_calls=1,max_total_tokens=2000,max_cost_micro_usd=10000)
        connection=service.connect(token,project,'Fixture',value,artifact_digest='a'*64)
        batch=service.generate_cases(token,project,connection['id'],task='Write done',fixture={'files':{'note.txt':'draft'}},rules=[dict(id='goal',text='expect files.note\\.txt = "done"')])
        case=service.case(token,project,batch['cases'][0]['id'])
        service.review_case(token,project,case['id'],decisions={k:'confirmed' for k in case['oracle_checks']},fixture=True,classification=True)
        service.approve_case(token,project,case['id'],case['case']['fingerprint']);suite=service.freeze_suite(token,project,'Fixture',[case['id']])
        worker=runner(service,token,project);job=service.enqueue(token,project,suite['id'],'queued-live')
        service.operator_model_budget(project,max_tokens=0,max_cost_micro_usd=0,operator='fixture',reason='Revoke unused grant')
        assert service.claim(worker['runner_token']) is None
        assert service.job(token,project,job['id'])['state']=='cancelled'
        assert service.usage(token,project)['reserved_model_tokens']==0 and endpoint.calls==[]

def test_negative_non_admission_usage_blocks_quota(tmp_path):
    from agentguard.management.service import AccessError
    from test_phase7_workflow import approved_suite
    service,token,project=setup_project(tmp_path);_,suite=approved_suite(service,token,project)
    job=service.enqueue(token,project,suite['id'],'negative');service.cancel(token,project,job['id'])
    with service.db() as db:db.execute('INSERT INTO usage VALUES(?,?,?,?,?,?,?)',('a'*32,project,job['id'],'model_tokens',-1,'invalid',service.clock()))
    with pytest.raises(AccessError,match='QUOTA_LEDGER_UNVERIFIED'):service.enqueue(token,project,suite['id'],'bypass-negative')
