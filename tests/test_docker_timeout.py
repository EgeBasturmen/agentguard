from unittest.mock import patch

import pytest

from agentguard.execution.docker_runtime import (
    ContainerRuntimeError,
    DockerRuntime,
)
from agentguard.security.policy import SecurityPolicy


@pytest.mark.docker
def test_timeout_removes_container():
    runtime = DockerRuntime()

    original_command = runtime._docker_create_command
    created_names = []

    def slow_command(container_name, policy):
        created_names.append(container_name)
        command = original_command(container_name, policy)

        # Container oluşturulurken normal worker yerine
        # 3 saniye bekleyen bir Python süreci tanımlıyoruz.
        command[-1:] = [
            "python:3.11-slim",
            "python",
            "-c",
            "import time; time.sleep(3)",
        ]

        return command

    with patch.object(
        runtime,
        "_docker_create_command",
        side_effect=slow_command,
    ):
        with pytest.raises(
            ContainerRuntimeError,
            match="timed out",
        ):
            runtime.run_request(
                request={
                    "protocol_version": 1,
                    "scenario_name": "timeout-test",
                },
                policy=SecurityPolicy(
                    execution_mode="isolated",
                    timeout_seconds=1,
                ),
            )

    assert created_names
    # A timeout exception alone does not prove that execution stopped.
    assert runtime._inspect_container_state(created_names[0]) is None
