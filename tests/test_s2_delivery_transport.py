"""Actual HTTP timeout/reset/protocol boundaries. PYTEST_DONT_REWRITE."""
import json,socket,threading,time
import pytest
from agentguard.management.client import Client,RPCTransportError,RPCRemoteError
from agentguard.management.cache import EvidenceCache
from agentguard.management.delivery import RecoveryPolicy,DeliveryError,deliver_pending
from agentguard.management.store import AccessError
from s2_transport_support import endpoint,MeasuredClient,require,DeadlineControl,tls_stall,tls_fixture
pytestmark=pytest.mark.integration
TOKEN='ag7r_'+'s'*43

@pytest.fixture(autouse=True)
def private_fixture(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    secure_fixture_directory(tmp_path)

@pytest.mark.parametrize('mode,attempts',[('reset',2),('timeout',2),('trickle',2),('trickle_headers',2),('truncated',2),('server',2),('auth',1),('forbidden',1),('integrity',1),('json',1),('bad_error',1),('legacy',1)])
def test_only_transient_transport_is_retried(tmp_path,mode,attempts):
    with endpoint(mode) as (url,calls):
        c=EvidenceCache(tmp_path/'cache',server=url,credential=TOKEN)
        q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1});c.begin(q);c.complete(q,{'failure':'RUNNER_EXECUTION_ERROR'})
        client=MeasuredClient(url,TOKEN);policy=RecoveryPolicy(max_attempts=2)
        started=time.monotonic()
        with pytest.raises(AccessError) as caught:deliver_pending(c,client,q['project'],q['id'],policy=policy)
        metrics=dict(mode=mode,client_attempts=len(client.measurements),server_received=len(calls),elapsed_seconds=time.monotonic()-started,terminal_code=caught.value.code,rpc=client.measurements)
        require(len(client.measurements)==attempts,**metrics)
        require(len(calls)==attempts,**metrics)
        expected='RPC_TRANSPORT_UNVERIFIED' if mode in {'reset','timeout','trickle','trickle_headers','truncated'} else 'SERVICE_UNAVAILABLE' if mode=='server' else 'UNAUTHENTICATED' if mode=='auth' else 'FORBIDDEN' if mode=='forbidden' else 'STORED_RESULT_INTEGRITY_ERROR' if mode=='integrity' else 'RPC_PROTOCOL_UNVERIFIED' if mode in {'json','bad_error'} else 'HTTP_JSON_COMPLETE'
        require(all(m['code']==expected for m in client.measurements),**metrics)
        require(all(m['elapsed_seconds']<=policy.request_timeout+1 for m in client.measurements),**metrics)
        require(c.scan()[0]['state']=='pending_upload',**metrics)
        if attempts==2:require(isinstance(caught.value,DeliveryError) and caught.value.attempts==2,**metrics)

def test_backoff_jitter_and_total_budget_are_bounded(tmp_path):
    from agentguard.management.client import RPCTransportError
    c=EvidenceCache(tmp_path/'cache',server='http://127.0.0.1:8766',credential=TOKEN)
    q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1});c.begin(q);c.complete(q,{'failure':'RUNNER_EXECUTION_ERROR'})
    now=[0.0];pauses=[];calls=[]
    class Transport:
        url='http://127.0.0.1:8766';token=TOKEN
        def request(self,path,body,*,timeout,expected_owner=None):calls.append(timeout);raise RPCTransportError()
    def sleep(value):pauses.append(value);now[0]+=value
    with pytest.raises(DeliveryError) as caught:deliver_pending(c,Transport(),q['project'],q['id'],policy=RecoveryPolicy(max_attempts=4,total_seconds=1,request_timeout=.2,base_delay=.2,max_delay=.3),clock=lambda:now[0],sleep=sleep,jitter=lambda a,b:1)
    require(len(calls)==4 and pauses==[.2,.3,.3] and caught.value.attempts==4,client_attempts=len(calls),backoff_seconds=pauses,reported_attempts=caught.value.attempts)

