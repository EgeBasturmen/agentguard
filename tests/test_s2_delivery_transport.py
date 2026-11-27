"""Actual HTTP timeout/reset/protocol boundaries. PYTEST_DONT_REWRITE."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
import json,socket,threading,time
import pytest
from agentguard.management.client import Client,RPCTransportError,RPCRemoteError
from agentguard.management.cache import EvidenceCache
from agentguard.management.delivery import RecoveryPolicy,DeliveryError,deliver_pending
from agentguard.management.store import AccessError
pytestmark=pytest.mark.integration
TOKEN='ag7r_'+'s'*43

@pytest.fixture(autouse=True)
def private_fixture(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    secure_fixture_directory(tmp_path)

@contextmanager
def endpoint(mode):
    calls=[]
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        def handle_error(self,*a):pass
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']));calls.append(1)
            if mode=='reset':self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
            if mode=='timeout':time.sleep(.2)
            if mode=='trickle_headers':
                try:
                    for byte in b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}':
                        self.connection.sendall(bytes([byte]));time.sleep(.02)
                except OSError:pass
                return
            status,body=(503,{'error':'SERVICE_UNAVAILABLE'}) if mode=='server' else (401,{'error':'UNAUTHENTICATED'}) if mode=='auth' else (403,{'error':'FORBIDDEN'}) if mode=='forbidden' else (409,{'error':'STORED_RESULT_INTEGRITY_ERROR'}) if mode=='integrity' else (200,{'incompatible':True})
            raw=b'{' if mode in {'json','bad_error'} else json.dumps(body).encode()
            if mode=='bad_error':status=503
            self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)+(20 if mode=='truncated' else 0)));self.end_headers()
            try:
                if mode=='trickle':
                    for byte in raw:self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.01)
                else:self.wfile.write(raw)
            except (OSError,ValueError):pass
    server=Server(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port),calls
    finally:server.shutdown();server.server_close();thread.join(3)

@pytest.mark.parametrize('mode,attempts',[('reset',2),('timeout',2),('trickle',2),('trickle_headers',2),('truncated',2),('server',2),('auth',1),('forbidden',1),('integrity',1),('json',1),('bad_error',1),('legacy',1)])
def test_only_transient_transport_is_retried(tmp_path,mode,attempts):
    with endpoint(mode) as (url,calls):
        c=EvidenceCache(tmp_path/'cache',server=url,credential=TOKEN)
        q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1});c.begin(q);c.complete(q,{'failure':'RUNNER_EXECUTION_ERROR'})
        started=time.monotonic()
        with pytest.raises(AccessError):deliver_pending(c,Client(url,TOKEN),q['project'],q['id'],policy=RecoveryPolicy(max_attempts=2,total_seconds=1,request_timeout=.05,base_delay=.01,max_delay=.02))
        assert time.monotonic()-started<2 and len(calls)==attempts
        assert c.scan()[0]['state']=='pending_upload'

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
    assert len(calls)==4 and pauses==[.2,.3,.3] and caught.value.attempts==4

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
    assert not calls and caught.value.attempts==0 and c.scan()[0]['state']=='pending_upload'

@pytest.mark.parametrize('path',['/api/runner/claim','/api/runner/exchange','/api/runner/heartbeat'])
def test_client_never_retries_non_finish_rpc(path):
    with endpoint('reset') as (url,calls):
        with pytest.raises(RPCTransportError):Client(url,TOKEN).request(path,{},timeout=.1)
        assert len(calls)==1

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
    assert len(calls)==1 and c.scan()[0]['state']=='pending_upload'

def test_dns_wait_is_bounded_without_connection_or_secret_output(monkeypatch):
    original=socket.getaddrinfo
    def delayed(host,*a,**kw):
        if host=='localhost':time.sleep(.2)
        return original(host,*a,**kw)
    monkeypatch.setattr(socket,'getaddrinfo',delayed)
    start=time.monotonic()
    with pytest.raises(RPCTransportError):Client('http://localhost:1',TOKEN).request('/api/runner/finish',{},timeout=.05)
    assert time.monotonic()-start<.5

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
    assert len(calls)==1

def test_identity_change_between_guard_and_request_is_rejected(tmp_path):
    client=Client('http://127.0.0.1:8766',TOKEN)
    c=EvidenceCache(tmp_path/'cache',server=client.url,credential=TOKEN)
    q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1});c.begin(q);c.complete(q,{'failure':'RUNNER_EXECUTION_ERROR'})
    original=client.request
    def changed(*a,**kw):client.url='http://localhost:8766';return original(*a,**kw)
    client.request=changed
    with pytest.raises(AccessError,match='RPC_IDENTITY_CHANGED'):deliver_pending(c,client,q['project'],q['id'],policy=RecoveryPolicy())
    assert c.scan()[0]['state']=='pending_upload'
