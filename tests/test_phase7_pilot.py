import pytest
from test_phase7_workflow import setup_project,approved_suite

pytestmark=pytest.mark.integration

def test_manual_plan_activation_is_not_self_attested_payment(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path)
    request=service.request_plan(token,project,'pilot')
    assert request['status']=='pending_manual_payment' and service.usage(token,project)['plan']=='developer'
    with pytest.raises((ValueError,AccessError)):service.request_plan(token,project,'unlimited')
    service.operator_activate_plan(request['id'],operator='billing-operator',receipt_reference='manual-invoice-reference')
    assert service.usage(token,project)['plan']=='pilot'
    cancel=service.request_cancellation(token,project)
    assert cancel['status']=='pending_operator_cancellation'
    service.operator_cancel_plan(cancel['id'],operator='billing-operator',reason='Customer cancellation confirmed')
    assert service.usage(token,project)['plan']=='developer'

def test_feedback_is_tenant_scoped_and_not_external_pilot_evidence(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path)
    ticket=service.feedback(token,project,category='bug',title='Wrong output',body='Steps and expected behavior')
    assert ticket['status']=='open' and ticket['external_validation'] is False
    other=service.create_project(token,'Other')
    with pytest.raises(AccessError):service.feedback_item(token,other['id'],ticket['id'])
    with pytest.raises((ValueError,AccessError)):service.transition_feedback(token,project,ticket['id'],'closed',note='No evidence')
    readiness=service.pilot_readiness(token,project)
    assert readiness['ag075_status']=='PENDING_EXTERNAL_VALIDATION' and not readiness['external_pilot_verified']