def test_budget_expiring_during_cache_recheck_never_starts_rpc(tmp_path,monkeypatch):
    c=EvidenceCache(tmp_path/'cache',server='http://127.0.0.1:8766',credential=TOKEN)
    q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1});c.begin(q);c.complete(q,{'failure':'RUNNER_EXECUTION_ERROR'})
    now=[0.0];reads=[];calls=[];original=c.delivery
    def slow(*a,**kw):
        result=original(*a,**kw);reads.append(1)
        if len(reads)>1:now[0]+=2
        return result
    monkeypatch.setattr(c,'delivery',slow)
    class Transport:
        url='http://127.0.0.1:8766';token=TOKEN
        def request(self,*a,**kw):calls.append(1);raise RPCTransportError()
    with pytest.raises(DeliveryError) as caught:deliver_pending(c,Transport(),q['project'],q['id'],policy=RecoveryPolicy(total_seconds=1,request_timeout=.2),clock=lambda:now[0])
    require(not calls and caught.value.attempts==0 and c.scan()[0]['state']=='pending_upload',client_attempts=len(calls),reported_attempts=caught.value.attempts,logical_elapsed=now[0])

@pytest.mark.parametrize('path',['/api/runner/claim','/api/runner/exchange','/api/runner/heartbeat'])
def test_client_never_retries_non_finish_rpc(path):
    with endpoint('reset') as (url,calls):
        client=MeasuredClient(url,TOKEN)
        with pytest.raises(RPCTransportError):client.request(path,{},timeout=.1)
        metrics=dict(route=path,client_attempts=len(client.measurements),server_received=len(calls),rpc=client.measurements)
        require(len(client.measurements)==1,**metrics)
        require(len(calls)<=1,**metrics)
        require(client.measurements[0]['code']=='RPC_TRANSPORT_UNVERIFIED',**metrics)

@pytest.mark.parametrize('path',['/api/runner/claim','/api/runner/exchange','/api/runner/heartbeat'])
def test_non_finish_reaches_real_server_without_retry(path):
    with endpoint('reset') as (url,calls):
        client=MeasuredClient(url,TOKEN)
        with pytest.raises(RPCTransportError):client.request(path,{},timeout=RecoveryPolicy().request_timeout)
        metrics=dict(route=path,client_attempts=len(client.measurements),server_received=len(calls),rpc=client.measurements)
        require(len(client.measurements)==len(calls)==1,**metrics)

def test_certificate_failure_is_never_retryable_even_at_deadline(tmp_path):
    import ssl,urllib.error
    client=Client('https://127.0.0.1:8766',TOKEN);calls=[]
    class CertificateFailure:
        def open(self,*a,**kw):
            calls.append(1);time.sleep(.08)
            raise urllib.error.URLError(ssl.SSLCertVerificationError(1,'Synthetic certificate failure'))
    client.opener=CertificateFailure()
    c=EvidenceCache(tmp_path/'cache',server=client.url,credential=TOKEN)
    q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1});c.begin(q);c.complete(q,{'failure':'RUNNER_EXECUTION_ERROR'})
    with pytest.raises(AccessError,match='RPC_TLS_UNVERIFIED'):deliver_pending(c,client,q['project'],q['id'],policy=RecoveryPolicy(request_timeout=.05))
    require(len(calls)==1 and c.scan()[0]['state']=='pending_upload',client_attempts=len(calls),category='tls',pending_preserved=c.scan()[0]['state']=='pending_upload')

def test_dns_wait_is_bounded_without_connection_or_secret_output(monkeypatch):
    original=socket.getaddrinfo
    def delayed(host,*a,**kw):
        if host=='localhost':time.sleep(.2)
        return original(host,*a,**kw)
    monkeypatch.setattr(socket,'getaddrinfo',delayed)
    start=time.monotonic()
    with pytest.raises(RPCTransportError):Client('http://localhost:1',TOKEN).request('/api/runner/finish',{},timeout=.05)
    elapsed=time.monotonic()-start
    require(elapsed<.5,elapsed_seconds=elapsed,deadline_seconds=.05,category='transport',phase='dns')

def test_origin_change_during_backoff_never_sends_to_other_server(tmp_path):
    c=EvidenceCache(tmp_path/'cache',server='http://127.0.0.1:8766',credential=TOKEN)
    q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1});c.begin(q);c.complete(q,{'failure':'RUNNER_EXECUTION_ERROR'})
    calls=[]
    class Transport:
        url='http://127.0.0.1:8766';token=TOKEN
        def request(self,*a,**kw):calls.append(1);raise RPCTransportError()
    client=Transport()
    def change(delay):client.url='http://localhost:8766'
    with pytest.raises(AccessError,match='CACHE_OWNER_MISMATCH'):deliver_pending(c,client,q['project'],q['id'],policy=RecoveryPolicy(),sleep=change)
    require(len(calls)==1,client_attempts=len(calls),category='identity_changed')

