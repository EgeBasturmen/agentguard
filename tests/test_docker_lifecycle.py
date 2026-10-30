import threading
from unittest.mock import patch
import subprocess
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


def test_late_launch_finishes_before_container_cleanup():
    runtime = DockerRuntime()
    handle = LaunchHandle()

    launch_can_finish = threading.Event()
    cleanup_started = threading.Event()
    events = []

    def fake_launch(*, command, deadline):
        def finish_later():
            launch_can_finish.wait(timeout=2)
            events.append("launcher_finished")
            handle.finished.set()

        threading.Thread(
            target=finish_later,
            daemon=True,
        ).start()

        raise ProcessLaunchTimeout(handle)

    def fake_cleanup(container_name):
        events.append("container_cleanup")
        cleanup_started.set()

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
        launch_can_finish.set()

        with pytest.raises(
            ContainerRuntimeError,
            match="launch exceeded its deadline",
        ):
            runtime.run_request(
                request={
                    "protocol_version": 1,
                    "scenario_name": "lifecycle-test",
                },
                policy=SecurityPolicy(timeout_seconds=1),
            )

    assert cleanup_started.is_set()

    assert events == [
        "launcher_finished",
        "container_cleanup",
    ]


def test_inspect_container_state_returns_running():
    runtime = DockerRuntime()

    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="running\n",
        stderr="",
    )

    with patch(
        "agentguard.execution.docker_runtime.subprocess.run",
        return_value=completed,
    ):
        state = runtime._inspect_container_state(
            "a" * 64
        )

    assert state == "running"


def test_inspect_container_state_returns_none_when_missing():
    runtime = DockerRuntime()

    completed = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="Error: No such object: abc",
    )

    with patch(
        "agentguard.execution.docker_runtime.subprocess.run",
        return_value=completed,
    ):
        state = runtime._inspect_container_state(
            "a" * 64
        )

    assert state is None


def test_inspect_container_state_fails_closed():
    runtime = DockerRuntime()

    completed = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="",
        stderr="Docker daemon unavailable",
    )

    with patch(
        "agentguard.execution.docker_runtime.subprocess.run",
        return_value=completed,
    ):
        with pytest.raises(
            ContainerRuntimeError,
            match="state inspection failed",
        ):
            runtime._inspect_container_state(
                "a" * 64
            )


def test_start_timeout_inspects_running_container_before_cleanup():
    runtime = DockerRuntime()
    handle = LaunchHandle()

    events = []

    container_id = "a" * 64

    def fake_create_container(
        *,
        container_name,
        policy,
        deadline,
    ):
        return container_id

    def fake_launch(*, command, deadline):
        handle.finished.set()
        raise ProcessLaunchTimeout(handle)

    def fake_inspect(container_ref):
        assert container_ref == container_id
        events.append("inspect")
        return "running"

    def fake_cleanup(container_ref):
        assert container_ref == container_id
        events.append("cleanup")

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
            "_inspect_container_state",
            side_effect=fake_inspect,
        ),
        patch.object(
            runtime,
            "_cleanup_container",
            side_effect=fake_cleanup,
        ),
    ):
        with pytest.raises(
            ContainerRuntimeError,
            match="Observed container state before cleanup: running",
        ):
            runtime.run_request(
                request={
                    "protocol_version": 1,
                    "scenario_name": "late-start-running",
                },
                policy=SecurityPolicy(
                    timeout_seconds=10,
                ),
            )

    assert events == [
        "inspect",
        "cleanup",
    ]