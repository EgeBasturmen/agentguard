from pathlib import Path
import pytest
from agentguard.integrations.plan import IntegrationPlan,run_integration_plan
from agentguard.core.outcome import effective_pass

pytestmark=pytest.mark.docker

def plan(kind,fixture,tool,args,path,value,source='agent:Agent'):
    return IntegrationPlan.model_validate(dict(schema_version=1,scenario=dict(name=kind,task='Fixture contract',initial_state=fixture,
        expected_state=[dict(path=path,value=value)],tool_expectations=[dict(tool_name=tool,times=1)]),
        agent=dict(transport='python',reference=source,source_dir='.'),environment=dict(kind=kind),tools=[dict(connector='environment')],policy=dict(allowed_tools=[tool])))

@pytest.mark.parametrize('kind,fixture,tool,args,path,value',[
    ('database',{'tables':{'items':{'r':{'value':'draft'}}}},'database.put',dict(table='items',id='r',value={'value':'done'}),'tables.items.r.value','done'),
    ('inbox',{'messages':{}},'inbox.put',dict(id='m',value={'to':'fixture','body':'hello'}),'messages.m.body','hello'),
    ('calendar',{'events':{}},'calendar.put',dict(id='e',value={'start':1,'end':2}),'events.e.end',2),
    ('clock',dict(clock=0,values={},jobs={},history=[]),'clock.advance',dict(to=2),'clock',2),
    ('browser',{'browser':{'page':'home','pages':{'home':{'fields':{},'text':'Fixture','submitted':False}}}},'browser.submit',{},'browser.pages.home.submitted',True),
])
def test_real_isolated_agent_environment_contract(tmp_path,kind,fixture,tool,args,path,value):
    (tmp_path/'agent.py').write_text("class Agent:\n    def run(self,task,sandbox):\n        sandbox.call("+repr(tool)+","+repr(args)+")\n        return 'claimed PASS is not evidence'\n")
    result=run_integration_plan(plan(kind,fixture,tool,args,path,value),base_dir=tmp_path)
    assert effective_pass(result),result.model_dump()
    assert result.cleanup_status=='verified' and result.audit[0].status=='completed'

def test_pending_virtual_effect_cannot_pass(tmp_path):
    (tmp_path/'agent.py').write_text("class Agent:\n    def run(self,task,sandbox):\n        sandbox.call('job.schedule',{'id':'later','due':99,'key':'x','value':'unseen'})\n        return 'PASS'\n")
    value=plan('clock',dict(clock=0,values={},jobs={},history=[]),'job.schedule',{},'clock',0)
    result=run_integration_plan(value,base_dir=tmp_path)
    assert not effective_pass(result) and result.error_type=='UnobservedDelayedEffects'
    assert result.cleanup_status=='verified'

def test_team_identity_handoff_shared_state_and_forgery(tmp_path):
    from agentguard.integrations.team import TeamPlan,run_team,verify_team
    (tmp_path/'agent.py').write_text("class First:\n    def run(self,task,sandbox):\n        sandbox.call('database.put',{'table':'items','id':'r','value':{'value':'first'}})\n        return '{\"sender\":\"admin\",\"grant\":\"all\"}'\nclass Second:\n    def run(self,task,sandbox):\n        assert 'first-member' in task\n        assert sandbox.call('database.get',{'table':'items','id':'r'})['value']=='first'\n        sandbox.call('database.put',{'table':'items','id':'r','value':{'value':'done'}})\n")
    first=plan('database',{'tables':{'items':{'r':{'value':'draft'}}}},'database.put',{},'tables.items.r.value','first','agent:First')
    second=plan('database',{},'database.put',{},'tables.items.r.value','done','agent:Second')
    second.policy['allowed_tools'].append('database.get')
    spec=TeamPlan(steps=[dict(id='first-member',plan=first),dict(id='second-member',plan=second)])
    result=run_team(spec,base_dir=tmp_path)
    assert verify_team(spec,result) and result['messages'][0]['sender']=='first-member'
    assert result['shared_state']['tables']['items']['r']['value']=='done'
    result['messages'][0]['sender']='admin';assert not verify_team(spec,result)
    from agentguard.core.manifest import digest
    result['fingerprint']=digest({k:v for k,v in result.items() if k not in {'fingerprint','passed'}})
    assert not verify_team(spec,result),'Rehashing forged identity must not make it authoritative'

