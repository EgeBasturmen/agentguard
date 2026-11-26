"""Real HTTP runner delivery/restart tests. PYTEST_DONT_REWRITE: synthetic private state."""
import json
import pytest
from agentguard.management.runner import PrivateRunner
from agentguard.management.client import Client
from agentguard.management.store import AccessError

pytestmark=pytest.mark.integration

@pytest.fixture(autouse=True)
def private_fixture(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    secure_fixture_directory(tmp_path)

def test_legacy_unacknowledged_evidence_is_never_silently_purged(tmp_path):
    from agentguard.management import credentials as secure
    root=tmp_path/'cache';secure._parent(root)
    marker=root/'.agentguard-runner-cache';marker.write_text('private-runner-cache-v1')
    folder=root/('b'*32);secure._parent(folder)
    evidence=folder/('a'*32+'.json');evidence.write_text('{"failure":"RUNNER_EXECUTION_ERROR"}')
    meta=folder/('a'*32+'.meta.json');meta.write_text('{"expires":0}')
    import os
    if os.name=='nt':
        for path in (marker,evidence,meta):secure._windows_security(path,'harden')
    else:
        for path in (marker,evidence,meta):path.chmod(0o600)
    runner=PrivateRunner(Client('http://127.0.0.1:8766','ag7r_'+'s'*43),source_root=tmp_path,cache_root=root)
    runner.purge_cache()
    assert evidence.exists() and meta.exists()

@pytest.mark.parametrize('failure',['lost_response','invalid_receipt'])
def test_real_http_pending_restart_never_reexecutes_or_retries(tmp_path,monkeypatch,failure):
    from test_phase7_workflow import setup_project,approved_suite
    from test_phase7_queue import runner as enroll
    from agentguard.management.http import running_server
    import agentguard.management.runner as module
    service,owner,project=setup_project(tmp_path);_,suite=approved_suite(service,owner,project)
    worker=enroll(service,owner,project);job=service.enqueue(owner,project,suite['id'],'cache-restart')
    invoked=[]
    def execute(*a,**k):invoked.append(1);return {'failure':'RUNNER_EXECUTION_ERROR'}
    monkeypatch.setattr(module,'execute_spec',execute)
    with running_server(service) as url:
        client=Client(url,worker['runner_token']);request=client.request;finishes=[]
        def wrapped(path,body=None):
            result=request(path,body)
            if path.endswith('/finish'):
                finishes.append(1)
                if failure=='lost_response':raise AccessError('RPC_TRANSPORT_UNVERIFIED',502)
                result.pop('report_digest',None)
            return result
        client.request=wrapped
        private=PrivateRunner(client,source_root=tmp_path,cache_root=tmp_path/'cache')
        with pytest.raises(AccessError):private.run_once()
        restarted=PrivateRunner(Client(url,worker['runner_token']),source_root=tmp_path,cache_root=tmp_path/'cache')
        with pytest.raises(AccessError,match='CACHE_RECOVERY_REQUIRED'):restarted.run_once()
        assert len(invoked)==1 and len(finishes)==1
        assert restarted.evidence.scan()[0]['state']=='pending_upload'
    assert service.job(owner,project,job['id'])['gate_decision']=='BLOCK'

def test_deadline_during_intent_publication_prevents_execution(tmp_path,monkeypatch):
    from types import SimpleNamespace
    import agentguard.management.runner as module
    now=[1.0];invocations=[];calls=[]
    q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',max_seconds=30,spec={'retention_days':1})
    class LocalClient:
        url='http://127.0.0.1:8766';token='ag7r_'+'s'*43
        def request(self,path,body=None):
            calls.append(path)
            if path.endswith('/claim'):return q
            if path.endswith('/heartbeat'):return dict(cancel=False,lease_seconds=30)
            raise AssertionError('Finish must not be attempted')
    p=PrivateRunner(LocalClient(),source_root=tmp_path,cache_root=tmp_path/'cache')
    begin=p.evidence.begin
    def delayed(claim):begin(claim);now[0]=40.0
    monkeypatch.setattr(p.evidence,'begin',delayed)
    monkeypatch.setattr(module,'time',SimpleNamespace(monotonic=lambda:now[0]))
    monkeypatch.setattr(module,'execute_spec',lambda *a,**k:invocations.append(1))
    assert p.run_once()['gate_decision']=='BLOCK'
    assert not invocations and not any(x.endswith('/finish') for x in calls)
    assert p.evidence.scan()[0]['state']=='incomplete'

def test_real_http_acceptance_can_be_recorded_after_local_restart(tmp_path):
    from test_phase7_results import execute_http_job
    from agentguard.management.http import running_server
    from agentguard.management.cache import EvidenceCache
    service,owner,project,worker,job,claim,report=execute_http_job(tmp_path)
    with running_server(service) as url:
        c=EvidenceCache(tmp_path/'cache',server=url,credential=worker['runner_token'])
        c.begin(claim);c.complete(claim,report)
        reply=Client(url,worker['runner_token']).request('/api/runner/finish',dict(job_id=job['id'],lease=claim['lease'],report=report))
        restarted=EvidenceCache(tmp_path/'cache',server=url,credential=worker['runner_token'])
        delivery=restarted.delivery(project,job['id'])
        restarted.acknowledge_delivery(delivery,reply)
        assert restarted.scan()[0]['state']=='acknowledged'
