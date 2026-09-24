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