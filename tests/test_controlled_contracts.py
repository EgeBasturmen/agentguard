"""Source selection and fail-closed controlled evidence independent of Docker."""
from pathlib import Path
import pytest
from agentguard.execution.agent_spec import AgentLaunchSpec
from agentguard.execution.ipc_protocol import decode_ipc_message, ToolCallRequest, IPCProtocolError
from agentguard.core.result import RunResult, AssertionResult
from agentguard.core.outcome import effective_pass
from agentguard.contracts.compatibility import controlled_context
from agentguard.execution.records import RuntimeManifest


def test_source_bundle_is_explicit_and_never_imported(tmp_path):
    (tmp_path/'agent.py').write_text('raise RuntimeError("must never import on host")')
    (tmp_path/'.env').write_text('secret')
    (tmp_path/'Dockerfile').write_text('RUN execute-untrusted-code')
    (tmp_path/'setup.py').write_text('raise RuntimeError("setup executed")')
    spec=AgentLaunchSpec(reference='agent:Agent',source_dir=tmp_path)
    files, digest=spec.sources()
    assert set(files)=={'agent.py'}
    (tmp_path/'.env').write_text('changed secret')
    assert spec.sources()[1]==digest
    (tmp_path/'agent.py').write_text('different source')
    assert spec.sources()[1]!=digest


@pytest.mark.parametrize('name',['../outside.py','.env','Dockerfile','/root/secret.py','dir\\bad.py'])
def test_source_selection_rejects_escapes_and_non_python(tmp_path,name):
    (tmp_path/'agent.py').write_text('')
    with pytest.raises((ValueError,OSError)):
        AgentLaunchSpec(reference='agent:Agent',source_dir=tmp_path,include_files=[name]).sources()


@pytest.mark.parametrize('payload',[
    b'{"protocol_version":true,"call_id":"a","tool_name":"x","arguments":{}}',
    b'{"protocol_version":1.0,"call_id":"a","tool_name":"x","arguments":{}}',
    b'{"protocol_version":1,"call_id":"a","tool_name":"x","arguments":{"x":NaN}}',
    b'{"protocol_version":1,"call_id":"a","call_id":"b","tool_name":"x","arguments":{}}',
])
def test_wire_rejects_ambiguous_json(payload):
    with pytest.raises(IPCProtocolError): decode_ipc_message(payload,ToolCallRequest)


def valid_result():
    return RunResult(scenario_name='x', passed=True, status='passed',evaluation_status='complete',
        assertions=[AssertionResult(name='a',passed=True)],execution_context=controlled_context(),
        cleanup_status='verified',policy_status='passed',run_manifest=RuntimeManifest(run_id='a'*32,
        image_id='sha256:'+'b'*64,base_digest='python@sha256:'+'c'*64,source_digest='d'*64,
        worker_digest='e'*64,container_id='f'*64,started=True,completed=True))


@pytest.mark.parametrize('field,value', [('cleanup_status','unverified'),('cleanup_status','not_required'),
    ('policy_status','unknown'),('policy_status','violation'),('run_manifest',None)])
def test_incomplete_controlled_evidence_never_passes(field,value):
    result=valid_result()
    assert effective_pass(result)
    setattr(result,field,value)
    assert not effective_pass(result)


def test_missing_lifecycle_blocks_even_zero_exit():
    result=valid_result()
    result.run_manifest.completed=False
    assert not effective_pass(result)
