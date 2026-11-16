import pytest
from test_phase7_workflow import setup_project,runtime

pytestmark=pytest.mark.integration

def test_live_gateway_needs_operator_project_budget_and_atomic_reservation(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path)
    value=runtime();value['model']=dict(provider='gemini',model='explicit-test-configuration',mode='live',max_calls=1,
        max_total_tokens=1000,max_cost_micro_usd=500)
    with pytest.raises(AccessError):service.connect(token,project,'Not authorized',value,artifact_digest='a'*64)
    service.operator_model_budget(project,max_tokens=1500,max_cost_micro_usd=1000,operator='operator',reason='Offline configuration test; no calls')
    cheap={**value,'model':{**value['model'],'input_rate_micro_usd_per_million':0,'output_rate_micro_usd_per_million':0}}
    with pytest.raises(AccessError):service.connect(token,project,'Pricing bypass',cheap,artifact_digest='a'*64)
    connection=service.connect(token,project,'Explicit bounded configuration',value,artifact_digest='a'*64)
    batch=service.generate_cases(token,project,connection['id'],task='Write done',fixture={'files':{'note.txt':'draft'}},rules=[dict(id='goal',text='expect files.note\\.txt = "done"')])
    case=service.case(token,project,batch['cases'][0]['id'])
    service.review_case(token,project,case['id'],decisions={key:'confirmed' for key in case['oracle_checks']},fixture=True,classification=True)
    service.approve_case(token,project,case['id'],case['case']['fingerprint'])
    suite=service.freeze_suite(token,project,'Release',[case['id']])
    job=service.enqueue(token,project,suite['id'],'reserved')
    assert service.usage(token,project)['reserved_model_tokens']==1000
    with pytest.raises(AccessError):service.enqueue(token,project,suite['id'],'over-budget')
    service.cancel(token,project,job['id'])
    assert service.usage(token,project)['reserved_model_tokens']==0
    service.enqueue(token,project,suite['id'],'after-known-no-dispatch-cancel')
