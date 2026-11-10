"""Phase 3 red tests: explicit network policy, schema and deterministic routing."""
import pytest

def test_endpoint_rejects_private_network_by_default():
    from agentguard.integrations.http import Endpoint, request_json
    with pytest.raises(PermissionError):
        request_json(Endpoint(base_url='http://127.0.0.1:9',allow_http=True), 'GET', '/')

@pytest.mark.parametrize('path',['//evil.test/x','/../secret','/%2e%2e/secret','/x%2fy','/x\\y'])
def test_endpoint_rejects_path_escapes(path):
    from agentguard.integrations.http import validate_path
    with pytest.raises(ValueError): validate_path(path)

def test_replay_miss_never_calls_live_target():
    from agentguard.integrations.tools import ToolContract, RoutedTool
    contract=ToolContract(name='lookup',version='1.0.0',adapter='custom@1.0.0',input_schema={'type':'object','additionalProperties':False})
    route=RoutedTool(contract=contract,mode='replay')
    with pytest.raises(LookupError,match='Replay miss'): route({},)

def test_mock_cannot_satisfy_business_state_preflight():
    from agentguard.integrations.sdk import preflight
    from agentguard.integrations.agents import PythonAgent
    from agentguard.environments import MemoryEnvironment
    from agentguard.integrations.tools import ToolContract,RoutedTool
    from agentguard.execution.agent_spec import AgentLaunchSpec
    from agentguard.core.scenario import Scenario
    from agentguard.security.policy import SecurityPolicy
    scenario=Scenario(name='x',task='x',initial_state={'x':0},expected_state=[{'path':'x','value':1}])
    agent=PythonAgent(AgentLaunchSpec(reference='unused:Agent',source_dir=__import__('pathlib').Path('.')))
    tool=RoutedTool(ToolContract(name='x',version='1.0.0',adapter='custom@1.0.0',input_schema={'type':'object'}),mode='mock',mock_result={'x':1})
    result=preflight(scenario,agent,MemoryEnvironment(),[tool],SecurityPolicy(allowed_tools=frozenset({'x'})))
    assert not result.supported and any('mock' in gap.lower() for gap in result.issues)

def test_openapi_rejects_remote_refs_and_unpinned_versions():
    from agentguard.integrations.tools import OpenAPIConnector
    from agentguard.integrations.http import Endpoint
    connector=OpenAPIConnector(Endpoint(base_url='https://example.com'))
    with pytest.raises(ValueError): connector.discover({'openapi':'3.2.0','paths':{}})
    with pytest.raises(ValueError): connector.discover({'openapi':'3.1.1','info':{'version':'1.0.0'},'paths':{'/x':{'post':{'operationId':'x','requestBody':{'content':{'application/json':{'schema':{'$ref':'https://evil/x'}}}},'responses':{'200':{'description':'ok'}}}}}})


def test_replay_approval_must_be_boolean_not_truthy_text():
    from agentguard.integrations.tools import ReplayEntry
    with pytest.raises(ValueError): ReplayEntry('a'*64,'result',{},approved='false')

def test_route_identity_tracks_endpoint_and_operation_binding():
    from agentguard.integrations.tools import OpenAPIConnector,RoutedTool
    from agentguard.integrations.http import Endpoint
    from dataclasses import replace
    document={'openapi':'3.1.1','info':{'version':'1.0.0'},'paths':{'/x':{'get':{'operationId':'x','responses':{'200':{'content':{'application/json':{'schema':{'type':'object'}}}}}}}}}
    connector=OpenAPIConnector(Endpoint('https://example.com'))
    contract=connector.discover(document)[0]
    a=RoutedTool(contract,mode='staging',target=connector,approved_effect=True).identity()
    other=connector.bound(Endpoint('https://other.example.com'))
    b=RoutedTool(contract,mode='staging',target=other,approved_effect=True).identity()
    assert a!=b


def test_private_host_grant_never_allows_link_local_metadata(monkeypatch):
    import socket
    from agentguard.integrations.http import Endpoint,request_json
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('169.254.169.254',80))])
    def connect(*a,**k): raise AssertionError('Attempted metadata connection')
    monkeypatch.setattr(socket,'create_connection',connect)
    with pytest.raises(PermissionError):
        request_json(Endpoint('http://staging.test',allow_http=True,private_hosts=frozenset({'staging.test'})),'GET','/')


@pytest.mark.parametrize('field,value',[('allow_http','false'),('private_hosts','127.0.0.1'),('timeout_seconds',True),('timeout_seconds',float('nan'))])
def test_endpoint_security_grants_are_strict(field,value):
    from agentguard.integrations.http import Endpoint
    config={'base_url':'https://example.com',field:value}
    with pytest.raises((ValueError,TypeError)): Endpoint(**config)


def test_staging_approval_is_not_coerced_from_text():
    from agentguard.integrations.tools import ToolContract,RoutedTool
    contract=ToolContract(name='x',version='1.0.0',adapter='custom@1.0.0',input_schema={'type':'object'})
    with pytest.raises(ValueError): RoutedTool(contract,mode='staging',approved_effect='false')


def test_nested_schema_resource_cannot_change_local_ref_meaning():
    from jsonschema import Draft202012Validator,ValidationError
    from agentguard.integrations.schema import validate
    schema={'type':'object','$defs':{'value':{'type':'integer'}},'properties':{
        'nested':{'$id':'https://schemas.example/nested','type':'object','$defs':{'value':{'type':'string'}},
                  'properties':{'value':{'$ref':'#/$defs/value'}},'required':['value']}},'required':['nested']}
    value={'nested':{'value':1}}
    with pytest.raises(ValidationError): Draft202012Validator(schema).validate(value)
    # This resource-scoped reference profile must be rejected, never flattened against the wrong root.
    with pytest.raises(ValueError): validate(schema,value)


def test_unknown_schema_dialect_is_rejected_instead_of_reinterpreted():
    from agentguard.integrations.schema import validate
    with pytest.raises(ValueError): validate({'$schema':'https://schemas.example/custom-dialect','type':'integer'},1)


@pytest.mark.parametrize('schema',[{'$id':'https://schemas.example/nested','type':'object'},{'type':123}])
def test_sealed_report_cannot_accept_an_unsupported_tool_schema(schema):
    import hashlib
    from agentguard.core.manifest import canonical_json
    from agentguard.integrations.evidence import IntegrationEvidence
    body={'schema_version':1,'agent_transport':'python','environment':{'kind':'memory'},
        'tools':[{'contract':{'name':'x','version':'1.0.0','adapter':'custom@1.0.0','input_schema':schema},'mode':'simulator'}],
        'modes':['simulator'],'required_capabilities':[]}
    body['fingerprint']=hashlib.sha256(canonical_json(body).encode()).hexdigest()
    with pytest.raises(ValueError): IntegrationEvidence.model_validate(body)


def test_container_context_does_not_claim_python_source_provenance():
    from agentguard.contracts.compatibility import controlled_context
    context=controlled_context('container.ipc')
    assert any('prebuilt' in message.lower() and 'provenance' in message.lower() for message in context.limitations)
    assert not any('Only explicitly included Python' in message for message in context.limitations)
