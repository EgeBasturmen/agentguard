"""Standalone installed-wheel probe. Uses real local HTTP, never paid APIs."""
import argparse
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading

def verify(cli,docker=False):
    from agentguard.sdk import GatewayConfig,ModelGateway,EvaluationSpec,evaluate
    import agentguard.sdk
    assert Path(agentguard.sdk.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            text='ready'
            try:
                prompt=json.loads(data['messages'][-1]['content'])
                if 'untrusted_response' in prompt:text=json.dumps(dict(passed=prompt['untrusted_response']!='incorrect',confidence=0.99,reason='HTTP contract fixture'))
            except ValueError:pass
            body=json.dumps(dict(protocol_version=1,text=text,input_tokens=20,output_tokens=10,model_version='wheel-protocol-fixture')).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        config=dict(provider='json-http',model='protocol-fixture',mode='live',endpoint=dict(base_url='http://127.0.0.1:'+str(server.server_address[1]),allow_http=True,private_hosts=['127.0.0.1']))
        gateway=ModelGateway(GatewayConfig.model_validate(config))
        assert gateway.generate([dict(role='user',content='ready')],max_output_tokens=32,purpose='generation')=='ready'
        judge=EvaluationSpec(id='judge',kind='judge',config=dict(rubric='Reject incorrect; accept correct',rubric_version='1',labels=[dict(input='correct',expected=True,source='independent-fixture'),dict(input='incorrect',expected=False,source='independent-fixture')]))
        assert evaluate(judge,{'output':'ready','gateway':gateway}).status=='pass'
        assert len(gateway.evidence().calls)==4 and gateway.evidence().status=='passed'
        assert evaluate(EvaluationSpec(id='missing',kind='output_equals',config={'text':'ready'}),{}).status=='inconclusive'
        if docker:
            Path('phase5_worker.py').write_text("import os\nclass Agent:\n    async def run(self,task,sandbox):\n        assert 'GEMINI_API_KEY' not in os.environ\n        return sandbox.call('model.generate',{'messages':[{'role':'user','content':task}],'max_output_tokens':32})\nclass Session:\n    def __init__(self):self.count=0\n    async def run(self,task,sandbox):\n        self.count+=1\n        return str(self.count)\n",encoding='utf-8')
            plan=dict(schema_version=1,scenario=dict(name='wheel-model',task='ready',initial_state={},evaluations=[dict(id='output',kind='output_equals',config={'text':'ready'})]),
                agent=dict(transport='python',reference='phase5_worker:Agent',source_dir='.'),environment=dict(kind='memory'),policy=dict(allowed_tools=['model.generate']),model=config)
            def run(plan,name):
                file=Path(name+'-plan.json');file.write_text(json.dumps(plan),encoding='utf-8')
                result=subprocess.run([str(cli),'integration',str(file),'--json-report',name+'.json'],capture_output=True,text=True,timeout=180)
                assert result.returncode==0,(result.stdout,result.stderr)
                report=json.loads(Path(name+'.json').read_text(encoding='utf-8'))
                assert report['verdict']=='PASS' and report['result']['cleanup_status']=='verified'
                return report['result']
            model_result=run(plan,'wheel-phase5-model')
            assert model_result['model_evidence']['calls'][0]['input_tokens']==20 and model_result['audit'][0]['tool_name']=='model.generate'
            plan.pop('model');plan['policy']={'allowed_tools':[]};plan['agent']['reference']='phase5_worker:Session'
            plan['scenario']=dict(name='wheel-session',task='session',initial_state={},turns=[dict(task='first'),dict(task='second')],evaluations=[dict(id='first',kind='turn_output',config={'turn':1,'text':'1'}),dict(id='second',kind='turn_output',config={'turn':2,'text':'2'})])
            session=run(plan,'wheel-phase5-session');assert session['turn_outputs']==['1','2']
        return dict(phase5_gateway=True,phase5_evaluations=True,phase5_worker=docker,real_llm_verified=False)
    finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cli',required=True,type=Path);parser.add_argument('--docker',action='store_true');args=parser.parse_args()
    print(json.dumps(verify(args.cli,args.docker)))
