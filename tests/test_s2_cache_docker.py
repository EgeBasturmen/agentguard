"""Real Docker execution and HTTP acknowledgement, synthetic state only.

PYTEST_DONT_REWRITE: private leases and fixture credentials must not be echoed.
"""
import pytest
from test_phase7_workflow import setup_project
from test_phase7_queue import runner
from test_phase7_docker import prepare

pytestmark=pytest.mark.docker

@pytest.mark.parametrize('lost_ack',[False,True])
def test_real_docker_cached_evidence_does_not_trigger_reexecution(tmp_path,monkeypatch,lost_ack):
    from agentguard.management.http import running_server
    from agentguard.management.client import Client
    from agentguard.management.runner import PrivateRunner
    from agentguard.management.store import AccessError
    from agentguard.orchestration.controller import RunController
    service,owner,project=setup_project(tmp_path);source=tmp_path/'source';source.mkdir()
    (source/'agent.py').write_text("class Agent:\n def run(self,task,sandbox):\n  sandbox.call('file.write',{'filename':'note.txt','content':'done'})\n  return 'done'\n")
    _,suite=prepare(service,owner,project,source);worker=runner(service,owner,project)
    job=service.enqueue(owner,project,suite['id'],'cache-proof');invocations=[];original=RunController.run
    def observed(self,*a,**k):invocations.append(1);return original(self,*a,**k)
    monkeypatch.setattr(RunController,'run',observed)
    with running_server(service) as url:
        client=Client(url,worker['runner_token']);request=client.request;finishes=[]
        def wrapped(path,body=None):
            reply=request(path,body)
            if path.endswith('/finish'):
                finishes.append(1)
                if lost_ack:raise AccessError('RPC_TRANSPORT_UNVERIFIED',502)
            return reply
        client.request=wrapped
        private=PrivateRunner(client,source_root=source,cache_root=tmp_path/'cache')
        if lost_ack:
            with pytest.raises(AccessError):private.run_once()
        else:assert private.run_once()['gate_decision']=='ALLOW'
        restarted=PrivateRunner(Client(url,worker['runner_token']),source_root=source,cache_root=tmp_path/'cache')
        assert restarted.evidence.scan()[0]['state']==('pending_upload' if lost_ack else 'acknowledged')
        if lost_ack:
            with pytest.raises(AccessError,match='CACHE_RECOVERY_REQUIRED'):restarted.run_once()
            delivery=restarted.evidence.delivery(project,job['id'])
            assert delivery['report']['execution']['cases'][0]['result']['cleanup_status']=='verified'
        else:assert restarted.run_once() is None
        assert len(invocations)==1 and len(finishes)==1
    done=service.job(owner,project,job['id'])
    assert done['gate_decision']=='ALLOW' and done['cleanup_status']=='verified'
