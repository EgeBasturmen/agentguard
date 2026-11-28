"""Bounded pilot HTTP transport. TLS edge, private storage and single-controller deployment required."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from importlib import resources
import json
import socket
import threading
import time
import uuid
from urllib.parse import urlsplit,parse_qs
from agentguard.management.store import AccessError,encode
from agentguard.integrations.http import _json

CSP="default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"

def dispatch(service,method,path,token,data,query):
    parts=path.strip('/').split('/')
    if method=='GET' and path=='/api/account':return service.account_info(token)
    if method=='POST' and path=='/api/account/delete':return service.delete_workspace(token,**data)
    if method=='POST' and path=='/api/runner/exchange':return service.exchange_enrollment(**data)
    if method=='POST' and path=='/api/runner/claim':
        if data:raise AccessError('UNKNOWN_FIELDS',400)
        return service.claim(token)
    if method=='POST' and path in {'/api/runner/heartbeat','/api/runner/finish'}:
        return (service.heartbeat if path.endswith('heartbeat') else service.finish)(token,**data)
    if method=='POST' and path=='/api/runner/recovery-evidence':return service.register_recovery_evidence(token,**data)
    if path=='/api/key/rotate' and method=='POST':
        if data:raise AccessError('UNKNOWN_FIELDS',400)
        return service.rotate_key(token)
    if path=='/api/projects':
        if method=='GET':return service.projects(token)
        if method=='POST':return service.create_project(token,**data)
    if len(parts)<4 or parts[:2]!=['api','projects']:raise AccessError('NOT_FOUND',404)
    project=parts[2];tail=parts[3:]
    offset=int(query.get('offset',['0'])[0]);limit=int(query.get('limit',['50'])[0])
    if method=='GET':
        if tail==['info']:return service.project(token,project)
        if tail[0] in {'connections','cases','suites'} and len(tail)==1:return service.objects(token,project,{'connections':'connection','cases':'case','suites':'suite'}[tail[0]],offset=offset,limit=limit)
        if tail==['runs']:return service.jobs(token,project,after=offset,limit=limit)
        if tail==['usage']:return service.usage(token,project)
        if tail==['pilot']:return service.pilot_readiness(token,project)
        if tail==['audit']:return service.audit_log(token,project,after=int(query.get('after',['0'])[0]),limit=limit)
        if tail==['runners']:return service.runners(token,project)
        if tail==['operations']:return service.operations_health(token,project)
        if tail==['onboarding']:return service.onboarding_funnel(token,project)
        if tail[0]=='cases' and len(tail)==2:return service.case(token,project,tail[1])
        if tail[0]=='suites' and len(tail)==2:return service.suite_spec(token,project,tail[1])
        if tail[0]=='runs' and len(tail)==2:return service.job(token,project,tail[1])
        if tail[0]=='runs' and len(tail)==3 and tail[2]=='events':return service.events(token,project,tail[1],after=int(query.get('after',['0'])[0]),limit=limit)
        if tail[0]=='artifacts' and len(tail)==2:return service.artifact(token,project,tail[1],query.get('grant',[''])[0])
        if tail[0]=='feedback' and len(tail)==2:return service.feedback_item(token,project,tail[1])
    if method=='POST':
        if len(tail)==3 and tail[0]=='runs' and tail[2]=='recover':return service.recover_job(tail[1],token=token,project=project,**data)
        actions={'members':service.add_member,'keys':service.issue_key,'connections':service.connect,
            'generate':service.generate_cases,'suites':service.freeze_suite,'runs':service.enqueue,
            'data-policy':service.set_data_policy,'plans':service.request_plan,'feedback':service.feedback,
            'runner-enrollment':service.enroll_runner,'compare':service.compare_reports}
        actions['delete']=service.delete_project
        if len(tail)==1 and tail[0] in actions:return actions[tail[0]](token,project,**data)
        if tail==['cancellation']:
            if data:raise AccessError('UNKNOWN_FIELDS',400)
            return service.request_cancellation(token,project)
        if tail==['members','remove']:return service.remove_member(token,project,**data)
        if len(tail)==3 and tail[0]=='cases':
            actions={'review':service.review_case,'approve':service.approve_case,'revise':service.revise_case,'retire':service.retire_case}
            if tail[2] in actions:return actions[tail[2]](token,project,tail[1],**data)
        if len(tail)==3 and tail[0]=='suites' and tail[2]=='agent-version':return service.bind_agent_version(token,project,tail[1],**data)
        if len(tail)==3 and tail[0]=='runs' and tail[2]=='cancel':
            if data:raise AccessError('UNKNOWN_FIELDS',400)
            return service.cancel(token,project,tail[1])
        if len(tail)==3 and tail[0]=='keys' and tail[2]=='revoke':return service.revoke_key(token,project,tail[1],**data)
        if len(tail)==3 and tail[0]=='runners' and tail[2]=='revoke':return service.revoke_runner(token,project,tail[1],**data)
        if len(tail)==3 and tail[0]=='feedback' and tail[2]=='transition':return service.transition_feedback(token,project,tail[1],**data)
        if len(tail)==3 and tail[0]=='artifacts':
            if tail[2]=='grant':return service.artifact_grant(token,project,tail[1],**data)
            if tail[2]=='delete':return service.delete_artifact(token,project,tail[1],**data)
    raise AccessError('NOT_FOUND',404)

class PilotServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,handler):
        self.slots=threading.BoundedSemaphore(16);self.rate_lock=threading.Lock();self.windows={}
        super().__init__(address,handler)
    def process_request(self,request,client_address):
        if not self.slots.acquire(blocking=False):self.shutdown_request(request);return
        try:super().process_request(request,client_address)
        except Exception:self.slots.release();raise
    def process_request_thread(self,request,client_address):
        try:super().process_request_thread(request,client_address)
        finally:self.slots.release()
    def handle_error(self,request,client_address):pass
    def service_actions(self):
        if time.monotonic()-getattr(self,'last_maintenance',0)>=30:
            self.last_maintenance=time.monotonic()
            self.service.reconcile();self.service.purge_expired()

def make_server(service,host='127.0.0.1',port=0,*,tls_context=None,public_origin=None,request_timeout=10.0):
    if isinstance(request_timeout,bool) or not isinstance(request_timeout,(int,float)) or not 0<request_timeout<=60:raise ValueError('Bounded request deadline required')
    if host not in {'127.0.0.1','localhost','::1'} and tls_context is None:raise ValueError('Non-loopback binding requires TLS or a loopback TLS reverse proxy')
    if public_origin and (urlsplit(public_origin).scheme!='https' or urlsplit(public_origin).path not in {'','/'} or urlsplit(public_origin).query or urlsplit(public_origin).username):raise ValueError('Explicit HTTPS public origin required')
    class Handler(BaseHTTPRequestHandler):
        server_version='AgentGuard';sys_version='';protocol_version='HTTP/1.0'
        def setup(self):
            super().setup();self.connection.settimeout(request_timeout)
            # HTTP/1.0: one request per connection. Per-read socket timeouts alone
            # permit indefinitely dripping headers/bodies; bound the entire request.
            def expire():
                try:self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            self.deadline=threading.Timer(request_timeout,expire)
            self.deadline.daemon=True;self.deadline.start()
        def finish(self):
            try:super().finish()
            finally:self.deadline.cancel()
        def log_message(self,*args):pass
        def send_error(self,code,message=None,explain=None):self.respond(code,dict(error='HTTP_PROTOCOL_ERROR'))
        def respond(self,status,value,*,content_type='application/json; charset=utf-8',download=None):
            payload=value if isinstance(value,bytes) else encode(value).encode('utf-8')
            self.send_response(status);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(payload)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer')
            self.send_header('Content-Security-Policy',CSP);self.send_header('X-Request-ID',uuid.uuid4().hex)
            self.send_header('X-AgentGuard-API-Version','1')
            if tls_context is not None or public_origin:self.send_header('Strict-Transport-Security','max-age=31536000')
            if download:self.send_header('Content-Disposition','attachment; filename="'+download+'"')
            self.end_headers();self.wfile.write(payload)
        def execute(self):
            try:
                window=int(time.monotonic()//60);peer=self.client_address[0]
                with self.server.rate_lock:
                    self.server.windows={k:v for k,v in self.server.windows.items() if k[1]>=window-1}
                    key=(peer,window);count=self.server.windows.get(key,0)+1;self.server.windows[key]=count
                    if count>600:raise AccessError('REQUEST_RATE_LIMIT',429)
                port=self.server.server_port
                hosts={'127.0.0.1:'+str(port),'localhost:'+str(port),'[::1]:'+str(port)}
                origins={('https' if tls_context else 'http')+'://'+h for h in hosts}
                if public_origin:hosts.add(urlsplit(public_origin).netloc);origins.add(public_origin.rstrip('/'))
                if self.headers.get('Host') not in hosts or len(self.headers.get_all('Host',[]))!=1:raise AccessError('INVALID_HOST',403)
                if self.headers.get('Origin') is not None and self.headers.get('Origin') not in origins:raise AccessError('CROSS_ORIGIN_DENIED',403)
                versions=self.headers.get_all('X-AgentGuard-API-Version',[])
                if versions and versions!=['1']:raise AccessError('UNSUPPORTED_API_VERSION',400)
                if sum(len(k)+len(v) for k,v in self.headers.items())>16384:raise AccessError('HEADERS_TOO_LARGE',431)
                parsed=urlsplit(self.path)
                if len(self.path)>4096 or parsed.scheme or parsed.netloc:raise AccessError('INVALID_PATH',400)
                query=parse_qs(parsed.query,keep_blank_values=True,max_num_fields=10)
                if any(len(v)!=1 for v in query.values()) or set(query)-{'offset','limit','after','grant'}:raise AccessError('INVALID_QUERY',400)
                path=parsed.path
                if self.command=='GET' and path in {'/','/app.js','/app.css'}:
                    filename={'/':'index.html','/app.js':'app.js','/app.css':'app.css'}[path]
                    body=resources.files('agentguard.management.web').joinpath(filename).read_bytes()
                    mime={'/':'text/html; charset=utf-8','/app.js':'text/javascript; charset=utf-8','/app.css':'text/css; charset=utf-8'}[path]
                    self.respond(200,body,content_type=mime);return
                auth=self.headers.get_all('Authorization',[])
                if len(auth)>1:raise AccessError('UNAUTHENTICATED',401)
                token=auth[0][7:] if auth and auth[0].startswith('Bearer ') else ''
                data={}
                if self.command=='POST':
                    lengths=self.headers.get_all('Content-Length',[])
                    if self.headers.get('Transfer-Encoding') is not None or len(lengths)!=1 or not lengths[0].isascii() or not lengths[0].isdigit():raise AccessError('CONTENT_LENGTH_REQUIRED',400)
                    length=int(lengths[0])
                    if length>2*1024*1024:raise AccessError('BODY_TOO_LARGE',413)
                    if self.headers.get_content_type()!='application/json':raise AccessError('JSON_REQUIRED',415)
                    raw=self.rfile.read(length)
                    if len(raw)!=length:raise AccessError('INCOMPLETE_BODY',400)
                    data=_json(raw)
                    if not isinstance(data,dict):raise AccessError('OBJECT_REQUIRED',400)
                result=dispatch(service,self.command,path,token,data,query)
                self.respond(200,result,download=path.split('/')[-1]+'.json' if self.command=='GET' and '/artifacts/' in path else None)
            except AccessError as exc:self.respond(exc.status,dict(error=exc.code))
            except (ValueError,TypeError,KeyError,PermissionError,RecursionError):self.respond(400,dict(error='INVALID_SPEC_OR_STATE'))
            except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
            except Exception:self.respond(500,dict(error='OPERATION_FAILED_NO_RESULT_VERIFIED'))
        do_GET=execute;do_POST=execute
    server=PilotServer((host,port),Handler)
    server.service=service
    if tls_context is not None:server.socket=tls_context.wrap_socket(server.socket,server_side=True,do_handshake_on_connect=False)
    return server

@contextmanager
def running_server(service):
    server=make_server(service);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield 'http://127.0.0.1:'+str(server.server_port)
    finally:server.shutdown();server.server_close();thread.join(timeout=5)
