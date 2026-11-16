import os
import pytest
pytestmark=pytest.mark.unit

def test_controlled_container_command_has_exact_owned_recovery_identity():
    from agentguard.execution.controlled_runtime import ControlledDockerRuntime
    from agentguard.security.policy import SecurityPolicy
    command=ControlledDockerRuntime()._docker_create_command('agentguard-run-'+'a'*32,SecurityPolicy())
    labels=[command[index+1] for index,part in enumerate(command[:-1]) if part=='--label']
    assert 'io.agentguard.run_id='+'a'*32 in labels
    assert 'io.agentguard.owner_pid='+str(os.getpid()) in labels

def test_remote_daemon_recovery_is_rejected_before_any_docker_command(monkeypatch):
    from agentguard.execution import recovery
    monkeypatch.setenv('DOCKER_HOST','tcp://example.invalid:2376')
    monkeypatch.delenv('DOCKER_CONTEXT',raising=False)
    def forbidden(*args,**kwargs):raise AssertionError('Unexpected Docker invocation')
    monkeypatch.setattr(recovery,'command',forbidden)
    with pytest.raises(ValueError,match='local'):recovery.reap_container('a'*64,'b'*32)

def test_unknown_windows_owner_is_treated_as_alive_for_reaping(monkeypatch):
    from agentguard.execution import recovery,owner_watch
    class Unknown:
        handle=None;error=5
        def __init__(self,pid):pass
        def alive(self):return False
        def close(self):pass
    monkeypatch.setattr(owner_watch,'OwnerWatch',Unknown);monkeypatch.setattr(recovery,'WINDOWS',True,raising=False)
    assert recovery.process_alive(99999999) is True

def test_effective_context_overrides_local_host_before_cleanup(monkeypatch):
    from agentguard.execution import recovery
    monkeypatch.setenv('DOCKER_CONTEXT','remote-context');monkeypatch.setenv('DOCKER_HOST','unix:///var/run/docker.sock')
    def context_only(args,deadline):
        if args[1]=='context':return '"tcp://example.invalid:2376"'
        raise AssertionError('Remote container must not be inspected')
    monkeypatch.setattr(recovery,'command',context_only)
    with pytest.raises(ValueError,match='local'):recovery.reap_container('a'*64,'b'*32)
