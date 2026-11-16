import copy
import threading
import pytest

pytestmark=pytest.mark.integration

@pytest.mark.parametrize('name',['fixture-text','fixture-submit'])
def test_browser_fixture_reserved_dom_identity_rejected(name):
    from agentguard.environments.browser import fixture_check
    value={'browser':{'page':'home','pages':{'home':{'fields':{name:'x'},'text':'','submitted':False}}}}
    with pytest.raises(ValueError):fixture_check(value)

def test_team_duplicate_identity_and_hidden_model_rejected():
    from agentguard.integrations.team import TeamPlan
    from agentguard.integrations.plan import IntegrationPlan
    value=IntegrationPlan.model_validate(dict(schema_version=1,scenario=dict(name='s',task='t',initial_state={},expected_state=[dict(path='tables',value={})]),agent=dict(transport='python',reference='agent:Agent'),environment=dict(kind='database')))
    with pytest.raises(ValueError):TeamPlan(steps=[dict(id='same',plan=value),dict(id='same',plan=value)])
    value.agent.transport='http'
    with pytest.raises(ValueError):TeamPlan(steps=[dict(id='one',plan=value),dict(id='two',plan=value)])

def test_database_scope_identity_sql_injection_calendar_and_oversize_rejected():
    from agentguard.environments.services import RecordEnvironment
    env=RecordEnvironment();env.provision({'tables':{'items':{}}})
    try:
        with pytest.raises(ValueError):env.invoke('database.put',dict(table="items';DROP TABLE records;--",id='r',value={}))
        with pytest.raises(ValueError):env.invoke('database.put',dict(table='items',id='r',value={'text':'x'*65537}))
        assert env.snapshot()=={'tables':{'items':{}}}
    finally:assert env.cleanup()=='verified'
    calendar=RecordEnvironment(kind='calendar');calendar.provision({'events':{}})
    try:
        with pytest.raises(ValueError):calendar.invoke('calendar.put',dict(id='bad',value={'start':3,'end':1}))
        assert calendar.snapshot()=={'events':{}}
    finally:assert calendar.cleanup()=='verified'

def test_clock_invalid_schedule_has_no_effect_and_fixture_reset_isolated():
    from agentguard.environments.clock import ClockEnvironment
    fixture=dict(clock=0,values={},jobs={},history=[]);env=ClockEnvironment();env.provision(fixture)
    try:
        for due in [True,-1,2**53+1,'tomorrow']:
            with pytest.raises(ValueError):env.invoke('job.schedule',dict(id='x',due=due,key='x',value='bad'))
            assert env.snapshot()==fixture
    finally:assert env.cleanup()=='verified'

@pytest.mark.parametrize('kind,fixture,rule',[
    ('database',{'tables':{'items':{}}},'expect tables.items = {}'),
    ('inbox',{'messages':{}},'expect messages = {}'),
    ('calendar',{'events':{}},'expect events = {}'),
    ('clock',dict(clock=0,values={},jobs={},history=[]),'expect clock = 0'),
    ('browser',{'browser':{'page':'home','pages':{'home':{'fields':{},'text':'Fixture','submitted':False}}}},'expect browser.page = "home"')])
def test_management_generation_supports_new_environment_without_host_import(tmp_path,kind,fixture,rule):
    from test_phase7_workflow import setup_project,runtime
    service,token,project=setup_project(tmp_path);value=runtime();value['environment']['kind']=kind
    value['policy']['allowed_tools']={'database':['database.get'],'inbox':['inbox.list'],'calendar':['calendar.list'],'clock':['clock.advance'],'browser':['browser.text']}[kind]
    connection=service.connect(token,project,'New fixture',value,artifact_digest='a'*64)
    batch=service.generate_cases(token,project,connection['id'],task='Fixture readiness',fixture=fixture,rules=[dict(id='goal',text=rule)])
    case=service.case(token,project,batch['cases'][0]['id'])
    assert case['oracle_checks'] and not connection['verified'] and not connection['automatic_discovery']
    service.review_case(token,project,case['id'],decisions={k:'confirmed' for k in case['oracle_checks']},fixture=True,classification=True)
    service.approve_case(token,project,case['id'],case['case']['fingerprint'])
    assert service.freeze_suite(token,project,'New surface',[case['id']])['snapshot']['cases']

@pytest.mark.parametrize('kind',['database','inbox','calendar','clock','browser'])
def test_invalid_new_fixture_fails_preflight_without_provisioning(tmp_path,kind):
    from agentguard.integrations.plan import IntegrationPlan,run_integration_plan
    value=IntegrationPlan.model_validate(dict(schema_version=1,scenario=dict(name='invalid',task='No import',initial_state={},expected_state=[dict(path='x',value=1)]),
        agent=dict(transport='python',reference='missing:Agent'),environment=dict(kind=kind),tools=[dict(connector='environment')]))
    check=run_integration_plan(value,base_dir=tmp_path,preflight_only=True)
    assert not check.supported and any('fixture' in issue.lower() for issue in check.issues)
