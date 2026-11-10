import pytest
from pydantic import ValidationError

from agentguard.execution.docker_runtime import (
    ContainerRuntimeError,
    DockerRuntime,
)
from agentguard.execution.protocol import (
    WorkerRequest,
    WorkerResponse,
)
from agentguard.security.policy import SecurityPolicy


def test_valid_worker_request():
    request = WorkerRequest(
        scenario_name="refund-order",
    )

    assert request.protocol_version == 1


def test_request_rejects_extra_fields():
    with pytest.raises(ValidationError):
        WorkerRequest(
            scenario_name="refund-order",
            agent_code="print('hello')",
        )


def test_request_rejects_wrong_version():
    with pytest.raises(ValidationError):
        WorkerRequest(
            protocol_version=2,
            scenario_name="refund-order",
        )


def test_response_rejects_unknown_status():
    with pytest.raises(ValidationError):
        WorkerResponse(
            scenario_name="refund-order",
            status="passed",
            message="Unexpected status",
        )


def test_runtime_rejects_invalid_request_before_docker():
    runtime = DockerRuntime()

    with pytest.raises(
        ContainerRuntimeError,
        match="Invalid worker request",
    ):
        runtime.run_request(
            request={
                "protocol_version": 1,
                "scenario_name": "refund-order",
                "agent_code": "print('hello')",
            },
            policy=SecurityPolicy(),
        )


@pytest.mark.docker
def test_docker_worker_protocol_roundtrip():
    runtime = DockerRuntime()

    response = runtime.run_request(
        request={
            "protocol_version": 1,
            "scenario_name": "refund-order",
        },
        policy=SecurityPolicy(),
    )

    assert response["protocol_version"] == 1
    assert response["status"] == "ready"
    assert response["scenario_name"] == "refund-order"
