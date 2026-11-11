"""Real HTTP protocol fixture, explicitly not a real LLM provider."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading

class ModelService:
    def __init__(self,mode='normal'):self.mode=mode;self.calls=[]
    def __enter__(self):
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));owner.calls.append(data)
                text='ready'
                try:
                    prompt=json.loads(data['messages'][-1]['content'])
                    if 'untrusted_response' in prompt:
                        text=json.dumps(dict(passed=prompt['untrusted_response']!='wrong',confidence=0.99,reason='fixture rubric'))
                except (ValueError,KeyError):pass
                result=dict(protocol_version=1,text=text,input_tokens=20,output_tokens=10,model_version='protocol-fixture-v1')
                if owner.mode=='missing_usage':result.pop('input_tokens')
                if owner.mode=='overflow':result['output_tokens']=999999
                if owner.mode=='disagree':result['text']=json.dumps(dict(passed=True,confidence=0.99,reason='always agrees'))
                if owner.mode=='error':self.send_error(503);return
                body=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.server.server_address[1]);return self
    def __exit__(self,*args):self.server.shutdown();self.server.server_close();self.thread.join()

def model_config(service,**kwargs):
    return dict(provider='json-http',model='test-model',mode='live',endpoint=dict(base_url=service.url,allow_http=True,private_hosts=['127.0.0.1']),**kwargs)
