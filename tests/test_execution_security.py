import pytest

from agentguard.execution.isolated import (
    IsolatedExecutionBackend,
    IsolationNotImplementedError,
)
from agentguard.execution.local import (
    LocalExecutionBackend,
    UnsafeExecutionModeError,
)
from agentguard.security.policy import SecurityPolicy
from agentguard.spec.loader import load_scenario

from examples.refund_agent import GoodRefundAgent


def create_scenario():
    return load_scenario(
        "scenarios/refund_order.yaml"
    )


def test_security_policy_defaults_to_isolated():
    policy = SecurityPolicy()

    assert policy.execution_mode == "isolated"
    assert policy.allow_network is False
    assert policy.allow_host_filesystem is False
    assert policy.allow_host_environment is False
    assert policy.allowed_tools == frozenset()


def test_local_backend_rejects_isolated_policy():
    backend = LocalExecutionBackend()

    with pytest.raises(
        UnsafeExecutionModeError,
        match="cannot satisfy",
    ):
        backend.run(
            scenario=create_scenario(),
            agent=GoodRefundAgent(),
            policy=SecurityPolicy(),
        )


def test_local_backend_runs_with_explicit_local_mode():
    backend = LocalExecutionBackend()

    result = backend.run(
        scenario=create_scenario(),
        agent=GoodRefundAgent(),
        policy=SecurityPolicy(
            execution_mode="local",
        ),
    )

    assert result.passed is True
    assert result.status == "passed"


def test_isolated_backend_refuses_execution():
    backend = IsolatedExecutionBackend()

    with pytest.raises(
        IsolationNotImplementedError,
        match="Live host agent objects are unsupported",
    ):
        backend.run(
            scenario=create_scenario(),
            agent=GoodRefundAgent(),
            policy=SecurityPolicy(),
        )