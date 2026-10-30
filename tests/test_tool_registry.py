
import pytest

from pydantic import BaseModel, ConfigDict, Field

from agentguard.execution.tool_broker import (
    ToolBroker,
    ToolNotAllowedError,
    ToolArgumentsError,
    ToolCallLimitExceeded,
)
from agentguard.execution.tool_registry import ToolRegistry


class CreateLeadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str = Field(min_length=1)


def test_register_and_execute_crm_tool():
    leads = []

    def create_lead(company: str):
        lead = {
            "id": f"LEAD-{len(leads) + 1}",
            "company": company,
        }
        leads.append(lead)
        return lead

    registry = ToolRegistry()

    registry.register(
        "crm.create_lead",
        CreateLeadArgs,
        create_lead,
    )

    broker = ToolBroker(registry=registry)

    result = broker.execute(
        "crm.create_lead",
        {"company": "Belzeka"},
    )

    assert result["id"] == "LEAD-1"
    assert leads[0]["company"] == "Belzeka"
    assert broker.call_count == 1


def test_unknown_tool_is_rejected():
    registry = ToolRegistry()
    broker = ToolBroker(registry=registry)

    with pytest.raises(ToolNotAllowedError):
        broker.execute(
            "system.execute",
            {"command": "whoami"},
        )


def test_invalid_arguments_are_rejected():
    registry = ToolRegistry()

    registry.register(
        "crm.create_lead",
        CreateLeadArgs,
        lambda company: {"company": company},
    )

    broker = ToolBroker(registry=registry)

    with pytest.raises(ToolArgumentsError):
        broker.execute(
            "crm.create_lead",
            {"wrong_field": "ABC"},
        )


def test_duplicate_tool_registration_rejected():
    registry = ToolRegistry()

    registry.register(
        "crm.create_lead",
        CreateLeadArgs,
        lambda company: None,
    )

    with pytest.raises(ValueError):
        registry.register(
            "crm.create_lead",
            CreateLeadArgs,
            lambda company: None,
        )


def test_tool_call_limit():
    registry = ToolRegistry()

    registry.register(
        "crm.create_lead",
        CreateLeadArgs,
        lambda company: {"company": company},
    )

    broker = ToolBroker(
        registry=registry,
        max_tool_calls=1,
    )

    broker.execute(
        "crm.create_lead",
        {"company": "ABC"},
    )

    with pytest.raises(ToolCallLimitExceeded):
        broker.execute(
            "crm.create_lead",
            {"company": "XYZ"},
        )


def test_list_registered_tools():
    registry = ToolRegistry()

    registry.register(
        "crm.create_lead",
        CreateLeadArgs,
        lambda company: None,
    )

    assert registry.list_tools() == [
        "crm.create_lead"
    ]
