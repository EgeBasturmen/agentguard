"""Actual isolated agents using filesystem, dataset, OpenAPI, MCP, replay and mock routes."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import pytest
from phase3_service import Service,OPENAPI
from test_phase3_integration import endpoint,scenario
from agentguard.sdk import (IntegrationRunner,PythonAgent,ContainerAgent,ContainerLaunchSpec,FilesystemEnvironment,
    DocumentEnvironment,MemoryEnvironment,HTTPEnvironment,OpenAPIConnector,MCPConnector,RoutedTool,ReplayEntry,ToolContract,environment_tools)
from agentguard.core.scenario import Scenario
from agentguard.core.outcome import effective_pass
from agentguard.execution.agent_spec import AgentLaunchSpec
from agentguard.security.policy import SecurityPolicy

pytestmark=pytest.mark.docker
SOURCE="""
class FileAgent:
    def run(self,task,sandbox):
        sandbox.call('file.write',{'filename':'note.txt','content':'done'})
        return sandbox.call('file.read',{'filename':'note.txt'})
class DocumentAgent:
    async def run(self,task,sandbox):
        items=sandbox.call('document.search',{'query':'world'})
        return items[0]['content']
class OpenAPIAgent:
    def run(self,task,sandbox):
        return str(sandbox.call('record.update',{'name':'note','body':{'value':'done'}})['value'])
class MCPAgent:
    def run(self,task,sandbox):
        return str(sandbox.call('record.update',{'name':'note','value':'done'})['value'])
class DeniedAgent:
    def run(self,task,sandbox):
        try: sandbox.call('record.update',{'name':'protected','value':'broken'})
        except RuntimeError: pass
        sandbox.call('record.update',{'name':'note','value':'done'})
class RecordedAgent:
    def run(self,task,sandbox): return str(sandbox.call('record.update',{'name':'note','value':'done'}))
