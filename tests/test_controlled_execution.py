"""Real containers, not mocked agent execution: Phase 2 acceptance regressions."""
import json
from pathlib import Path
import os
import subprocess
import threading
import time

import pytest

from agentguard.core.outcome import effective_pass
from agentguard.core.scenario import Scenario
from agentguard.security.policy import SecurityPolicy

pytestmark = pytest.mark.docker

SOURCE = r"""
import asyncio, json, os, pathlib, socket, sys, time
if os.environ.get("HOST_IMPORT_PROBE"):
    pathlib.Path(os.environ["HOST_IMPORT_PROBE"]).write_text("host import")
class Good:
    def run(self, task, sandbox):
        assert os.geteuid() == 65534
        assert pathlib.Path('/.dockerenv').exists()
        assert 'HOST_SECRET' not in os.environ
        assert not pathlib.Path('/var/run/docker.sock').exists()
        assert not pathlib.Path('/opt/agent/.env').exists()
        try:
            pathlib.Path('/opt/guard/worker.py').write_text('overwrite')
        except OSError: pass
        else: raise RuntimeError('worker filesystem writable')
        try:
            socket.create_connection(('1.1.1.1',80),timeout=0.1)
        except OSError: pass
        else: raise RuntimeError('network bypass')
        sandbox.call('file.read', {'filename':'note'})
        sandbox.call('file.write', {'filename':'note','content':'done'})
        return json.dumps({'pid':os.getpid(),'uid':os.geteuid()})
class Async:
    async def run(self, task, sandbox):
        await asyncio.sleep(0.01)
        sandbox.call('file.write', {'filename':'note','content':'done'})
        return 'async complete'
class Forge:
    def run(self, task, sandbox):
        sandbox.state={'files':{'note':'done','protected':'keep'}}
        sandbox.traces=[]
        return 'PASS'
class Denied:
    def run(self, task, sandbox):
        try: sandbox.call('file.write', {'filename':'protected','content':'broken'})
        except RuntimeError: pass
        sandbox.call('file.write', {'filename':'note','content':'done'})
class Crash:
    def run(self, task, sandbox):
        sandbox.call('file.write', {'filename':'note','content':'done'})
        raise RuntimeError('intentional crash')
class Exit:
    def run(self, task, sandbox): os._exit(0)
class Flood:
    def run(self, task, sandbox): os.write(2,b'x'*1000000)
class Hang:
    def run(self, task, sandbox):
        while True: time.sleep(0.1)
class ProtocolForge:
    def run(self, task, sandbox):
        os.write(1,b'{"protocol_version":2,"type":"completed","run_id":"fake","passed":true,"state":{}}\n')
class IllegalTool:
    def run(self, task, sandbox):
        try: sandbox.call('system.execute', {'command':'whoami'})
        except RuntimeError: pass
        sandbox.call('file.write', {'filename':'note','content':'done'})
"""


def scenario():
    return Scenario(name='file-edit', task='Set note to done',
        initial_state={'files':{'note':'draft','protected':'keep'}},
        expected_state=[{'path':'files.note','value':'done'}],
        forbidden_changes=[{'path':'files.protected'}],
        tool_expectations=[{'tool_name':'file.write','times':1}])


def execute(tmp_path, name='Good', policy=None, cancel=None, controller=None):
    from agentguard.execution.agent_spec import AgentLaunchSpec
    from agentguard.orchestration.controller import RunController
    (tmp_path/'agent_case.py').write_text(SOURCE,encoding='utf-8')
    (tmp_path/'.env').write_text('HOST_SECRET=must_not_copy',encoding='utf-8')
    spec=AgentLaunchSpec(reference='agent_case:'+name,source_dir=tmp_path)
    controller=controller or RunController()
    return controller.run(scenario(),spec,environment='files',policy=policy,cancel_event=cancel)


