import json
import pytest
from test_phase7_workflow import setup_project,runtime
from test_phase7_queue import runner

pytestmark=pytest.mark.docker

def prepare(service,token,project,root):
    from agentguard.execution.agent_spec import AgentLaunchSpec
    _,sha=AgentLaunchSpec(reference='agent:Agent',source_dir=root).sources()
    connection=service.connect(token,project,'Python',runtime(),artifact_digest=sha)
    generated=service.generate_cases(token,project,connection['id'],task='Write done',fixture={'files':{'note.txt':'draft'}},
        rules=[dict(id='goal',text='expect files.note\\.txt = "done"')])
    case=service.case(token,project,generated['cases'][0]['id'])
    service.review_case(token,project,case['id'],decisions={key:'confirmed' for key in case['oracle_checks']},fixture=True,classification=True)
    service.approve_case(token,project,case['id'],case['case']['fingerprint'])
    return connection,service.freeze_suite(token,project,'Release',[case['id']])

def test_http_private_runner_real_docker_good_bad_comparison_and_retest(tmp_path):
    from agentguard.management.http import running_server
    from agentguard.management.client import Client
    from agentguard.management.runner import PrivateRunner
    from agentguard.execution.agent_spec import AgentLaunchSpec
    service,token,project=setup_project(tmp_path)
    source=tmp_path/'source';source.mkdir();agent=source/'agent.py'
    bad="class Agent:\n    def run(self,task,sandbox):\n        sandbox.call('file.write',{'filename':'note.txt','content':'wrong'})\n        return 'done'\n"
    agent.write_text(bad)
    _,suite=prepare(service,token,project,source);worker=runner(service,token,project)
    with running_server(service) as url:
        runner_client=PrivateRunner(Client(url,worker['runner_token']),source_root=source,cache_root=tmp_path/'cache')
        first=service.enqueue(token,project,suite['id'],'bad')
        before=runner_client.run_once();assert before['gate_decision']=='BLOCK' and before['cleanup_status']=='verified'
        agent.write_text(bad.replace("'content':'wrong'","'content':'done'"))
        _,sha=AgentLaunchSpec(reference='agent:Agent',source_dir=source).sources()
        connection=service.connect(token,project,'Fixed',runtime(),artifact_digest=sha)
        fixed=service.bind_agent_version(token,project,suite['id'],connection['id'])
        second=service.enqueue(token,project,fixed['id'],'fixed')
        after=runner_client.run_once();assert after['gate_decision']=='ALLOW' and not after['authorizes_release']
        old=json.loads((tmp_path/'cache'/project/(first['id']+'.json')).read_text())
        new=json.loads((tmp_path/'cache'/project/(second['id']+'.json')).read_text())
        comparison=service.compare_reports(token,project,baseline_job=first['id'],candidate_job=second['id'],baseline_report=old,candidate_report=new)
        # Phase 6 requires a passing baseline; fixing a failed run is not a release-baseline promotion.
        assert comparison['decision']['gate_decision']=='BLOCK'
        reverse=service.compare_reports(token,project,baseline_job=second['id'],candidate_job=first['id'],baseline_report=new,candidate_report=old)
        assert reverse['decision']['gate_decision']=='BLOCK'
        third=service.enqueue(token,project,fixed['id'],'repeat-good');assert runner_client.run_once()['gate_decision']=='ALLOW'
        repeated=json.loads((tmp_path/'cache'/project/(third['id']+'.json')).read_text())
        healthy=service.compare_reports(token,project,baseline_job=second['id'],candidate_job=third['id'],baseline_report=new,candidate_report=repeated)
        assert healthy['decision']['gate_decision']=='ALLOW'
        ticket=service.feedback(token,project,category='bug',title='Wrong file content',body='Fixture-based reproduction',job_id=first['id'])
        for status in ['investigating','fix_ready','retested','closed']:
            service.transition_feedback(token,project,ticket['id'],status,note='Technical fixture only',baseline_job=first['id'],candidate_job=second['id'])
        assert not service.pilot_readiness(token,project)['external_pilot_verified']