"""

def python_agent(tmp_path,name):
    (tmp_path/'agent.py').write_text(SOURCE)
    return PythonAgent(AgentLaunchSpec(reference='agent:'+name,source_dir=tmp_path))


def test_real_filesystem_is_independent_and_cleaned(tmp_path):
    env=FilesystemEnvironment()
    spec=Scenario(name='workspace',task='edit note',initial_state={'files':{'note.txt':'draft'}},
        expected_state=[{'path':'files.note\\.txt','value':'done'}],tool_expectations=[{'tool_name':'file.write','times':1}])
    result=IntegrationRunner().run(spec,python_agent(tmp_path,'FileAgent'),env,environment_tools(env),
        policy=SecurityPolicy(allowed_tools=frozenset({'file.read','file.write'}),resource_scopes={'file.write':{'filename':frozenset({'note.txt'})}}))
    assert effective_pass(result),result.model_dump()
    assert result.worker_output=='done' and not Path(env.root).exists()


def test_real_immutable_document_dataset(tmp_path):
    env=DocumentEnvironment()
    spec=Scenario(name='retrieval',task='find world',initial_state={'documents':{'d1':{'content':'hello world','source':'fixture-v1'}}},
        tool_expectations=[{'tool_name':'document.search','times':1}])
    result=IntegrationRunner().run(spec,python_agent(tmp_path,'DocumentAgent'),env,environment_tools(env),
        policy=SecurityPolicy(allowed_tools=frozenset({'document.search'})))
    assert effective_pass(result),result.model_dump()
    assert result.worker_output=='hello world'


@pytest.mark.parametrize('kind',['openapi','mcp'])
def test_container_agent_calls_existing_network_tool_via_broker(tmp_path,kind):
    with Service() as service:
        env=HTTPEnvironment(endpoint(service),True)
        if kind=='openapi':
            connector=OpenAPIConnector(endpoint(service));contract=connector.discover(OPENAPI)[0]
            agent=python_agent(tmp_path,'OpenAPIAgent')
        else:
            connector=MCPConnector(endpoint(service,'/mcp'),'1.0.0');contract=connector.discover()[0]
            agent=python_agent(tmp_path,'MCPAgent')
        contract=contract.model_copy(update={'effect_type':'write'})
        route=RoutedTool(contract,mode='staging',target=connector,approved_effect=True)
        policy=SecurityPolicy(allowed_tools=frozenset({'record.update'}),resource_scopes={'record.update':{'name':frozenset({'note'})}},tool_timeout_seconds=10)
        result=IntegrationRunner().run(scenario(True),agent,env,[route],policy=policy)
        assert effective_pass(result),result.model_dump()
        assert result.audit[-1].status=='completed' and result.worker_output=='done'
        assert not service.spaces and not service.sessions


def test_denied_staging_resource_never_reaches_external_service(tmp_path):
    with Service() as service:
        connector=MCPConnector(endpoint(service,'/mcp'),'1.0.0')
        contract=connector.discover()[0].model_copy(update={'effect_type':'write'})
        result=IntegrationRunner().run(scenario(True),python_agent(tmp_path,'DeniedAgent'),HTTPEnvironment(endpoint(service),True),
            [RoutedTool(contract,mode='staging',target=connector,approved_effect=True)],
            policy=SecurityPolicy(allowed_tools=frozenset({'record.update'}),resource_scopes={'record.update':{'name':frozenset({'note'})}},tool_timeout_seconds=10))
        assert not effective_pass(result) and result.policy_status=='violation'
        assert any(item.reason=='RESOURCE_SCOPE_DENIED' for item in result.audit)
        # Only one external tool call: the protected attempt was stopped before transport.
        assert len([c for c in service.calls if c[0]=='POST' and '/spaces/' in c[1] and c[1].endswith('/mcp')])==3
        assert not service.spaces


def test_approved_replay_is_bound_to_arguments_fixture_and_version(tmp_path):
    contract=ToolContract(name='record.update',version='1.0.0',adapter='custom@1.0.0',input_schema={'type':'object','properties':{'name':{'type':'string'},'value':{'type':'string'}},'required':['name','value'],'additionalProperties':False},output_schema={'type':'string'})
    case=scenario(True)
    after={'records':{'note':'done','protected':'keep'}}
    entry=ReplayEntry.capture(contract,{'name':'note','value':'done'},case.initial_state,'done',after,approved=True)
    route=RoutedTool(contract,mode='replay',replay_entries=(entry,))
    policy=SecurityPolicy(allowed_tools=frozenset({'record.update'}))
    result=IntegrationRunner().run(case,python_agent(tmp_path,'RecordedAgent'),MemoryEnvironment(),[route],policy=policy)
    assert effective_pass(result),result.model_dump()
    assert any('Replay' in gap for gap in result.coverage_gaps)
    route.contract=contract.model_copy(update={'version':'2.0.0'})
    missed=IntegrationRunner().run(case,python_agent(tmp_path,'RecordedAgent'),MemoryEnvironment(),[route],policy=policy)
    assert not effective_pass(missed) and 'Replay miss' in missed.error_message


def test_schema_mock_is_only_interface_evidence(tmp_path):
    contract=ToolContract(name='record.update',version='1.0.0',adapter='custom@1.0.0',input_schema={'type':'object'},output_schema={'type':'string'})
    case=Scenario(name='interface',task='call',initial_state={},tool_expectations=[{'tool_name':'record.update','times':1}])
    result=IntegrationRunner().run(case,python_agent(tmp_path,'RecordedAgent'),MemoryEnvironment(),
        [RoutedTool(contract,mode='mock',mock_result='interface-response')],policy=SecurityPolicy(allowed_tools=frozenset({'record.update'})))
    assert effective_pass(result) and any('Schema mock' in gap for gap in result.coverage_gaps)


def test_language_neutral_shell_image_obeys_same_ipc_broker(tmp_path):
    # Non-Python worker; the base is pinned and no untrusted build commands execute.
    image_info=json.loads(subprocess.check_output(['docker','image','inspect','python:3.11-slim']))[0]
    base=image_info['RepoDigests'][0]
    script='#!/bin/sh\nIFS= read -r start\nid=$(printf \'%s\' "$start" | sed -n \'s/.*"run_id":"\\([^" ]*\\)".*/\\1/p\')\nprintf \'{"protocol_version":2,"type":"started","run_id":"%s"}\\n\' "$id"\nprintf \'%s\\n\' \'{"protocol_version":1,"type":"tool_call","call_id":"shell-1","tool_name":"file.write","arguments":{"filename":"note.txt","content":"done"}}\'\nIFS= read -r result\nprintf \'{"protocol_version":2,"type":"completed","run_id":"%s","status":"ok","output":"shell complete"}\\n\' "$id"\n'
    (tmp_path/'agent.sh').write_text(script,encoding='utf-8',newline='')
    (tmp_path/'Dockerfile').write_text('FROM '+base+'\nCOPY agent.sh /agent.sh\nENTRYPOINT ["/bin/sh","/agent.sh"]\n')
    built=subprocess.run(['docker','build','--network=none','--quiet',str(tmp_path)],capture_output=True,text=True,timeout=60,check=True)
    image=built.stdout.strip().splitlines()[-1]
    spec=Scenario(name='shell',task='edit',initial_state={'files':{'note.txt':'draft'}},expected_state=[{'path':'files.note\\.txt','value':'done'}])
    env=FilesystemEnvironment()
    result=IntegrationRunner().run(spec,ContainerAgent(ContainerLaunchSpec(image=image)),env,environment_tools(env),policy=SecurityPolicy(allowed_tools=frozenset({'file.write'})))
    assert effective_pass(result),result.model_dump()
    assert result.worker_output=='shell complete' and result.run_manifest.artifact_kind=='container_image'


def test_custom_protocol_adapter_uses_existing_engine_and_contract(tmp_path):
    from examples.custom_connector import ExistingJSONTool
    with Service() as service:
        target=ExistingJSONTool(endpoint(service))
        route=RoutedTool(target.contract(),mode='staging',target=target,approved_effect=True)
        result=IntegrationRunner().run(scenario(True),python_agent(tmp_path,'MCPAgent'),HTTPEnvironment(endpoint(service),True),[route],
            policy=SecurityPolicy(allowed_tools=frozenset({'record.update'}),resource_scopes={'record.update':{'name':frozenset({'note'})}},tool_timeout_seconds=10))
        assert effective_pass(result),result.model_dump()
        assert not service.spaces


def test_mcp_session_cleanup_failure_is_unverified(tmp_path):
    with Service() as service:
        connector=MCPConnector(endpoint(service,'/mcp'),'1.0.0')
        contract=connector.discover()[0].model_copy(update={'effect_type':'write'})
        service.session_cleanup_failure=True
        result=IntegrationRunner().run(scenario(True),python_agent(tmp_path,'MCPAgent'),HTTPEnvironment(endpoint(service),True),
            [RoutedTool(contract,mode='staging',target=connector,approved_effect=True)],
            policy=SecurityPolicy(allowed_tools=frozenset({'record.update'}),tool_timeout_seconds=10))
        assert not effective_pass(result)
        assert result.cleanup_status=='unverified',result.model_dump()


def simulator_put(state,name,value):
    state['records'][name]=value
    return value


def test_same_plan_fingerprint_is_independent_of_provision_duration(tmp_path):
    import time
    class SlowMemory(MemoryEnvironment):
        def provision(self,fixture):
            time.sleep(1.1)
            super().provision(fixture)
    contract=ToolContract(name='record.update',version='1.0.0',adapter='custom@1.0.0',input_schema={'type':'object'},output_schema={'type':'string'})
    route=RoutedTool(contract,handler=simulator_put)
    policy=SecurityPolicy(allowed_tools=frozenset({'record.update'}))
    runner=IntegrationRunner();agent=python_agent(tmp_path,'RecordedAgent')
    fast=runner.run(scenario(True),agent,MemoryEnvironment(),[route],policy=policy)
    slow=runner.run(scenario(True),agent,SlowMemory(),[route],policy=policy)
    assert effective_pass(fast) and effective_pass(slow),(fast.model_dump(),slow.model_dump())
    assert fast.comparison_manifest.fingerprint==slow.comparison_manifest.fingerprint
