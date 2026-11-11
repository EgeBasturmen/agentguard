"""Real local HTTP errors, never a Gemini network request or real credential."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.mark.integration
@pytest.mark.parametrize('status,reason,field,category', [
    ('INVALID_ARGUMENT', 'API_KEY_INVALID', None, 'credentials'),
    ('INVALID_ARGUMENT', 'API_KEY_EXPIRED', None, 'credentials'),
    ('PERMISSION_DENIED', 'API_KEY_SERVICE_BLOCKED', None, 'model_access'),
    ('FAILED_PRECONDITION', None, None, 'provider_precondition'),
    ('INVALID_ARGUMENT', None, 'generation_config.max_output_tokens', 'request_parameters'),
    ('INVALID_ARGUMENT', None, 'contents[0].parts[0].text', 'request_parameters'),
    ('INVALID_ARGUMENT', None, None, 'invalid_request'),
])
def test_google_structured_error_reaches_safe_diagnostic_without_body(monkeypatch,status,reason,field,category):
    import agentguard.model_gateway.gateway as gateway_module
    from agentguard.integrations.http import Endpoint, request_json
    from scripts.check_gemini import VerificationProgress
    secret='offline-sensitive-key-and-response'
    payload={'error':{'code':400,'status':status,'message':secret,'details':[]}}
    if reason:
        payload['error']['details'].append({'@type':'type.googleapis.com/google.rpc.ErrorInfo',
            'reason':reason,'domain':'googleapis.com','metadata':{'consumer':secret,'service':secret}})
    if field:
        payload['error']['details'].append({'@type':'type.googleapis.com/google.rpc.BadRequest',
            'fieldViolations':[{'field':field,'description':secret}]})
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(data)
            assert self.headers['x-goog-api-key']==secret
            body=json.dumps(payload).encode()
            self.send_response(400);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    def local_request(endpoint,method,path,body,**kwargs):
        assert endpoint.base_url=='https://generativelanguage.googleapis.com'
        assert path=='/v1beta/models/gemini-3.5-flash-lite:generateContent'
        local=Endpoint('http://127.0.0.1:'+str(server.server_port),allow_http=True,
            private_hosts=frozenset({'127.0.0.1'}))
        return request_json(local,method,path,body,**kwargs)
    monkeypatch.setenv('GEMINI_API_KEY',secret)
    monkeypatch.setattr(gateway_module,'request_json',local_request)
    gateway=gateway_module.ModelGateway(gateway_module.GatewayConfig(provider='gemini',model='gemini-3.5-flash-lite'))
    progress=VerificationProgress();progress.stage='first_agent';progress.gateways.append(('first_agent',gateway))
    try:
        with pytest.raises(RuntimeError) as caught:
            gateway.generate([dict(role='user',content='ready')],max_output_tokens=32)
        # Failed request cannot trigger a second network attempt.
        with pytest.raises(RuntimeError):gateway.generate([dict(role='user',content='again')],max_output_tokens=32)
    finally:
        server.shutdown();server.server_close();thread.join()
    report=progress.report()
    assert len(requests)==1 and report['retries']==0
    assert report['diagnostic']['category']==category
    assert report['diagnostic']['provider_status']==status
    assert report['diagnostic']['provider_reasons']==([reason] if reason else [])
    assert report['diagnostic']['invalid_fields']==([{'generation_config.max_output_tokens':'generationConfig.maxOutputTokens',
        'contents[0].parts[0].text':'contents[].parts[].text'}[field]] if field else [])
    assert report['diagnostic']['code']=='HTTP_400' and report['actual_api_calls'] is None
    assert secret not in json.dumps(report) and secret not in str(caught.value)
    evidence=gateway.evidence()
    assert secret not in evidence.model_dump_json()
    assert evidence.calls[0].usage_source=='unknown' and evidence.calls[0].reserved_tokens>0
    assert evidence.status!='passed'


@pytest.mark.parametrize('payload',[
    {'error':{'status':'private-status','message':'private','details':[{'@type':'type.googleapis.com/google.rpc.ErrorInfo','reason':'private-reason'}]}},
    {'error':{'status':[],'details':{'private':'private'}}},
    {'error':{'details':[{'@type':'type.googleapis.com/google.rpc.BadRequest','fieldViolations':[
        {'field':'generationConfig.private-secret','description':'private'}]}]}},
    {'error':{'status':'INVALID_ARGUMENT','status_duplicate':'private'}},
])
def test_error_summary_never_copies_unknown_provider_strings(payload):
    from agentguard.model_gateway.gateway import GeminiHTTPError
    error=GeminiHTTPError.from_response(400,json.dumps(payload).encode())
    assert 'private' not in error.details.model_dump_json()
    assert 'private' not in repr(error)


def test_old_model_ledger_serialization_does_not_gain_empty_error_fields():
    from agentguard.model_gateway.gateway import GatewayConfig, ModelGateway, GatewayEvidence
    gateway=ModelGateway(GatewayConfig(provider='mock',mode='mock',model='fixture'))
    gateway.generate([dict(role='user',content='ready')],max_output_tokens=32)
    data=gateway.evidence().model_dump(mode='json')
    assert 'provider_error' not in data['calls'][0]
    assert GatewayEvidence.model_validate(data).fingerprint==data['fingerprint']


@pytest.mark.parametrize('body',[b'<html>private</html>',b'{"error":{"status":"INVALID_ARGUMENT","status":"private"}}',
    b'{"error":{"code":401,"status":"INVALID_ARGUMENT","message":"private"}}'])
def test_malformed_or_inconsistent_error_body_preserves_only_http_status(body):
    from agentguard.model_gateway.gateway import GeminiHTTPError
    error=GeminiHTTPError.from_response(400,body)
    assert error.details.model_dump()==dict(http_status=400,status=None,reasons=[],fields=[])


@pytest.mark.parametrize('error_status',[400,403])
def test_completed_usage_cannot_carry_contradictory_http_failure(error_status):
    from agentguard.model_gateway.gateway import GatewayConfig, ModelGateway, GatewayEvidence
    gateway=ModelGateway(GatewayConfig(provider='mock',mode='mock',model='fixture'))
    gateway.generate([dict(role='user',content='ready')],max_output_tokens=32)
    data=gateway.evidence().model_dump(mode='json');data.pop('fingerprint')
    data['calls'][0]['provider_error']=dict(http_status=error_status)
    with pytest.raises(ValueError):GatewayEvidence.model_validate(data)


def test_auth_key_type_reason_is_preserved_without_free_text():
    from agentguard.model_gateway.gateway import GeminiHTTPError
    error=GeminiHTTPError.from_response(401,json.dumps({'error':{'code':401,
        'status':'UNAUTHENTICATED','message':'private-response','details':[{
            '@type':'type.googleapis.com/google.rpc.ErrorInfo',
            'reason':'ACCESS_TOKEN_TYPE_UNSUPPORTED','metadata':{'consumer':'private-project'}}]}}).encode())
    assert error.details.status=='UNAUTHENTICATED'
    assert error.details.reasons==['ACCESS_TOKEN_TYPE_UNSUPPORTED']
    assert 'private' not in error.details.model_dump_json()


def test_aq_authorization_key_is_sent_unchanged_only_in_api_key_header(monkeypatch):
    import agentguard.model_gateway.gateway as module
    secret='AQ.offline-fixture-not-a-real-key'
    monkeypatch.setenv('GEMINI_API_KEY',secret)
    def request(endpoint,method,path,body,**kwargs):
        assert kwargs['headers']=={'x-goog-api-key':secret}
        assert endpoint.base_url=='https://generativelanguage.googleapis.com'
        assert secret not in path and secret not in json.dumps(body)
        return {'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'ready'}]}}],
            'usageMetadata':{'promptTokenCount':5,'candidatesTokenCount':1,'totalTokenCount':6},
            'modelVersion':'offline-fixture'}, {},200
    monkeypatch.setattr(module,'request_json',request)
    gateway=module.ModelGateway(module.GatewayConfig(provider='gemini',model='gemini-3.5-flash-lite'))
    assert gateway.generate([dict(role='user',content='ready')],max_output_tokens=32)=='ready'
    assert secret not in gateway.evidence().model_dump_json()