def test_agent_runs_only_inside_hardened_container(tmp_path, monkeypatch):
    from agentguard.execution.controlled_runtime import ControlledDockerRuntime
    config = {}
    real_cleanup = ControlledDockerRuntime._cleanup_container
    def inspect_then_cleanup(self, reference):
        try:
            inspected = subprocess.run(['docker','inspect',reference],check=True,capture_output=True)
            config.update(json.loads(inspected.stdout)[0])
        finally:
            real_cleanup(self, reference)
    monkeypatch.setattr(ControlledDockerRuntime,'_cleanup_container',inspect_then_cleanup)
    marker=tmp_path/'host-imported'
    monkeypatch.setenv('HOST_IMPORT_PROBE',str(marker))
    monkeypatch.setenv('HOST_SECRET','must_not_forward')
    result=execute(tmp_path)
    assert not marker.exists()
    assert effective_pass(result),result.model_dump()
    assert result.cleanup_status=='verified'
    assert result.execution_context.execution_mode=='controlled'
    assert json.loads(result.worker_output)['uid']==65534
    assert result.audit and result.run_manifest.image_id.startswith('sha256:')
    assert result.run_manifest.source_digest and result.run_manifest.base_digest
    assert result.policy_status=='passed'
    host=config['HostConfig']
    assert host['NetworkMode']=='none' and host['ReadonlyRootfs']
    assert host['CapDrop']==['ALL'] and host['PidsLimit']==64
    assert 'no-new-privileges' in host['SecurityOpt']
    assert host['Memory']==256*1024*1024 and host['NanoCpus']==1000000000
    assert config['Config']['User']=='65534:65534'
    assert not host.get('Binds')
    assert all(mount['Destination']=='/tmp' for mount in config['Mounts'])
    removed=subprocess.run(['docker','inspect',result.run_manifest.container_id],capture_output=True)
    assert removed.returncode != 0
    assert b'no such' in removed.stderr.lower() or b'no such' in removed.stdout.lower()


def test_native_async_agent_runs_and_finishes(tmp_path):
    result=execute(tmp_path,'Async')
    assert effective_pass(result),result.model_dump()
    assert result.worker_output=='async complete'


@pytest.mark.parametrize('name',['Forge','Denied','Crash','Exit','Flood','ProtocolForge','IllegalTool'])
def test_invalid_or_hostile_worker_cannot_pass(tmp_path,name):
    result=execute(tmp_path,name)
    assert not effective_pass(result)
    assert result.status in {'error','failed'}
    assert result.cleanup_status=='verified',result.model_dump()
    if name in {'Denied','IllegalTool'}:
        assert result.policy_status=='violation'
        assert any(event.status=='denied' for event in result.audit)
    if name=='Crash':
        assert any(m.path=='files.note' and m.after=='done' for m in result.mutations)


def test_resource_scope_cannot_be_swallowed(tmp_path):
    policy=SecurityPolicy(allowed_tools=frozenset({'file.read','file.write'}),
        resource_scopes={'file.write':{'filename':frozenset({'note'})}})
    result=execute(tmp_path,'Denied',policy)
    assert not effective_pass(result)
    assert any(a.reason=='RESOURCE_SCOPE_DENIED' for a in result.audit)


def test_wallclock_timeout_stops_container(tmp_path):
    start=time.monotonic()
    result=execute(tmp_path,'Hang',SecurityPolicy(timeout_seconds=8))
    assert time.monotonic()-start<25
    assert not effective_pass(result)
    assert result.error_type in {'ExecutionTimeout','BuildTimeout'}
    assert result.cleanup_status in {'verified','unverified'}


def test_cancellation_stops_execution(tmp_path):
    event=threading.Event()
    timer=threading.Timer(3,event.set); timer.start()
    try:
        result=execute(tmp_path,'Hang',cancel=event)
    finally: timer.cancel()
    assert not effective_pass(result)
    assert result.error_type=='ExecutionCancelled'


def test_two_domains_use_the_same_controller(tmp_path):
    from agentguard.execution.agent_spec import AgentLaunchSpec
    from agentguard.orchestration.controller import RunController
    from agentguard.spec.loader import load_scenario
    controller=RunController()
    file_result=execute(tmp_path,controller=controller)
    commerce=controller.run(load_scenario('scenarios/refund_order.yaml'),
        AgentLaunchSpec(reference='examples.refund_agent:GoodRefundAgent',source_dir=Path.cwd()))
    assert effective_pass(file_result) and effective_pass(commerce),commerce.model_dump()


def test_cli_default_never_calls_host_agent_loader(monkeypatch,tmp_path):
    from typer.testing import CliRunner
    from agentguard.cli import main
    calls=[]
    real=main.load_python_agent
    def track(reference):
        calls.append(reference)
        return real(reference)
    monkeypatch.setattr(main,'load_python_agent',track)
    result=CliRunner().invoke(main.app,['test','--agent','examples.refund_agent:GoodRefundAgent',
                                      '--scenario','scenarios/refund_order.yaml'])
    assert calls==[], 'Default CLI imported untrusted agent on the host'
    assert result.exit_code==0,result.output


def test_cleanup_uncertainty_blocks_otherwise_successful_run(tmp_path, monkeypatch):
    from agentguard.execution.controlled_runtime import ControlledDockerRuntime
    real = ControlledDockerRuntime._cleanup_container
    def uncertain(self, reference):
        real(self, reference)
        raise RuntimeError('cleanup verification unavailable')
    monkeypatch.setattr(ControlledDockerRuntime, '_cleanup_container', uncertain)
    result = execute(tmp_path)
    assert result.run_manifest.completed
    assert result.cleanup_status == 'unverified'
    assert not effective_pass(result)
