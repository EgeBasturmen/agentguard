import threading
import time
from unittest.mock import patch

import pytest

from agentguard.execution.docker_runtime import (
    ContainerRuntimeError,
    DockerRuntime,
)
from agentguard.execution.process_launcher import (
    LaunchHandle,
    ProcessLaunchTimeout,
)
from agentguard.security.policy import SecurityPolicy
from agentguard.execution.process_launcher import (
    ProcessLaunchError,
    ProcessLaunchTimeout,
    launch_with_deadline,
)

def test_docker_worker_receives_request():
    runtime = DockerRuntime()

    response = runtime.run_request(
        request={
            "protocol_version": 1,
            "scenario_name": "docker-smoke-test",
        },
        policy=SecurityPolicy(
            timeout_seconds=30,
        ),
    )

    assert response["protocol_version"] == 1
    assert response["status"] == "ready"
    assert response["scenario_name"] == "docker-smoke-test"


def test_docker_runtime_rejects_local_policy():
    runtime = DockerRuntime()

    with pytest.raises(
        ContainerRuntimeError,
        match="requires isolated",
    ):
        runtime.run_request(
            request={
                "protocol_version": 1,
                "scenario_name": "test",
            },
            policy=SecurityPolicy(
                execution_mode="local",
            ),
        )


def test_docker_runtime_rejects_network_access():
    runtime = DockerRuntime()

    with pytest.raises(
        ContainerRuntimeError,
        match="does not support",
    ):
        runtime.run_request(
            request={
                "protocol_version": 1,
                "scenario_name": "test",
            },
            policy=SecurityPolicy(
                allow_network=True,
            ),
        )

import subprocess
from unittest.mock import patch

from agentguard.execution.docker_runtime import DockerRuntime


def test_cleanup_removes_container():
    runtime = DockerRuntime()

    removal_result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="agentguard-test-container",
        stderr="",
    )

    inspection_result = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr=(
            "error: no such object: "
            "agentguard-test-container"
        ),
    )

    with patch(
        "agentguard.execution.docker_runtime.subprocess.run",
        side_effect=[
            removal_result,
            inspection_result,
        ],
    ) as mocked_run:

        runtime._cleanup_container(
            "agentguard-test-container"
        )

    assert mocked_run.call_count == 2

    assert mocked_run.call_args_list[0].args[0] == [
        "docker",
        "rm",
        "--force",
        "agentguard-test-container",
    ]

    assert mocked_run.call_args_list[1].args[0] == [
        "docker",
        "inspect",
        "agentguard-test-container",
    ]


def test_cleanup_accepts_already_removed_container():
    runtime = DockerRuntime()

    removal_result = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr=(
            "Error response from daemon: "
            "No such container: agentguard-test"
        ),
    )

    inspection_result = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr=(
            "error: no such object: agentguard-test"
        ),
    )

    with patch(
        "agentguard.execution.docker_runtime.subprocess.run",
        side_effect=[
            removal_result,
            inspection_result,
        ],
    ) as mocked_run:

        runtime._cleanup_container(
            "agentguard-test"
        )

    assert mocked_run.call_count == 2


def test_cleanup_reports_failure():
    runtime = DockerRuntime()

    removal_result = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="Docker daemon unavailable",
    )

    with patch(
        "agentguard.execution.docker_runtime.subprocess.run",
        return_value=removal_result,
    ) as mocked_run:

        with pytest.raises(
            ContainerRuntimeError,
            match="Container removal failed",
        ):
            runtime._cleanup_container(
                "agentguard-test"
            )

    assert mocked_run.call_count == 1


def test_cleanup_rejects_container_still_existing():
    runtime = DockerRuntime()

    removal_result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="agentguard-test",
        stderr="",
    )

    inspection_result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='[{"Id": "container-id"}]',
        stderr="",
    )

    with patch(
        "agentguard.execution.docker_runtime.subprocess.run",
        side_effect=[
            removal_result,
            inspection_result,
        ],
    ):
        with pytest.raises(
            ContainerRuntimeError,
            match="Container still exists",
        ):
            runtime._cleanup_container(
                "agentguard-test"
            )




