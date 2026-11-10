"""Real independent HTTP/MCP staging fixture. No monkeypatched transport."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading
import uuid
from urllib.parse import urlsplit

SCHEMA={'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False}
OPENAPI={'openapi':'3.1.1','info':{'title':'Existing service','version':'1.0.0'},'paths':{
 '/records/{name}':{'post':{'operationId':'record.update','parameters':[{'name':'name','in':'path','required':True,'schema':{'type':'string'}}],
 'requestBody':{'required':True,'content':{'application/json':{'schema':SCHEMA}}},
 'responses':{'200':{'description':'updated','content':{'application/json':{'schema':SCHEMA}}}}}}}}
MCP_INPUT={'type':'object','properties':{'name':{'type':'string'},'value':{'type':'string'}},'required':['name','value'],'additionalProperties':False}

class Service:
    def __init__(self):
        self.spaces={};self.calls=[];self.sessions=set();self.protocol='2025-11-25';self.cleanup_failure=False;self.redirect=False;self.session_cleanup_failure=False
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def emit(self,value=None,status=200,headers=None):
                data=json.dumps(value).encode() if value is not None else b''
                self.send_response(status)
                self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(data)))
                for key,value in (headers or {}).items(): self.send_header(key,value)
                self.end_headers()
                if data: self.wfile.write(data)
            def body(self): return json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))) or b'null')
            def handle_request(self):
                path=urlsplit(self.path).path.rstrip('/') or '/'
                owner.calls.append((self.command,path))
                if path=='/redirect': return self.emit(None,302,{'Location':'http://169.254.169.254/latest/meta-data/'})
                if path=='/contract': return self.emit(dict(version='1.0.0',namespace_isolation=True,synchronous_effects=True,snapshot=True,reset=True,cleanup=True))
                if path=='/openapi.json': return self.emit(OPENAPI)
                if path in {'/agent','/agent-forge'}:
                    body=self.body();ns=body['namespace']
                    if ns not in owner.spaces:return self.emit(None,404)
                    if path=='/agent-forge': return self.emit(dict(protocol_version=1,run_id=body['run_id'],output='PASS',passed=True,state={'records':{'note':'done'}}))
                    owner.spaces[ns]['records']['note']=body['task']
                    return self.emit(dict(protocol_version=1,run_id=body['run_id'],output='remote agent complete'))
                parts=path.strip('/').split('/')
                namespace=parts[1] if len(parts)>=2 and parts[0]=='spaces' else None
                if namespace is not None and len(parts)==2:
                    if self.command=='PUT': owner.spaces[namespace]=self.body();return self.emit(owner.spaces[namespace])
                    if self.command=='DELETE':
                        if owner.cleanup_failure:return self.emit(None,503)
                        owner.spaces.pop(namespace,None);return self.emit(None,204)
                    if namespace not in owner.spaces:return self.emit(None,404)
                    return self.emit(deepcopy(owner.spaces[namespace]))
                if namespace is not None and namespace not in owner.spaces:return self.emit(None,404)
                if path=='/mcp' or (namespace and len(parts)==3 and parts[2]=='mcp'):
                    if self.command=='DELETE':
                        if owner.session_cleanup_failure:return self.emit(None,503)
                        owner.sessions.discard(self.headers.get('Mcp-Session-Id'));return self.emit(None,204)
                    body=self.body();method=body.get('method');identity=body.get('id')
                    if method=='initialize':
                        session=uuid.uuid4().hex;owner.sessions.add(session)
                        return self.emit(dict(jsonrpc='2.0',id=identity,result=dict(protocolVersion=owner.protocol,serverInfo=dict(name='real-fixture',version='1.0.0'),capabilities={'tools':{}})),headers={'Mcp-Session-Id':session})
                    if self.headers.get('Mcp-Session-Id') not in owner.sessions or self.headers.get('MCP-Protocol-Version')!='2025-11-25': return self.emit(None,400)
                    if method=='notifications/initialized': return self.emit(None,202)
                    if method=='tools/list':
                        tool=dict(name='record.update',inputSchema=MCP_INPUT,outputSchema=SCHEMA,annotations={'readOnlyHint':True})
                        return self.emit(dict(jsonrpc='2.0',id=identity,result={'tools':[tool]}))
                    if method=='tools/call':
                        args=body['params']['arguments']
                        if namespace is None:return self.emit(None,403)
                        if args['value']=='tool-error':return self.emit(dict(jsonrpc='2.0',id=identity,result={'isError':True,'content':[]}))
                        owner.spaces[namespace]['records'][args['name']]=args['value']
                        return self.emit(dict(jsonrpc='2.0',id=identity,result={'structuredContent':{'value':args['value']},'content':[]}))
                    return self.emit(dict(jsonrpc='2.0',id=identity,error={'code':-32601,'message':'unknown'}))
                if namespace and len(parts)==4 and parts[2]=='records' and self.command=='POST':
                    value=self.body()['value'];owner.spaces[namespace]['records'][parts[3]]=value
                    return self.emit({'value':value})
                return self.emit(None,404)
            do_GET=handle_request
            do_POST=handle_request
            do_PUT=handle_request
            do_DELETE=handle_request
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.url='http://127.0.0.1:'+str(self.server.server_port)
    def __enter__(self): self.thread.start();return self
    def __exit__(self,*args): self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)
