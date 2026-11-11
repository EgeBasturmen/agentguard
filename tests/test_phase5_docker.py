"""Phase 5 real Docker worker/gateway/session/fault/coding acceptance tests."""
import pytest
from agentguard.core.outcome import effective_pass
from agentguard.integrations.plan import IntegrationPlan,run_integration_plan
from phase5_service import ModelService,model_config

pytestmark=pytest.mark.docker

def plan(tmp_path,source,scenario,*,model=None,tools=None,allowed=()):
    (tmp_path/'agent.py').write_text(source,encoding='utf-8')
    data=dict(schema_version=1,scenario=scenario,agent=dict(transport='python',reference='agent:Agent',source_dir='.'),
        environment=dict(kind='memory'),tools=tools or [],policy=dict(allowed_tools=list(allowed)))
    if model is not None:data['model']=model
    return IntegrationPlan.model_validate(data)

def test_multi_turn_agent_instance_persists_and_next_run_resets(tmp_path):
    p=plan(tmp_path,"class Agent:\n    def __init__(self):self.count=0\n    def run(self,task,sandbox):\n        self.count+=1\n        return str(self.count)+':'+task\n",
        dict(name='session',task='session',initial_state={},turns=[dict(task='first'),dict(task='second')],
            evaluations=[dict(id='one',kind='turn_output',config={'turn':1,'text':'1:first'}),dict(id='two',kind='turn_output',config={'turn':2,'text':'2:second'})]))
    first=run_integration_plan(p,base_dir=tmp_path);second=run_integration_plan(p,base_dir=tmp_path)
    assert effective_pass(first),first.model_dump()
    assert effective_pass(second),second.model_dump()
    assert first.turn_outputs==second.turn_outputs==['1:first','2:second']
    assert first.run_manifest.container_id!=second.run_manifest.container_id


def test_sync_agent_can_manage_its_own_asyncio_loop_as_before_phase5(tmp_path):
    source="import asyncio\nclass Agent:\n    def run(self,task,sandbox):\n        async def work():return 'ready'\n        return asyncio.run(work())\n"
    p=plan(tmp_path,source,dict(name='sync-compatibility',task='Return ready',initial_state={},evaluations=[dict(id='output',kind='output_equals',config={'text':'ready'})]))
    result=run_integration_plan(p,base_dir=tmp_path)
    assert effective_pass(result),result.model_dump()

def test_gateway_executes_on_host_via_broker_with_real_http_and_usage(tmp_path):
    with ModelService() as service:
        p=plan(tmp_path,"class Agent:\n    def run(self,task,sandbox):\n        return sandbox.call('model.generate',{'messages':[{'role':'user','content':task}],'max_output_tokens':32})\n",
            dict(name='model',task='Return ready',initial_state={},evaluations=[dict(id='text',kind='output_equals',config={'text':'ready'})]),
            model=model_config(service),allowed=['model.generate'])
        result=run_integration_plan(p,base_dir=tmp_path)
        assert effective_pass(result),result.model_dump()
        assert len(service.calls)==1 and result.model_evidence['calls'][0]['input_tokens']==20
        assert result.audit[0].tool_name=='model.generate'
        assert result.cleanup_status=='verified'
        original=result.audit[0].arguments['messages'][0]['content']
        result.audit[0].arguments['messages'][0]['content']='forged prompt'
        assert not effective_pass(result),'Usage must bind the actual controller model request'
        result.audit[0].arguments['messages'][0]['content']=original
        result.model_evidence=None
        assert not effective_pass(result),'Dropping measured model usage must not retain PASS'