def test_launch_timeout_waits_for_launcher_before_cleanup():
    runtime = DockerRuntime()
    handle = LaunchHandle()
    launcher_finished = threading.Event()
    cleanup_called = threading.Event()

    def fake_launch(*, command, deadline):
        def finish_later():
            time.sleep(0.1)
            launcher_finished.set()
            handle.finished.set()

        threading.Thread(
            target=finish_later,
            daemon=True,
        ).start()

        raise ProcessLaunchTimeout(handle)

    def fake_cleanup(container_name):
        assert launcher_finished.is_set(), (
            "Cleanup started before launcher finished."
        )
        cleanup_called.set()

    with (
        patch(
            "agentguard.execution.docker_runtime.launch_with_deadline",
            side_effect=fake_launch,
        ),
        patch.object(
            runtime,
            "_cleanup_container",
            side_effect=fake_cleanup,
        ),
    ):
        with pytest.raises(
            ContainerRuntimeError,
            match="launch exceeded its deadline",
        ):
            runtime.run_request(
                request={
                    "protocol_version": 1,
                    "scenario_name": "late-launch-test",
                },
                policy=SecurityPolicy(timeout_seconds=1),
            )

    assert cleanup_called.is_set()


def test_launch_timeout_reports_unfinished_launcher():
    runtime = DockerRuntime()
    handle = LaunchHandle()
    cleanup_called = threading.Event()

    def fake_launch(*, command, deadline):
        raise ProcessLaunchTimeout(handle)

    def fake_cleanup(container_name):
        cleanup_called.set()

    with (
        patch(
            "agentguard.execution.docker_runtime.launch_with_deadline",
            side_effect=fake_launch,
        ),
        patch.object(
            runtime,
            "_cleanup_container",
            side_effect=fake_cleanup,
        ),
        patch.object(
            runtime,
            "CLEANUP_TIMEOUT_SECONDS",
            0.1,
        ),
    ):
        with pytest.raises(
            ContainerRuntimeError,
            match="launcher thread did not finish",
        ):
            runtime.run_request(
                request={
                    "protocol_version": 1,
                    "scenario_name": "unfinished-launch-test",
                },
                policy=SecurityPolicy(timeout_seconds=1),
            )

    assert cleanup_called.is_set()


def test_docker_create_output_overflow_triggers_cleanup():
    runtime = DockerRuntime()
    runtime.IMAGE = "python:3.11-slim"

    original_command = runtime._docker_create_command

    def noisy_create_command(container_name, policy):
        command = original_command(container_name, policy)

        # Docker create'in kendi çıktısını büyütmek için
        # geçersiz bir image adı kullanmak yerine,
        # komutu kontrollü bir Python süreciyle değiştiriyoruz.
        return [
            "python",
            "-c",
            (
                "import sys; "
                "sys.stdout.write('X' * 100000); "
                "sys.stdout.flush()"
            ),
        ]

    cleanup_called = False

    def fake_cleanup(container_name):
        nonlocal cleanup_called
        cleanup_called = True

    with (
        patch.object(
            runtime,
            "_docker_create_command",
            side_effect=noisy_create_command,
        ),
        patch.object(
            runtime,
            "_cleanup_container",
            side_effect=fake_cleanup,
        ),
    ):
        with pytest.raises(
            ContainerRuntimeError,
            match="output exceeds size limit",
        ):
            runtime.run_request(
                request={
                    "protocol_version": 1,
                    "scenario_name": "create-output-overflow",
                },
                policy=SecurityPolicy(
                    execution_mode="isolated",
                    timeout_seconds=10,
                ),
            )

    assert cleanup_called

def test_start_and_cleanup_use_created_container_id():
    runtime = DockerRuntime()

    container_id = "a" * 64
    started_commands = []
    cleanup_targets = []

    class FakeProcess:
        def __init__(self):
            self.stdout = None
            self.stderr = None
            self.stdin = None
            self.returncode = 0

        def poll(self):
            return 0

    def fake_create_container(
        *,
        container_name,
        policy,
        deadline,
    ):
        return container_id

    def fake_launch(*, command, deadline):
        started_commands.append(command)

        # Burada gerçek process akışını taklit etmek
        # istemiyoruz. launch sonrasında kontrollü hata
        # vererek finally/cleanup yoluna geçiyoruz.
        raise ProcessLaunchError("test stop")

    def fake_cleanup(target):
        cleanup_targets.append(target)

    with (
        patch.object(
            runtime,
            "_create_container",
            side_effect=fake_create_container,
        ),
        patch(
            "agentguard.execution.docker_runtime.launch_with_deadline",
            side_effect=fake_launch,
        ),
        patch.object(
            runtime,
            "_cleanup_container",
            side_effect=fake_cleanup,
        ),
    ):
        with pytest.raises(
            ContainerRuntimeError,
            match="Docker CLI launch failed",
        ):
            runtime.run_request(
                request={
                    "protocol_version": 1,
                    "scenario_name": "container-id-test",
                },
                policy=SecurityPolicy(
                    timeout_seconds=10,
                ),
            )

    assert len(started_commands) == 1

    assert started_commands[0] == [
        "docker",
        "start",
        "-a",
        "-i",
        container_id,
    ]

    assert cleanup_targets == [
        container_id,
    ]