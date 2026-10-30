import pytest

from agentguard.execution.container_lifecycle import (
    ContainerLifecycle,
    ContainerState,
)


def test_valid_container_lifecycle():
    lifecycle = ContainerLifecycle(name="test-container")

    assert lifecycle.state == ContainerState.CREATING

    lifecycle.transition(ContainerState.CREATED)
    assert lifecycle.state == ContainerState.CREATED

    lifecycle.transition(ContainerState.STARTING)
    assert lifecycle.state == ContainerState.STARTING

    lifecycle.transition(ContainerState.EXECUTED)
    assert lifecycle.state == ContainerState.EXECUTED

    lifecycle.transition(ContainerState.CLEANING_UP)
    assert lifecycle.state == ContainerState.CLEANING_UP

    lifecycle.transition(ContainerState.REMOVED)
    assert lifecycle.state == ContainerState.REMOVED


def test_cleanup_can_start_after_creation_failure():
    lifecycle = ContainerLifecycle(name="test-container")

    lifecycle.transition(ContainerState.CLEANING_UP)
    lifecycle.transition(ContainerState.REMOVED)

    assert lifecycle.state == ContainerState.REMOVED


def test_cleanup_can_be_unverified():
    lifecycle = ContainerLifecycle(name="test-container")

    lifecycle.transition(ContainerState.CREATED)
    lifecycle.transition(ContainerState.CLEANING_UP)
    lifecycle.transition(
        ContainerState.CLEANUP_UNVERIFIED
    )

    assert (
        lifecycle.state
        == ContainerState.CLEANUP_UNVERIFIED
    )


def test_invalid_transition_is_rejected():
    lifecycle = ContainerLifecycle(name="test-container")

    with pytest.raises(
        ValueError,
        match="Invalid container transition",
    ):
        lifecycle.transition(ContainerState.EXECUTED)


def test_removed_state_is_terminal():
    lifecycle = ContainerLifecycle(name="test-container")

    lifecycle.transition(ContainerState.CLEANING_UP)
    lifecycle.transition(ContainerState.REMOVED)

    with pytest.raises(ValueError):
        lifecycle.transition(ContainerState.CREATED)


def test_cleanup_unverified_state_is_terminal():
    lifecycle = ContainerLifecycle(name="test-container")

    lifecycle.transition(ContainerState.CLEANING_UP)
    lifecycle.transition(
        ContainerState.CLEANUP_UNVERIFIED
    )

    with pytest.raises(ValueError):
        lifecycle.transition(ContainerState.CREATED)