def test_identity_change_between_guard_and_request_is_rejected(tmp_path):
    client=Client('http://127.0.0.1:8766',TOKEN)
    c=EvidenceCache(tmp_path/'cache',server=client.url,credential=TOKEN)
    q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1});c.begin(q);c.complete(q,{'failure':'RUNNER_EXECUTION_ERROR'})
    original=client.request
    def changed(*a,**kw):client.url='http://localhost:8766';return original(*a,**kw)
    client.request=changed
    with pytest.raises(AccessError,match='RPC_IDENTITY_CHANGED'):deliver_pending(c,client,q['project'],q['id'],policy=RecoveryPolicy())
    require(c.scan()[0]['state']=='pending_upload',category='identity_changed',pending_preserved=c.scan()[0]['state']=='pending_upload')

def test_pre_send_timeout_does_not_imply_a_received_non_finish_request(monkeypatch):
    original=socket.getaddrinfo
    release=threading.Event()
    def delayed(*a,**kw):
        release.wait(3)
        return original(*a,**kw)
    monkeypatch.setattr(socket,'getaddrinfo',delayed)
    try:test_client_never_retries_non_finish_rpc('/api/runner/claim')
    finally:release.set()

def observed_deadline(monkeypatch,phase,timeout):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import ExitStack
    from types import SimpleNamespace
    import agentguard.management.rpc_deadline as rpc
    control=DeadlineControl();control.install(monkeypatch)
    entered=threading.Event();release=threading.Event();finished=threading.Event();created=[];native=socket.socket
    with ExitStack() as stack:
        if phase=='tls':url=stack.enter_context(tls_stall(entered));calls=[]
        else:url,calls=stack.enter_context(endpoint('trickle_headers' if phase=='headers' else 'trickle' if phase=='body' else 'reset',entered))
        values=dict(vars(socket))
        if phase=='dns':
            original=socket.getaddrinfo
            def resolve(*a,**kw):
                entered.set()
                try:
                    release.wait(5)
                    return original(*a,**kw)
                finally:finished.set()
            values['getaddrinfo']=resolve
        if phase=='connect':
            class KernelTimeout(native):
                def connect(self,address):
                    created.append(self);entered.set();release.wait(5);raise TimeoutError('Synthetic kernel connect timeout')
            values['socket']=KernelTimeout
        if phase in {'dns','connect'}:monkeypatch.setattr(rpc,'socket',SimpleNamespace(**values))
        client=MeasuredClient(url,TOKEN)
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(client.request,'/api/runner/finish',{},timeout=timeout)
            timer=control.timers.get(timeout=5)
            try:
                require(entered.wait(5),phase=phase,stage_entered=entered.is_set(),deadline_seconds=timer.interval)
                require(timer.interval==timeout,phase=phase,deadline_seconds=timer.interval)
                control.expire(timer)
                if phase=='connect':release.set()
                # The 3s profile must not pass by waiting for the former native
                # blocking timeout; the explicit deadline must be observed now.
                with pytest.raises(RPCTransportError):future.result(timeout=1)
            finally:release.set()
        if phase=='dns':require(finished.wait(5),phase=phase,resolver_completed=finished.is_set())
        metrics=dict(phase=phase,client_attempts=len(client.measurements),server_received=len(calls),logical_elapsed=control.now,rpc=client.measurements)
        require(timer.cancelled and control.now>timer.interval,**metrics)
        require(len(client.measurements)==1 and client.measurements[0]['code']=='RPC_TRANSPORT_UNVERIFIED',**metrics)
        require(len(calls)==(1 if phase in {'headers','body'} else 0),**metrics)
        if phase=='connect':require(bool(created) and all(s.fileno()==-1 for s in created),**metrics)

@pytest.mark.parametrize('phase',['dns','connect','tls','headers','body'])
def test_deadline_at_observed_io_phase_is_fail_closed(monkeypatch,phase):
    observed_deadline(monkeypatch,phase,3)

@pytest.mark.parametrize('phase',['dns','connect','tls','headers','body'])
def test_short_deadline_after_observed_io_barrier(monkeypatch,phase):
    observed_deadline(monkeypatch,phase,.05)

