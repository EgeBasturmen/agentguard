import copy
import pytest
from agentguard.integrations.sdk import contract_test

@pytest.mark.integration
@pytest.mark.parametrize('kind,key',[('database','tables'),('inbox','messages'),('calendar','events')])
def test_sqlite_lifecycle_real_persistence_and_namespace(tmp_path,kind,key):
    from agentguard.environments.services import RecordEnvironment
    fixture={key:{'items':{'r1':{'value':'draft'}}}} if kind=='database' else {key:{'r1':{'value':'draft'}}}
    env=RecordEnvironment(kind=kind);other=RecordEnvironment(kind=kind)
    assert contract_test(env,fixture)
    env=RecordEnvironment(kind=kind);env.provision(fixture);other.provision(fixture)
    try:
        args=dict(id='r1',value={'value':'done'})
        if kind=='database':args['table']='items'
        env.invoke(kind+'.put',args)
        assert env.snapshot()!=fixture and other.snapshot()==fixture
        assert env.clone().snapshot()==env.snapshot()
        with pytest.raises((ValueError,PermissionError)):env.invoke(kind+'.sql',{'query':'ATTACH DATABASE'})
    finally:assert env.cleanup()==other.cleanup()=='verified'

@pytest.mark.integration
def test_clock_due_order_cancel_reset_and_unobserved_window():
    from agentguard.environments.clock import ClockEnvironment
    fixture=dict(clock=0,values={},jobs={},history=[])
    env=ClockEnvironment();assert contract_test(env,fixture)
    env=ClockEnvironment();env.provision(fixture)
    try:
        env.invoke('job.schedule',dict(id='a',due=2,key='x',value='first'))
        env.invoke('job.schedule',dict(id='b',due=2,key='x',value='second'))
        env.invoke('job.schedule',dict(id='c',due=5,key='x',value='unseen'))
        env.invoke('clock.advance',dict(to=2))
        state=env.snapshot();assert state['values']['x']=='second' and len(state['history'])==2
        assert env.observation()['pending']==['c'] and env.observation()['observed_until']==2
        with pytest.raises(ValueError):env.invoke('clock.advance',dict(to=1))
        with pytest.raises((ValueError,PermissionError)):env.invoke('job.schedule',dict(id='../escape',due=3,key='x',value='bad'))
        env.invoke('job.cancel',dict(id='c'));env.reset(fixture);assert env.snapshot()==fixture
    finally:assert env.cleanup()=='verified'

@pytest.mark.docker
def test_browser_contract_real_dom_and_no_arbitrary_navigation():
    from agentguard.environments.browser import BrowserEnvironment
    fixture={'browser':{'page':'home','pages':{'home':{'fields':{'query':''},'text':'Fixture','submitted':False}}}}
    env=BrowserEnvironment();env.provision(fixture)
    try:
        assert env.snapshot()==fixture
        env.invoke('browser.fill',dict(field='query',value='<script>not executable</script>'))
        env.invoke('browser.submit',{})
        state=env.snapshot()['browser']['pages']['home']
        assert state['submitted'] and state['fields']['query']=='<script>not executable</script>'
        with pytest.raises((ValueError,PermissionError)):env.invoke('browser.open',dict(page='http://169.254.169.254'))
        assert env.clone().snapshot()==env.snapshot()
        env.reset(fixture);assert env.snapshot()==fixture
    finally:assert env.cleanup()=='verified'

@pytest.mark.docker
def test_browser_rpc_unauthenticated_and_attempts_are_independent():
    import json
    from agentguard.environments.browser import BrowserEnvironment
    from agentguard.integrations.http import Endpoint,request_json
    fixture={'browser':{'page':'home','pages':{'home':{'fields':{},'text':'Fixture','submitted':False}}}}
    first=BrowserEnvironment();second=BrowserEnvironment();first.provision(fixture);second.provision(fixture)
    try:
        port=json.loads(first._path('ready.json').read_text())['port']
        endpoint=Endpoint('http://127.0.0.1:'+str(port),allow_http=True,private_hosts=frozenset({'127.0.0.1'}))
        with pytest.raises(RuntimeError,match='HTTP status 400'):request_json(endpoint,'POST','/',dict(action='browser.submit',value={}))
        assert first.snapshot()==fixture
        first.invoke('browser.submit',{});assert second.snapshot()==fixture
    finally:assert first.cleanup()==second.cleanup()=='verified'
