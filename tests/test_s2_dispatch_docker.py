"""Real HTTP/Docker dispatch, with production controller calls counted, not mocked.

PYTEST_DONT_REWRITE: do not echo generated credentials or lease values on failure.
"""
import json
import pytest
from test_phase7_workflow import setup_project
from test_phase7_queue import runner
from test_phase7_docker import prepare

pytestmark=pytest.mark.docker

@pytest.mark.parametrize('mode',['valid','retire_before_claim','cancel_after_claim','retire_after_claim','revoke_after_claim','source_drift'])
def test_real_agent_only_runs_after_valid_start_fence(tmp_path,monkeypatch,mode):
    from agentguard.management.http import running_server
    from agentguard.management.client import Client
    from agentguard.management.runner import PrivateRunner
    from agentguard.orchestration.controller import RunController
    service,owner,project=setup_project(tmp_path);source=tmp_path/'source';source.mkdir()
    agent=source/'agent.py'
    agent.write_text("class Agent:\n    def run(self,task,sandbox):\n        sandbox.call('file.write',{'filename':'note.txt','content':'done'})\n        return 'done'\n")
    _,suite=prepare(service,owner,project,source);worker=runner(service,owner,project)
    case=next(c for c in service.objects(owner,project,'case') if c['status']=='approved')
    job=service.enqueue(owner,project,suite['id'],'synthetic-dispatch')
    invocations=[];original_run=RunController.run
    def observed(self,*args,**kwargs):invocations.append(1);return original_run(self,*args,**kwargs)
    monkeypatch.setattr(RunController,'run',observed)
    if mode=='retire_before_claim':service.retire_case(owner,project,case['id'])
    original_claim=service.claim
    def claim(token):
        value=original_claim(token)
        if mode=='cancel_after_claim':service.cancel(owner,project,job['id'])
        elif mode=='retire_after_claim':service.retire_case(owner,project,case['id'])
        elif mode=='revoke_after_claim':service.revoke_runner(owner,project,worker['id'])
        elif mode=='source_drift':agent.write_text('raise RuntimeError("Host import forbidden")\n')
        return value
    service.claim=claim
    with running_server(service) as url:
        result=PrivateRunner(Client(url,worker['runner_token']),source_root=source,cache_root=tmp_path/'cache').run_once()
    view=service.job(owner,project,job['id'])
    if mode=='valid':
        assert len(invocations)==1 and result['gate_decision']=='ALLOW' and view['cleanup_status']=='verified'
        report=json.loads((tmp_path/'cache'/project/(job['id']+'.json')).read_text())
        run=report['execution']['cases'][0]['result']
        assert any(e['tool_name']=='file.write' and e['status']=='completed' for e in run['audit'])
    else:
        assert len(invocations)==0 and view['gate_decision']=='BLOCK'
        if result is not None:assert result['gate_decision']=='BLOCK'