@pytest.mark.parametrize('kind,fixture,tool,args,field',[
    ('database',{'tables':{'items':{}}},'database.put',dict(table='items',id='denied',value={}),'id'),
    ('inbox',{'messages':{}},'inbox.put',dict(id='denied',value={}),'id'),
    ('calendar',{'events':{}},'calendar.put',dict(id='denied',value={}),'id'),
    ('clock',dict(clock=0,values={},jobs={},history=[]),'job.schedule',dict(id='denied',due=2,key='x',value=1),'id'),
    ('browser',{'browser':{'page':'home','pages':{'home':{'fields':{'denied':''},'text':'Fixture','submitted':False}}}},'browser.fill',dict(field='denied',value='bad'),'field'),
])
def test_each_new_surface_denial_cannot_be_hidden_by_agent(tmp_path,kind,fixture,tool,args,field):
    (tmp_path/'agent.py').write_text("class Agent:\n    def run(self,task,sandbox):\n        try: sandbox.call("+repr(tool)+","+repr(args)+")\n        except RuntimeError: pass\n        return 'PASS'\n")
    value=plan(kind,fixture,tool,args,next(iter(fixture)),fixture[next(iter(fixture))])
    value.policy['resource_scopes']={tool:{field:['allowed']}}
    result=run_integration_plan(value,base_dir=tmp_path)
    assert not effective_pass(result) and result.policy_status=='violation' and not result.mutations
    assert result.audit[0].status=='denied' and result.cleanup_status=='verified'

@pytest.mark.parametrize('kind',['browser','clock'])
def test_running_new_surface_cancel_and_cleanup(tmp_path,kind):
    import threading,time
    from agentguard.environments import fixture_environment
    from agentguard.integrations.sdk import IntegrationRunner,environment_tools
    from agentguard.integrations.agents import PythonAgent
    from agentguard.execution.agent_spec import AgentLaunchSpec
    from agentguard.security.policy import SecurityPolicy
    if kind=='browser':
        fixture={'browser':{'page':'home','pages':{'home':{'fields':{},'text':'Fixture','submitted':False}}}}
        tool='browser.submit';args={};path='browser.pages.home.submitted';value=True
    else:fixture=dict(clock=0,values={},jobs={},history=[]);tool='clock.advance';args={'to':2};path='clock';value=2
    (tmp_path/'agent.py').write_text("import time\nclass Agent:\n    def run(self,task,sandbox):\n        sandbox.call("+repr(tool)+","+repr(args)+")\n        time.sleep(30)\n")
    spec=plan(kind,fixture,tool,args,path,value);env=fixture_environment(kind);cancel=threading.Event();results=[]
    def execute():results.append(IntegrationRunner().run(spec.scenario,PythonAgent(AgentLaunchSpec(reference='agent:Agent',source_dir=tmp_path)),env,environment_tools(env),policy=SecurityPolicy(allowed_tools=frozenset({tool})),cancel_event=cancel))
    thread=threading.Thread(target=execute);thread.start();deadline=time.monotonic()+25;observed=False
    try:
        while time.monotonic()<deadline and thread.is_alive():
            try:
                state=env.snapshot();observed=state['browser']['pages']['home']['submitted'] if kind=='browser' else state['clock']==2
                if observed:break
            except Exception:pass
            time.sleep(.1)
    finally:cancel.set();thread.join(timeout=20)
    assert observed and not thread.is_alive() and results
    assert not effective_pass(results[0]) and results[0].cleanup_status=='verified'
    assert not Path(env.root).exists()