@pytest.mark.parametrize('fault_kind',['failure','timeout'])
def test_injected_tool_failure_requires_real_recovery_and_effect_evidence(tmp_path,fault_kind):
    p=plan(tmp_path,"class Agent:\n    def run(self,task,sandbox):\n        try:sandbox.call('file.write',{'filename':'note.txt','content':'ready'})\n        except RuntimeError:pass\n        sandbox.call('file.write',{'filename':'note.txt','content':'ready'})\n        return 'ready'\n",
        dict(name='recovery',task='Write note',initial_state={'files':{'note.txt':'draft'}},expected_state=[dict(path=r'files.note\.txt',value='ready')],
            faults=[dict(id='transient',tool_name='file.write',call_index=1,kind=fault_kind)],evaluations=[dict(id='recovered',kind='fault_recovery',config={'fault_id':'transient'})]),
        tools=[dict(connector='environment')],allowed=['file.write'])
    data=p.model_dump();data['environment']={'kind':'filesystem'}
    result=run_integration_plan(IntegrationPlan.model_validate(data),base_dir=tmp_path)
    assert effective_pass(result),result.model_dump()
    assert [e.status for e in result.audit]==['error','completed']
    assert result.audit[0].effect_status=='none' and result.audit[1].effect_status=='observed'
    assert result.audit[0].reason=='INJECTED_'+fault_kind.upper()
    result.audit[0].status='denied'
    assert not effective_pass(result)


def test_judge_pass_cannot_override_a_failed_independent_state_check(tmp_path):
    with ModelService() as service:
        judge=dict(id='quality',kind='judge',config=dict(rubric='Reject wrong responses; accept correct responses',rubric_version='1',labels=[dict(input='correct',expected=True,source='human:fixture'),dict(input='wrong',expected=False,source='human:fixture')]))
        p=plan(tmp_path,"class Agent:\n    def run(self,task,sandbox):return 'ready'\n",
            dict(name='judge',task='Set value to 1',initial_state={'value':0},expected_state=[dict(path='value',value=1)],evaluations=[judge]),model=model_config(service))
        result=run_integration_plan(p,base_dir=tmp_path)
        assert result.evaluations[0].status=='pass',result.model_dump()
        assert not effective_pass(result) and len(service.calls)==3


def test_document_retrieval_and_extractive_grounding_are_really_observed(tmp_path):
    source="import json\nclass Agent:\n    def run(self,task,sandbox):\n        doc=sandbox.call('document.get',{'document_id':'doc1'})\n        return json.dumps({'answer':doc['content'],'citations':[{'document_id':'doc1','quote':doc['content']}]})\n"
    p=plan(tmp_path,source,dict(name='rag',task='Quote the source',initial_state={'documents':{'doc1':{'content':'Verified independent material'}}},
        evaluations=[dict(id='source',kind='rag_sources',config={'required_documents':['doc1']}),dict(id='grounded',kind='rag_grounded',config={'required_documents':['doc1']})]),
        tools=[dict(connector='environment')],allowed=['document.get'])
    data=p.model_dump();data['environment']={'kind':'dataset'}
    result=run_integration_plan(IntegrationPlan.model_validate(data),base_dir=tmp_path)
    assert effective_pass(result),result.model_dump()
    assert result.evaluations[1].details['assurance']=='exact-extractive'


@pytest.mark.parametrize('correct',[True,False])
def test_coding_evaluator_executes_observed_program_in_separate_container(tmp_path,correct):
    import hashlib
    program="import sys\nprint(int(sys.argv[1])+int(sys.argv[2]))\n" if correct else "print('all tests passed')\n"
    source="class Agent:\n    def run(self,task,sandbox):\n        sandbox.call('file.write',{'filename':'main.py','content':"+repr(program)+"})\n        return 'all tests passed'\n"
    p=plan(tmp_path,source,dict(name='coding',task='Implement a sum CLI',initial_state={'files':{}},evaluations=[
        dict(id='files',kind='coding_files',config={'sha256':{'main.py':hashlib.sha256(program.encode()).hexdigest()}}),
        dict(id='tests',kind='coding_tests',config={'entrypoint':'main.py','cases':[{'arguments':['2','3'],'expected_stdout':'5'},{'arguments':['-1','1'],'expected_stdout':'0'}],'timeout_seconds':60})]),tools=[dict(connector='environment')],allowed=['file.write'])
    data=p.model_dump();data['environment']={'kind':'filesystem'};data['policy']['timeout_seconds']=90
    result=run_integration_plan(IntegrationPlan.model_validate(data),base_dir=tmp_path)
    assert effective_pass(result)==correct,result.model_dump()
    assert result.evaluations[1].details['cleanup_status']=='verified'
    assert all(case['container_id']!=result.run_manifest.container_id for case in result.evaluations[1].details['cases'])


