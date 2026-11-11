"""Offline diagnostics must retain stage evidence, never exception/provider text."""
import json
from types import SimpleNamespace

import pytest


def test_configuration_failure_has_safe_stage_and_no_network(tmp_path, monkeypatch):
    from scripts import check_gemini as script
    import agentguard.model_gateway.gateway as gateway
    secret = 'offline-secret-not-for-output'
    monkeypatch.setenv('GEMINI_API_KEY', secret)
    monkeypatch.setattr(gateway, 'request_json', lambda *a, **k: pytest.fail('Network forbidden'))
    output = tmp_path / 'diagnosis.json'
    monkeypatch.setattr('sys.argv', ['check_gemini.py', '--live', '--model', secret + '/invalid', '--result', str(output)])
    with pytest.raises(SystemExit):
        script.main()
    data = json.loads(output.read_text())
    assert data['stage'] == 'configuration'
    assert data['diagnostic']['category'] == 'configuration'
    assert data['actual_api_calls'] == 0
    assert data['phase5_complete'] is False
    assert secret not in output.read_text()


def test_execution_exception_retains_checkpoint_without_exception_text(tmp_path, monkeypatch):
    from scripts import check_gemini as script
    from agentguard.orchestration.controller import RunController
    secret = 'offline-sensitive-provider-body'
    monkeypatch.setenv('GEMINI_API_KEY', secret)
    def fail(*a, **k):
        raise ValueError(secret)
    monkeypatch.setattr(RunController, 'run', fail)
    output = tmp_path / 'diagnosis.json'
    monkeypatch.setattr('sys.argv', ['check_gemini.py', '--live', '--result', str(output)])
    with pytest.raises(SystemExit):
        script.main()
    data = json.loads(output.read_text())
    assert data['stage'] == 'first_agent'
    assert data['diagnostic']['category'] == 'execution_or_validation'
    assert data['gateways'][0]['calls'] == []
    assert secret not in output.read_text()


@pytest.mark.parametrize('failure,code,category,http', [
    ('http400', 'HTTP_400', 'provider_http', 400),
    ('http401', 'HTTP_401', 'credentials', 401),
    ('http403', 'HTTP_403', 'model_access', 403),
    ('http404', 'HTTP_404', 'model_access', 404),
    ('http429', 'HTTP_429', 'provider_rate_limit', 429),
    ('missing', 'PROVIDER_USAGE_MISSING', 'usage', None),
    ('inconsistent', 'PROVIDER_USAGE_INCONSISTENT', 'usage', None),
    ('overflow', 'PROVIDER_USAGE_RESERVATION_EXCEEDED', 'usage', None),
    ('truncated', 'PROVIDER_MAX_TOKENS', 'output_limit', None),
])
def test_provider_failures_are_distinct_and_safe(monkeypatch, failure, code, category, http):
    import agentguard.model_gateway.gateway as module
    from scripts.check_gemini import VerificationProgress
    secret = 'offline-sensitive-body'
    monkeypatch.setenv('GEMINI_API_KEY', secret)
    def request(*args, **kwargs):
        if failure.startswith('http'):
            raise RuntimeError('HTTP status ' + failure[4:])
        usage = dict(promptTokenCount=5, candidatesTokenCount=2, totalTokenCount=7)
        if failure == 'inconsistent': usage['totalTokenCount'] = 8
        if failure == 'overflow': usage.update(candidatesTokenCount=1000, totalTokenCount=1005)
        data = dict(candidates=[dict(finishReason='MAX_TOKENS' if failure == 'truncated' else 'STOP', content={'parts': [{'text': secret}]})], modelVersion=secret)
        if failure != 'missing': data['usageMetadata'] = usage
        return data, {}, 200
    monkeypatch.setattr(module, 'request_json', request)
    gateway = module.ModelGateway(module.GatewayConfig(provider='gemini', model='fixture'))
    progress = VerificationProgress()
    progress.stage = 'first_agent'
    progress.gateways.append(('first_agent', gateway))
    with pytest.raises(RuntimeError):
        gateway.generate([dict(role='user', content='ready')], max_output_tokens=32)
    data = progress.report()
    assert data['diagnostic']['code'] == code
    assert data['diagnostic']['category'] == category
    assert data['diagnostic']['http_status'] == http
    assert data['actual_api_calls'] is None  # Ledger entry cannot prove the server received it.
    assert data['gateways'][0]['calls'][0]['usage_source'] == 'unknown'
    assert data['gateways'][0]['calls'][0]['reserved_tokens'] > 0
    assert secret not in json.dumps(data)


def test_pre_network_budget_denial_and_calibration_are_separate():
    from agentguard.model_gateway.gateway import GatewayConfig, ModelGateway
    from scripts.check_gemini import VerificationProgress
    gateway = ModelGateway(GatewayConfig(provider='mock', mode='mock', model='fixture', max_total_tokens=1))
    progress = VerificationProgress()
    progress.stage = 'first_agent'
    progress.gateways.append(('first_agent', gateway))
    with pytest.raises(RuntimeError): gateway.generate([dict(role='user', content='ready')])
    data = progress.report()
    assert data['diagnostic']['category'] == 'budget_or_rate_policy'
    assert data['actual_api_calls'] == 0
    progress = VerificationProgress()
    progress.stage = 'judge'
    progress.record_run('judge', SimpleNamespace(status='failed', cleanup_status='verified', policy_status='violation', error_stage=None,
        evaluations=[SimpleNamespace(kind='judge', status='inconclusive', details={'disagreements': [{'reason': 'private'}]})]))
    data = progress.report()
    assert data['diagnostic']['category'] == 'judge_calibration'
    assert 'private' not in json.dumps(data)
