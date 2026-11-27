"""Real HTTP fixtures and allowlisted timing measurements. PYTEST_DONT_REWRITE."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json,queue,socket,threading,time,ssl,shutil,subprocess,os
from pathlib import Path
from types import SimpleNamespace
from agentguard.management.client import Client
from agentguard.management.store import AccessError

class MeasuredClient(Client):
    def __init__(self,*a,**kw):super().__init__(*a,**kw);self.measurements=[]
    def request(self,*a,**kw):
        start=time.monotonic();code='HTTP_JSON_COMPLETE';category='response'
        try:return super().request(*a,**kw)
        except AccessError as error:
            code=error.code;category='transport' if code=='RPC_TRANSPORT_UNVERIFIED' else 'tls' if code=='RPC_TLS_UNVERIFIED' else 'protocol' if code.startswith('RPC_') else 'remote'
            raise
        finally:self.measurements.append(dict(elapsed_seconds=time.monotonic()-start,deadline_seconds=kw.get('timeout',10),code=code,category=category))

def require(condition,**metrics):
    # Never include request arguments, credentials, exception strings or bodies.
    assert condition,json.dumps(metrics,sort_keys=True)

@contextmanager
def endpoint(mode,entered=None,ssl_context=None):
    calls=[];release=threading.Event()
    class Server(ThreadingHTTPServer):
        daemon_threads=True
        def handle_error(self,*a):pass
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']));calls.append(1)
            try:
                if mode=='reset':self.connection.shutdown(socket.SHUT_RDWR);return
                if mode in {'timeout','trickle_headers','trickle'}:
                    if mode=='trickle_headers':self.connection.sendall(b'HTTP/1.0 200 OK\r\nContent-Type:')
                    elif mode=='trickle':
                        self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length','100');self.end_headers();self.wfile.write(b'{');self.wfile.flush()
                    if entered is not None:entered.set()
                    release.wait(20);return
                status,body=(503,{'error':'SERVICE_UNAVAILABLE'}) if mode=='server' else (401,{'error':'UNAUTHENTICATED'}) if mode=='auth' else (403,{'error':'FORBIDDEN'}) if mode=='forbidden' else (409,{'error':'STORED_RESULT_INTEGRITY_ERROR'}) if mode=='integrity' else (200,{'incompatible':True})
                if mode=='large':body={'padding':'x'*(1024*1024)}
                raw=b'{' if mode in {'json','bad_error'} else json.dumps(body).encode()
                if mode=='bad_error':status=503
                self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)+(20 if mode=='truncated' else 0)));self.end_headers();self.wfile.write(raw)
            except (OSError,ValueError):pass
    server=Server(('127.0.0.1',0),Handler)
    if ssl_context is not None:server.socket=ssl_context.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield ('https://localhost:' if ssl_context is not None else 'http://127.0.0.1:')+str(server.server_port),calls
    finally:release.set();server.shutdown();server.server_close();thread.join(3)

@contextmanager
def tls_stall(entered):
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(5)
    release=threading.Event();errors=[]
    def serve():
        try:
            conn,_=listener.accept()
            with conn:
                conn.settimeout(5)
                if conn.recv(4096):entered.set()
                release.wait(10)
        except OSError:errors.append('socket_error')
    thread=threading.Thread(target=serve,daemon=True);thread.start()
    try:yield 'https://127.0.0.1:'+str(listener.getsockname()[1])
    finally:release.set();listener.close();thread.join(6)

class DeadlineControl:
    """Advance logical time only after the chosen real I/O stage is observed.

    Requested timeout values are retained. This controls scheduling, not response
    parsing, certificate verification or the real TCP/TLS/HTTP payloads.
    """
    def __init__(self):self.now=0.;self.timers=queue.Queue()
    def monotonic(self):return self.now
    def install(self,monkeypatch):
        import agentguard.management.client as client
        import agentguard.management.rpc_deadline as rpc
        control=self
        class Timer:
            def __init__(self,interval,callback):self.interval=interval;self.callback=callback;self.cancelled=False
            def start(self):self.started=control.now;control.timers.put(self)
            def cancel(self):self.cancelled=True
        monkeypatch.setattr(client,'time',self);monkeypatch.setattr(rpc,'time',self)
        monkeypatch.setattr(rpc,'threading',SimpleNamespace(Event=threading.Event,Lock=threading.Lock,Thread=threading.Thread,Timer=Timer))
    def expire(self,timer):
        self.now=timer.started+timer.interval+.000001;timer.callback()

def tls_fixture(directory):
    """Ephemeral test-only certificate; no committed key, no raw tool errors."""
    executable=shutil.which('openssl')
    if executable is None:
        candidate=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Git/usr/bin/openssl.exe'
        if candidate.is_file():executable=str(candidate)
    require(executable is not None,category='TLS_FIXTURE_TOOL_UNAVAILABLE')
    config=directory/'tls.cnf';cert=directory/'tls.crt';key=directory/'tls.key'
    config.write_text('[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=v3\n[dn]\nCN=localhost\n[v3]\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign,cRLSign\nextendedKeyUsage=serverAuth\nsubjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\nsubjectAltName=DNS:localhost\n',encoding='ascii')
    env=dict(os.environ);env.pop('OPENSSL_CONF',None)
    try:result=subprocess.run([executable,'req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-config',str(config)],capture_output=True,timeout=30,env=env)
    except subprocess.TimeoutExpired:raise AssertionError('TLS_FIXTURE_TIMEOUT') from None
    require(result.returncode==0,category='TLS_FIXTURE_GENERATION',exit_code=result.returncode)
    server=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server.load_cert_chain(cert,key)
    return server,cert