def test_real_tls_handshake_deadline_with_no_http_request():
    entered=threading.Event()
    with tls_stall(entered) as url:
        client=MeasuredClient(url,TOKEN)
        with pytest.raises(RPCTransportError):client.request('/api/runner/finish',{},timeout=3)
        metrics=dict(client_attempts=len(client.measurements),tls_client_hello_observed=entered.is_set(),rpc=client.measurements)
        require(entered.is_set() and len(client.measurements)==1,**metrics)
        require(client.measurements[0]['code']=='RPC_TRANSPORT_UNVERIFIED',**metrics)
        require(client.measurements[0]['elapsed_seconds']<=4,**metrics)

def test_finish_retry_before_dns_completion_preserves_pending(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    import agentguard.management.rpc_deadline as rpc
    with endpoint('reset') as (url,calls):
        c=EvidenceCache(tmp_path/'cache',server=url,credential=TOKEN)
        q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1});c.begin(q);c.complete(q,{'failure':'RUNNER_EXECUTION_ERROR'})
        control=DeadlineControl();control.install(monkeypatch)
        import queue
        entries=queue.Queue();release=threading.Event();original=socket.getaddrinfo
        def resolve(*a,**kw):entries.put(1);release.wait(10);return original(*a,**kw)
        values=dict(vars(socket));values['getaddrinfo']=resolve;monkeypatch.setattr(rpc,'socket',SimpleNamespace(**values))
        client=MeasuredClient(url,TOKEN);policy=RecoveryPolicy(max_attempts=2,base_delay=.01,max_delay=.02)
        with ThreadPoolExecutor(1) as pool:
            future=pool.submit(deliver_pending,c,client,q['project'],q['id'],policy=policy)
            try:
                for _ in range(2):
                    timer=control.timers.get(timeout=5);entries.get(timeout=5);control.expire(timer)
                with pytest.raises(DeliveryError) as caught:future.result(timeout=5)
            finally:release.set()
        metrics=dict(client_attempts=len(client.measurements),server_received=len(calls),terminal_code=caught.value.code,category=caught.value.category,rpc=client.measurements)
        require(caught.value.attempts==2 and caught.value.category=='transport',**metrics)
        require(len(client.measurements)==2 and not calls,**metrics)
        require(c.scan()[0]['state']=='pending_upload',**metrics)

@pytest.mark.parametrize('trust',['trusted','untrusted','wrong_hostname'])
def test_real_tls_verification_and_http_payload(tmp_path,monkeypatch,trust):
    import ssl
    import agentguard.management.rpc_deadline as rpc
    context,cert=tls_fixture(tmp_path);original=ssl.create_default_context
    if trust!='untrusted':
        def trusted(*a,**kw):
            result=original(cafile=str(cert))
            require(result.check_hostname and result.verify_mode==ssl.CERT_REQUIRED,category='TLS_VERIFICATION_DISABLED')
            return result
        monkeypatch.setattr(rpc.ssl,'create_default_context',trusted)
    with endpoint('large' if trust=='trusted' else 'legacy',ssl_context=context) as (url,calls):
        if trust=='wrong_hostname':url=url.replace('localhost','127.0.0.1')
        client=MeasuredClient(url,TOKEN)
        if trust=='trusted':
            result=client.request('/api/runner/finish',{'padding':'x'*(1024*1024)},timeout=3)
            require(result=={'padding':'x'*(1024*1024)},category='TLS_HTTP_RESPONSE_MISMATCH')
            require(len(calls)==len(client.measurements)==1,server_received=len(calls),rpc=client.measurements)
        else:
            c=EvidenceCache(tmp_path/'cache',server=url,credential=TOKEN)
            q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1});c.begin(q);c.complete(q,{'failure':'RUNNER_EXECUTION_ERROR'})
            with pytest.raises(AccessError) as caught:deliver_pending(c,client,q['project'],q['id'],policy=RecoveryPolicy(max_attempts=2))
            metrics=dict(trust=trust,client_attempts=len(client.measurements),server_received=len(calls),terminal_code=caught.value.code,rpc=client.measurements)
            require(caught.value.code=='RPC_TLS_UNVERIFIED' and len(client.measurements)==1 and not calls,**metrics)
            require(c.scan()[0]['state']=='pending_upload',**metrics)