@pytest.mark.parametrize('mode',['normal','error'])
def test_private_runner_gateway_budget_reconciliation_and_unknown_reservation(tmp_path,mode):
    from phase5_service import ModelService,model_config
    from agentguard.management.http import running_server
    from agentguard.management.client import Client
    from agentguard.management.runner import PrivateRunner
    from agentguard.management.service import AccessError
    from agentguard.execution.agent_spec import AgentLaunchSpec
    with ModelService(mode) as provider:
        service,token,project=setup_project(tmp_path);source=tmp_path/'source';source.mkdir()
        (source/'agent.py').write_text("class Agent:\n    def run(self,task,sandbox):\n        reply=sandbox.call('model.generate',{'messages':[{'role':'user','content':task}],'max_output_tokens':32})\n        sandbox.call('file.write',{'filename':'note.txt','content':'done'})\n        return reply\n")
        service.operator_model_budget(project,max_tokens=2500,max_cost_micro_usd=15000,operator='operator',reason='Offline HTTP protocol fixture; no paid provider')
        _,sha=AgentLaunchSpec(reference='agent:Agent',source_dir=source).sources();value=runtime();value['policy']['allowed_tools'].append('model.generate')
        value['model']=model_config(provider,max_calls=1,max_total_tokens=2000,max_cost_micro_usd=10000)
        connection=service.connect(token,project,'Gateway',value,artifact_digest=sha)
        batch=service.generate_cases(token,project,connection['id'],task='Write done',fixture={'files':{'note.txt':'draft'}},rules=[dict(id='goal',text='expect files.note\\.txt = "done"')])
        case=service.case(token,project,batch['cases'][0]['id'])
        service.review_case(token,project,case['id'],decisions={k:'confirmed' for k in case['oracle_checks']},fixture=True,classification=True)
        service.approve_case(token,project,case['id'],case['case']['fingerprint']);suite=service.freeze_suite(token,project,'Gateway',[case['id']])
        worker=runner(service,token,project);service.enqueue(token,project,suite['id'],'model')
        with running_server(service) as url:
            result=PrivateRunner(Client(url,worker['runner_token']),source_root=source,cache_root=tmp_path/'cache',allow_external=True,allow_live_models=True).run_once()
        assert len(provider.calls)==1 and result['cleanup_status']=='verified'
        usage=service.usage(token,project)
        if mode=='normal':
            assert result['gate_decision']=='ALLOW' and usage['model_tokens']==30 and usage['reserved_model_tokens']==0
        else:
            assert result['gate_decision']=='BLOCK' and usage['model_tokens'] is None and usage['reserved_model_tokens']==2000
            with pytest.raises(AccessError):service.enqueue(token,project,suite['id'],'no-unlimited-retry')

def test_private_runner_api_cancellation_stops_real_container_and_verifies_cleanup(tmp_path):
    import subprocess
    import threading
    import time
    from agentguard.management.http import running_server
    from agentguard.management.client import Client
    from agentguard.management.runner import PrivateRunner
    service,token,project=setup_project(tmp_path);source=tmp_path/'source';source.mkdir()
    (source/'agent.py').write_text("import time\nclass Agent:\n    def run(self,task,sandbox):\n        time.sleep(20)\n        sandbox.call('file.write',{'filename':'note.txt','content':'done'})\n")
    _,suite=prepare(service,token,project,source);worker=runner(service,token,project)
    def containers():return set(subprocess.check_output(['docker','ps','--filter','name=agentguard-run-','--format','{{.ID}}'],text=True).split())
    before=containers();results=[];errors=[]
    with running_server(service) as url:
        private=PrivateRunner(Client(url,worker['runner_token']),source_root=source,cache_root=tmp_path/'cache')
        job=service.enqueue(token,project,suite['id'],'cancel')
        def run():
            try:results.append(private.run_once())
            except Exception as error:errors.append(type(error).__name__)
        thread=threading.Thread(target=run,daemon=True);thread.start();deadline=time.monotonic()+20
        while time.monotonic()<deadline and not (containers()-before):time.sleep(.1)
        assert containers()-before,'Expected an actually running Docker container before cancellation'
        Client(url,token).request('/api/projects/'+project+'/runs/'+job['id']+'/cancel',{})
        thread.join(timeout=20)
        assert not thread.is_alive() and not errors
        assert results[0]['gate_decision']=='BLOCK' and results[0]['cleanup_status']=='verified'
        assert not (containers()-before)

def test_private_runner_management_key_never_enters_agent_environment(tmp_path,monkeypatch):
    from agentguard.management.http import running_server
    from agentguard.management.client import Client
    from agentguard.management.runner import PrivateRunner
    service,token,project=setup_project(tmp_path);source=tmp_path/'source';source.mkdir()
    (source/'agent.py').write_text("import os\nclass Agent:\n    def run(self,task,sandbox):\n        value='done' if os.environ.get('AGENTGUARD_RUNNER_TOKEN') is None else 'leaked'\n        sandbox.call('file.write',{'filename':'note.txt','content':value})\n")
    _,suite=prepare(service,token,project,source);worker=runner(service,token,project)
    monkeypatch.setenv('AGENTGUARD_RUNNER_TOKEN',worker['runner_token'])
    service.enqueue(token,project,suite['id'],'credential-isolation')
    with running_server(service) as url:
        result=PrivateRunner(Client(url,worker['runner_token']),source_root=source,cache_root=tmp_path/'cache').run_once()
    assert result['gate_decision']=='ALLOW' and result['cleanup_status']=='verified'
