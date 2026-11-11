"""Provider independent budgets and ledger: no silent free/unknown usage."""
import pytest


def test_budget_reservation_blocks_before_provider_call():
    from agentguard.model_gateway.gateway import ModelGateway,GatewayConfig
    config=GatewayConfig(provider='mock',model='fixture',mode='mock',max_total_tokens=2)
    gateway=ModelGateway(config)
    with pytest.raises(Exception):gateway.generate([{'role':'user','content':'long prompt'}],max_output_tokens=8)
    assert gateway.evidence().status=='blocked'


def test_mock_usage_is_never_labelled_live_provider_usage():
    from agentguard.model_gateway.gateway import ModelGateway,GatewayConfig
    gateway=ModelGateway(GatewayConfig(provider='mock',model='fixture',mode='mock',mock_output='fixture response'))
    assert gateway.generate([{'role':'user','content':'hello'}],max_output_tokens=32)=='fixture response'
    assert gateway.evidence().mode=='mock'
    assert gateway.evidence().calls[0].usage_source=='mock'


def test_gemini_credentials_are_header_only_and_cannot_leak_in_provider_metadata(monkeypatch):
    from agentguard.model_gateway.gateway import ModelGateway,GatewayConfig
    import agentguard.model_gateway.gateway as module
    secret='unit-only-credential';monkeypatch.setenv('GEMINI_API_KEY',secret)
    def request(endpoint,method,path,body,**kwargs):
        assert kwargs['headers']=={'x-goog-api-key':secret}
        assert secret not in path and secret not in str(body)
        return dict(candidates=[dict(finishReason='STOP',content={'parts':[{'text':secret}]})],usageMetadata={'promptTokenCount':5,'candidatesTokenCount':2,'totalTokenCount':7},modelVersion=secret),{},200
    monkeypatch.setattr(module,'request_json',request)
    gateway=ModelGateway(GatewayConfig(provider='gemini',model='fixture'))
    assert gateway.generate([{'role':'user','content':'Return ready'}],max_output_tokens=16)=='[REDACTED]'
    assert secret not in gateway.evidence().model_dump_json()


@pytest.mark.parametrize('mode',['missing_usage','overflow','error'])
@pytest.mark.integration
def test_real_http_errors_keep_usage_unverified_and_reserved(mode):
    from agentguard.model_gateway.gateway import ModelGateway,GatewayConfig
    from phase5_service import ModelService,model_config
    with ModelService(mode) as service:
        gateway=ModelGateway(GatewayConfig.model_validate(model_config(service)))
        with pytest.raises(RuntimeError):gateway.generate([{'role':'user','content':'hello'}],max_output_tokens=32)
        evidence=gateway.evidence()
        assert evidence.status=='unverified' and evidence.calls[0].reserved_tokens>0
        assert evidence.calls[0].usage_source=='unknown'
        if mode=='error':assert evidence.calls[0].error_code=='HTTP_503'


@pytest.mark.integration
def test_live_http_judge_requires_positive_and_negative_calibration():
    from agentguard.model_gateway.gateway import ModelGateway,GatewayConfig
    from agentguard.evaluation.registry import evaluate
    from agentguard.evaluation.specs import EvaluationSpec
    from phase5_service import ModelService,model_config
    spec=EvaluationSpec(id='judge',kind='judge',config=dict(rubric='Accept correct response; reject wrong',rubric_version='v1',labels=[dict(input='correct',expected=True,source='human:fixture-v1'),dict(input='wrong',expected=False,source='human:fixture-v1')]))
    with ModelService() as service:
        gateway=ModelGateway(GatewayConfig.model_validate(model_config(service)))
        finding=evaluate(spec,{'output':'ready','gateway':gateway})
        assert finding.status=='pass',finding
        assert len(service.calls)==3 and all(row.purpose=='judge' for row in gateway.evidence().calls)
    with ModelService('disagree') as service:
        gateway=ModelGateway(GatewayConfig.model_validate(model_config(service)))
        finding=evaluate(spec,{'output':'ready','gateway':gateway})
        assert finding.status=='inconclusive'
        assert finding.details['disagreements'][0]['expected'] is False


def test_live_smoke_reports_missing_credential_before_any_api_call(tmp_path,monkeypatch):
    import json
    from scripts.check_gemini import main
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)
    output=tmp_path/'verification.json'
    monkeypatch.setattr('sys.argv',['check_gemini.py','--live','--result',str(output)])
    with pytest.raises(SystemExit):main()
    evidence=json.loads(output.read_text())
    assert evidence['stage']=='configuration'
    assert evidence['actual_api_calls']==0
    assert evidence['error_type']=='CredentialUnavailable'


def test_gemini_truncation_has_a_safe_actionable_error_code(monkeypatch):
    import agentguard.model_gateway.gateway as module
    monkeypatch.setenv('GEMINI_API_KEY','unit-dummy')
    monkeypatch.setattr(module,'request_json',lambda *args,**kwargs:({'candidates':[{'finishReason':'MAX_TOKENS'}]}, {},200))
    gateway=module.ModelGateway(module.GatewayConfig(provider='gemini',model='fixture'))
    with pytest.raises(RuntimeError):gateway.generate([dict(role='user',content='ready')],max_output_tokens=32)
    assert gateway.evidence().calls[0].error_code=='PROVIDER_MAX_TOKENS'
