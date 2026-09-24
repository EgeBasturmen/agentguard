import subprocess
from unittest.mock import patch

import pytest

from agentguard.execution.docker_runtime import (
    ContainerRuntimeError,
    DockerRuntime,
)
from agentguard.security.policy import SecurityPolicy


def test_timeout_removes_container():
    runtime = DockerRuntime()

    original_command = runtime._docker_command

    def slow_command(container_name, policy):
        command = original_command(container_name, policy)

        # Testte worker yerine üç saniye bekleyen Python çalışır.
        command[-1:] = [
            "python:3.11-slim",
            "python",
            "-c",
            "import time; time.sleep(3)",
        ]

        return command

    with patch.object(
        runtime,
        "_docker_command",
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
                    timeout_seconds=1,
                ),
            )