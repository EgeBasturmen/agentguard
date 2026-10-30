import pytest

from agentguard.sandbox.commerce import CommerceSandbox
from agentguard.sandbox.crm import CRMSandbox
from agentguard.sandbox.registry import get_sandbox_factory


def test_commerce_sandbox():
    assert get_sandbox_factory("commerce") is CommerceSandbox


def test_crm_sandbox():
    assert get_sandbox_factory("crm") is CRMSandbox


def test_unknown_sandbox():
    with pytest.raises(ValueError, match="Unknown sandbox"):
        get_sandbox_factory("unknown")