import pytest

from agentguard.adapters.python import (
    AgentLoadError,
    load_python_agent,
)


def test_load_good_agent():

    agent = load_python_agent(
        "examples.refund_agent:GoodRefundAgent"
    )

    assert agent.__class__.__name__ == (
        "GoodRefundAgent"
    )

    assert callable(agent.run)


def test_unknown_module_fails():

    with pytest.raises(AgentLoadError):

        load_python_agent(
            "does_not_exist.agent:SomeAgent"
        )


def test_unknown_class_fails():

    with pytest.raises(AgentLoadError):

        load_python_agent(
            "examples.refund_agent:DoesNotExist"
        )


def test_invalid_reference_fails():

    with pytest.raises(AgentLoadError):

        load_python_agent(
            "examples.refund_agent"
        )