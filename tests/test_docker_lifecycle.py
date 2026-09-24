import threading
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