def test_exhausted_gateway_budget_is_not_pass_even_if_agent_claims_success(tmp_path):
    with ModelService() as service:
        source="class Agent:\n    def run(self,task,sandbox):\n        try:sandbox.call('model.generate',{'messages':[{'role':'user','content':task}],'max_output_tokens':32})\n        except RuntimeError:pass\n        return 'ready'\n"
        p=plan(tmp_path,source,dict(name='budget',task='Return ready',initial_state={},evaluations=[dict(id='output',kind='output_equals',config={'text':'ready'})]),
            model=model_config(service,max_total_tokens=1),allowed=['model.generate'])
        result=run_integration_plan(p,base_dir=tmp_path)
        assert not effective_pass(result)
        assert result.model_evidence['status']=='blocked' and not service.calls


def test_gemini_live_check_harness_is_verified_offline_without_calling_gemini(tmp_path,monkeypatch):
    """Actual Docker smoke harness; adapter fixture explicitly does NOT verify live LLM."""
    import json
    from agentguard.model_gateway.gateway import ModelGateway,ProviderResult
    from scripts.check_gemini import verify
    calls=[]
    class FixtureProvider:
        def generate(self,config,messages,max_output_tokens,**kwargs):
            calls.append(messages)
            prompt=messages[-1]['content']
            try:untrusted=json.loads(prompt)['untrusted_response']
            except (ValueError,KeyError):text='{"status":"ready"}'
            else:text=json.dumps(dict(passed=untrusted!='2+2=5',confidence=0.99,reason='offline harness fixture'))
            return ProviderResult(text,20,10,'offline-harness-fixture')
    original=ModelGateway.__init__
    def initialize(self,config,**kwargs):original(self,config,providers={'gemini':FixtureProvider()},**kwargs)
    monkeypatch.setattr(ModelGateway,'__init__',initialize)
    monkeypatch.setenv('GEMINI_API_KEY','offline-harness-dummy')
    result=verify('gemini-3.5-flash-lite')
    assert result['status']=='passed',result
    assert len(calls)==5 and result['budget_denied_before_network']


def test_gemini_structured_http_error_survives_worker_broker_and_safe_report(monkeypatch):
    """Real controlled worker; the HTTP error is an offline fixture, not Gemini."""
    import json
    import agentguard.model_gateway.gateway as module
    from scripts.check_gemini import verify
    secret='offline-secret-response-body'
    calls=[]
    def reject(*args,**kwargs):
        calls.append(1)
        raise module.GeminiHTTPError.from_response(400,json.dumps({'error':{'code':400,
            'status':'INVALID_ARGUMENT','message':secret,'details':[{
                '@type':'type.googleapis.com/google.rpc.ErrorInfo','reason':'API_KEY_INVALID',
                'metadata':{'consumer':secret}}]}}).encode())
    monkeypatch.setenv('GEMINI_API_KEY',secret)
    monkeypatch.setattr(module,'request_json',reject)
    result=verify('gemini-3.5-flash-lite',single_agent=True)
    assert result['status']=='incomplete' and result['phase5_complete'] is False
    assert result['stage']=='first_agent' and result['diagnostic']['category']=='credentials'
    assert result['diagnostic']['provider_reasons']==['API_KEY_INVALID']
    assert result['runs'][0]['cleanup_status']=='verified'
    assert result['runs'][0]['status']=='error'
    assert result['gateways'][0]['status']=='unverified' and len(calls)==1
    assert result['actual_api_calls'] is None and secret not in json.dumps(result)
