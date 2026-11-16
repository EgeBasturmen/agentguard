"""Installed-package contracts and real controlled fixture execution; no external APIs."""
import argparse
import json
from pathlib import Path
import tempfile

def verify(docker=False,browser=False):
    from agentguard.sdk import RecordEnvironment,ClockEnvironment,BrowserEnvironment,contract_test,TeamPlan,run_team,verify_team
    from agentguard.integrations.plan import IntegrationPlan,run_integration_plan
    from agentguard.core.outcome import effective_pass
    fixtures=[('database',{'tables':{'items':{}}},'database.put',dict(table='items',id='r',value={'value':'done'}),'tables.items.r.value','done'),
        ('inbox',{'messages':{}},'inbox.put',dict(id='m',value={'body':'hello'}),'messages.m.body','hello'),
        ('calendar',{'events':{}},'calendar.put',dict(id='e',value={'start':1,'end':2}),'events.e.end',2),
        ('clock',dict(clock=0,values={},jobs={},history=[]),'clock.advance',dict(to=2),'clock',2)]
    for kind,fixture,*_ in fixtures:
        assert contract_test(ClockEnvironment() if kind=='clock' else RecordEnvironment(kind=kind),fixture)
    if browser:
        fixture={'browser':{'page':'home','pages':{'home':{'fields':{},'text':'Fixture','submitted':False}}}}
        assert contract_test(BrowserEnvironment(),fixture)
        fixtures.append(('browser',fixture,'browser.submit',{},'browser.pages.home.submitted',True))
    if docker:
        with tempfile.TemporaryDirectory(prefix='agentguard-phase8-wheel-') as directory:
            root=Path(directory)
            (root/'agent.py').write_text("import json\nclass Agent:\n    def run(self,task,sandbox):\n        call=json.loads(task)\n        return str(sandbox.call(call['tool'],call['arguments']))\nclass First:\n    def run(self,task,sandbox):\n        sandbox.call('database.put',{'table':'items','id':'r','value':{'value':'first'}})\n        return 'untrusted handoff'\nclass Second:\n    def run(self,task,sandbox):\n        assert sandbox.call('database.get',{'table':'items','id':'r'})['value']=='first'\n        sandbox.call('database.put',{'table':'items','id':'r','value':{'value':'done'}})\n",encoding='utf-8')
            for kind,fixture,tool,args,path,value in fixtures:
                plan=IntegrationPlan.model_validate(dict(schema_version=1,scenario=dict(name=kind,task=json.dumps(dict(tool=tool,arguments=args)),initial_state=fixture,expected_state=[dict(path=path,value=value)],tool_expectations=[dict(tool_name=tool,times=1)]),agent=dict(transport='python',reference='agent:Agent'),environment=dict(kind=kind),tools=[dict(connector='environment')],policy=dict(allowed_tools=[tool])))
                result=run_integration_plan(plan,base_dir=root)
                assert effective_pass(result) and result.cleanup_status=='verified',kind
            def member(reference,fixture,value):
                return dict(schema_version=1,scenario=dict(name='handoff',task='Transfer observed state',initial_state=fixture,expected_state=[dict(path='tables.items.r.value',value=value)]),agent=dict(transport='python',reference=reference),environment=dict(kind='database'),tools=[dict(connector='environment')],policy=dict(allowed_tools=['database.put','database.get']))
            team=TeamPlan.model_validate(dict(steps=[dict(id='first',plan=member('agent:First',{'tables':{'items':{}}},'first')),dict(id='second',plan=member('agent:Second',{},'done'))]))
            assert verify_team(team,run_team(team,base_dir=root))
    return dict(phase8_contracts=True,phase8_execution=docker,phase8_browser=browser,phase8_team=docker,live_api_calls=0,external_pilot_verified=False)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--docker',action='store_true');parser.add_argument('--browser',action='store_true')
    args=parser.parse_args();print(json.dumps(verify(args.docker,args.browser)))
