"""Real HTTP agent, OpenAPI and MCP discovery/calls and lifecycle."""
from copy import deepcopy
from dataclasses import replace
import pytest
from phase3_service import Service,OPENAPI
from agentguard.sdk import Endpoint,OpenAPIConnector,MCPConnector,HTTPEnvironment,HTTPAgent,IntegrationRunner,RoutedTool,contract_test
from agentguard.core.scenario import Scenario
from agentguard.core.outcome import effective_pass
from agentguard.security.policy import SecurityPolicy


def endpoint(service,suffix=''):
    return Endpoint(base_url=service.url+suffix,allow_http=True,private_hosts=frozenset({'127.0.0.1'}))

def scenario(tools=False):
    return Scenario(name='existing-api',task='done',initial_state={'records':{'note':'draft','protected':'keep'}},
        expected_state=[{'path':'records.note','value':'done'}],forbidden_changes=[{'path':'records.protected'}],
        tool_expectations=[{'tool_name':'record.update','times':1}] if tools else [])


def test_http_agent_uses_real_external_observer_and_cleans_namespace():
    with Service() as service:
        env=HTTPEnvironment(endpoint(service),allow_staging_writes=True)
        result=IntegrationRunner().run(scenario(),HTTPAgent(endpoint(service,'/agent')),env)
        assert effective_pass(result),result.model_dump()
        assert result.execution_context.execution_mode=='observed'
        assert not result.execution_context.capabilities.supports_tool_interception
        assert result.coverage_gaps and result.worker_output=='remote agent complete'
        assert not service.spaces


def test_http_agent_cannot_forge_state_or_verdict():
    with Service() as service:
        result=IntegrationRunner().run(scenario(),HTTPAgent(endpoint(service,'/agent-forge')),HTTPEnvironment(endpoint(service),True))
        assert not effective_pass(result)
        assert result.error_type=='ValueError'
        assert not service.spaces


def test_missing_capability_blocks_before_agent_or_fixture_effect():
    with Service() as service:
        result=IntegrationRunner().run(scenario(True),HTTPAgent(endpoint(service,'/agent')),HTTPEnvironment(endpoint(service),True),required_capabilities=['supports_tool_interception'])
        assert not effective_pass(result) and result.error_type=='IntegrationPreflightError'
        assert not service.calls and not service.spaces


def test_cleanup_uncertainty_blocks_successful_remote_agent():
    with Service() as service:
        service.cleanup_failure=True
        result=IntegrationRunner().run(scenario(),HTTPAgent(endpoint(service,'/agent')),HTTPEnvironment(endpoint(service),True))
        assert result.cleanup_status=='unverified' and not effective_pass(result)


@pytest.mark.parametrize('kind',['openapi','mcp'])
def test_existing_service_discovery_and_real_invocation(kind):
    with Service() as service:
        env=HTTPEnvironment(endpoint(service),True)
        assert contract_test(env,scenario().initial_state)
        env.provision(scenario().initial_state)
        try:
            if kind=='openapi':
                connector=OpenAPIConnector(endpoint(service));contracts=connector.discover(OPENAPI)
                args={'name':'note','body':{'value':'done'}}
                bound=connector.bound(env.bound_endpoint())
            else:
                connector=MCPConnector(endpoint(service,'/mcp'),'1.0.0');contracts=connector.discover()
                args={'name':'note','value':'done'}
                bound=connector.bound(env.bound_endpoint('/mcp'))
            contract=contracts[0].model_copy(update={'effect_type':'write'})
            route=RoutedTool(contract,mode='staging',target=bound,observer=env,approved_effect=True)
            state=deepcopy(scenario().initial_state)
            assert route(state,**args)=={'value':'done'}
            assert state==env.snapshot() and state['records']['note']=='done'
        finally: assert env.cleanup()=='verified'
        assert not service.sessions and not service.spaces


def test_mcp_version_mismatch_is_rejected_and_session_closed():
    with Service() as service:
        service.protocol='2026-07-28'
        with pytest.raises(ValueError,match='version'):
            MCPConnector(endpoint(service,'/mcp'),'1.0.0').discover()
        assert not service.sessions


def test_redirect_is_never_followed_to_metadata():
    from agentguard.integrations.http import request_json
    with Service() as service:
        with pytest.raises(PermissionError,match='redirect'):
            request_json(endpoint(service),'GET','/redirect')
        assert service.calls==[('GET','/redirect')]


def test_cli_integration_plan_runs_real_http_agent(tmp_path):
    import json
    from typer.testing import CliRunner
    from agentguard.cli.main import app
    with Service() as service:
        config={'schema_version':1,'scenario':scenario().model_dump(),
            'agent':{'transport':'http','endpoint':{'base_url':service.url+'/agent','allow_http':True,'private_hosts':['127.0.0.1']}},
            'environment':{'kind':'http','endpoint':{'base_url':service.url,'allow_http':True,'private_hosts':['127.0.0.1']},'allow_staging_writes':True},'tools':[], 'policy':{}}
        path=tmp_path/'plan.json';path.write_text(json.dumps(config))
        result=CliRunner().invoke(app,['integration',str(path),'--json-report',str(tmp_path/'result.json')])
        assert result.exit_code==0,result.output
        report=json.loads((tmp_path/'result.json').read_text())
        assert report['verdict']=='PASS' and report['result']['coverage_gaps']
        assert not service.spaces


def test_integration_manifest_tampering_cannot_keep_pass():
    with Service() as service:
        result=IntegrationRunner().run(scenario(),HTTPAgent(endpoint(service,'/agent')),HTTPEnvironment(endpoint(service),True))
        assert effective_pass(result)
        result.integration_manifest['modes']=['mock']
        assert not effective_pass(result)
