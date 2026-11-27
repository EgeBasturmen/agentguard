"""Bounded real HTTP delivery, no agent retries. PYTEST_DONT_REWRITE."""
import pytest
from agentguard.management.delivery import RecoveryPolicy,deliver_pending
from agentguard.management.cache import EvidenceCache,CacheError
from agentguard.management.client import Client
from agentguard.management.store import AccessError
from agentguard.management.http import running_server
from test_s2_dispatch import fixture,invalidate
pytestmark=pytest.mark.integration

@pytest.fixture(autouse=True)
def private_fixture(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    secure_fixture_directory(tmp_path)

def policy():return RecoveryPolicy(max_attempts=3,total_seconds=2,request_timeout=.3,base_delay=.01,max_delay=.02)

@pytest.mark.parametrize('mode',['normal','before_commit','after_commit','unavailable','restart','budget','auth','legacy','cancel','withdraw'])
def test_real_http_delivery_is_bounded_and_no_duplicate_effect(tmp_path,mode):
    from agentguard.management.service import ControlPlane
    from agentguard.management.client import RPCTransportError
    s,o,p,c,suite,w,j,case=fixture(tmp_path);claim=s.claim(w['runner_token']);report={'failure':'RUNNER_EXECUTION_ERROR'}
    calls=[];original=s.finish
    def finish(*a,**kw):
        calls.append(1)
        if mode=='budget':raise AccessError('SERVICE_UNAVAILABLE',503)
        if mode=='auth':raise AccessError('UNAUTHENTICATED',401)
        if len(calls)==1:
            if mode=='before_commit':raise ConnectionResetError()
            if mode=='unavailable':raise AccessError('SERVICE_UNAVAILABLE',503)
            if mode=='cancel':s.cancel(o,p,j['id']);raise ConnectionResetError()
            if mode=='withdraw':s.retire_case(o,p,case['id']);raise ConnectionResetError()
            if mode=='restart':
                other=ControlPlane(s.root);reply=other.finish(*a,**kw);raise ConnectionResetError()
            if mode=='after_commit':original(*a,**kw);raise ConnectionResetError()
        reply=original(*a,**kw)
        if mode=='legacy':reply.pop('delivery_accepted',None);reply.pop('report_digest',None)
        return reply
    s.finish=finish
    with running_server(s) as url:
        cache=EvidenceCache(tmp_path/'cache',server=url,credential=w['runner_token']);cache.begin(claim);cache.complete(claim,report)
        client=Client(url,w['runner_token'])
        if mode in {'budget','auth','legacy'}:
            with pytest.raises(AccessError):deliver_pending(cache,client,p,j['id'],policy=policy())
            assert cache.scan()[0]['state']=='pending_upload'
        else:
            reply=deliver_pending(cache,client,p,j['id'],policy=policy())
            assert reply['gate_decision']=='BLOCK' and cache.scan()[0]['state']=='acknowledged'
        assert len(calls)==(3 if mode=='budget' else 1 if mode in {'normal','auth','legacy'} else 2)
    with s.db() as db:
        assert db.execute('SELECT count(*) FROM artifacts').fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM usage WHERE job=? AND kind='admitted_run'",(j['id'],)).fetchone()[0]==1

@pytest.mark.parametrize('value',[0,6,True])
def test_invalid_retry_attempts_fail_closed(value):
    with pytest.raises(ValueError):RecoveryPolicy(max_attempts=value)
