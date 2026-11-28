"""Bounded real HTTP delivery, no agent retries. PYTEST_DONT_REWRITE."""
import pytest
from agentguard.management.delivery import RecoveryPolicy,DeliveryError,deliver_pending
from agentguard.management.cache import EvidenceCache,CacheError
from agentguard.management.client import Client
from agentguard.management.store import AccessError
from agentguard.management.http import running_server
from test_s2_dispatch import fixture,invalidate
from s2_transport_support import MeasuredClient,require
pytestmark=pytest.mark.integration

@pytest.fixture(autouse=True)
def private_fixture(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    secure_fixture_directory(tmp_path)

def policy():return RecoveryPolicy()

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
        client=MeasuredClient(url,w['runner_token']);limits=policy();terminal=None
        if mode in {'budget','auth','legacy'}:
            with pytest.raises(AccessError) as caught:deliver_pending(cache,client,p,j['id'],policy=limits)
            terminal=caught.value.code
            assert cache.scan()[0]['state']=='pending_upload'
            require(terminal=={'budget':'FINISH_DELIVERY_RETRY_EXHAUSTED','auth':'UNAUTHENTICATED','legacy':'CACHE_ACKNOWLEDGEMENT_UNVERIFIED'}[mode],mode=mode,terminal_code=terminal,rpc=client.measurements)
        else:
            try:reply=deliver_pending(cache,client,p,j['id'],policy=limits)
            except DeliveryError as error:
                with s.db() as db:accepted=db.execute('SELECT result_hash IS NOT NULL FROM jobs WHERE id=?',(j['id'],)).fetchone()[0]
                require(False,mode=mode,client_attempts=len(client.measurements),server_received=len(calls),server_accepted=bool(accepted),reported_attempts=error.attempts,category=error.category,total_budget=limits.total_seconds,request_deadline=limits.request_timeout,rpc=client.measurements)
            assert reply['gate_decision']=='BLOCK' and cache.scan()[0]['state']=='acknowledged'
        metrics=dict(mode=mode,client_attempts=len(client.measurements),server_received=len(calls),max_attempts=limits.max_attempts,terminal_code=terminal,rpc=client.measurements)
        required=1 if mode in {'normal','auth','legacy','budget'} else 2
        require(required<=len(calls)<=len(client.measurements)<=limits.max_attempts,**metrics)
        transient={'RPC_TRANSPORT_UNVERIFIED','OPERATION_FAILED_NO_RESULT_VERIFIED','REMOTE_OPERATION_FAILED','SERVICE_UNAVAILABLE','SERVER_BUSY','UPSTREAM_UNAVAILABLE','GATEWAY_TIMEOUT'}
        require(all(row['code'] in transient for row in client.measurements[:-1]),**metrics)
        if mode=='budget':require(caught.value.attempts==len(client.measurements),**metrics)
        else:require(client.measurements[-1]['code'] in {'HTTP_JSON_COMPLETE',terminal},**metrics)
    with s.db() as db:
        assert db.execute('SELECT count(*) FROM artifacts').fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM usage WHERE job=? AND kind='admitted_run'",(j['id'],)).fetchone()[0]==1

@pytest.mark.parametrize('value',[0,6,True])
def test_invalid_retry_attempts_fail_closed(value):
    with pytest.raises(ValueError):RecoveryPolicy(max_attempts=value)

def test_functional_cancel_fixture_uses_default_budget_after_cache_latency(tmp_path,monkeypatch):
    """Cache latency is not evidence that the real HTTP cancellation failed."""
    import sys
    module=sys.modules[__name__];original=module.deliver_pending;observed=[]
    def delayed(cache,client,project,job,*,policy):
        now=[0.0];reads=[];delivery=cache.delivery
        def inspect(*a,**kw):
            result=delivery(*a,**kw);reads.append(1)
            if len(reads)==2:now[0]+=3.0
            return result
        monkeypatch.setattr(cache,'delivery',inspect)
        try:return original(cache,client,project,job,policy=policy,clock=lambda:now[0])
        finally:observed.extend(client.measurements)
    monkeypatch.setattr(module,'deliver_pending',delayed)
    test_real_http_delivery_is_bounded_and_no_duplicate_effect(tmp_path,'cancel')
    defaults=RecoveryPolicy()
    require(len(observed)>=2 and all(row['deadline_seconds']==defaults.request_timeout for row in observed),client_attempts=len(observed),rpc=observed)
