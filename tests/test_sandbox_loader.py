import pytest

from agentguard.adapters.sandbox import (
    SandboxLoadError,
    load_python_sandbox,
)
from agentguard.sandbox.commerce import CommerceSandbox
from agentguard.sandbox.files import FileSandbox


def test_load_commerce_sandbox():
    sandbox_class = load_python_sandbox(
        "agentguard.sandbox.commerce:CommerceSandbox"
    )

    assert sandbox_class is CommerceSandbox


def test_load_file_sandbox():
    sandbox_class = load_python_sandbox(
        "agentguard.sandbox.files:FileSandbox"
    )

    assert sandbox_class is FileSandbox


def test_unknown_sandbox_module():
    with pytest.raises(SandboxLoadError):
        load_python_sandbox(
            "unknown_sandbox_module:UnknownSandbox"
        )


def test_unknown_sandbox_class():
    with pytest.raises(SandboxLoadError):
        load_python_sandbox(
            "agentguard.sandbox.files:UnknownSandbox"
        )


def test_invalid_sandbox_reference():
    with pytest.raises(SandboxLoadError):
        load_python_sandbox("FileSandbox")