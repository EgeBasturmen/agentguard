from copy import deepcopy
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from agentguard.contracts.models import (
    AgentSpec, Capabilities, EnvironmentSpec, ExecutionContext, ScenarioVersion, ToolDefinition,
)
from examples.phase0 import sample_definitions


@pytest.fixture
def definition():
    return sample_definitions()["file-edit"]


@pytest.mark.parametrize("selector", ["agent", "environment", "tool", "scenario"])
def test_versioned_contracts_roundtrip_and_publish_schema(definition, selector):
    model = {"agent": definition.agent, "environment": definition.environment,
             "tool": definition.tools[0], "scenario": definition}[selector]
    assert type(model).model_validate_json(model.model_dump_json()) == model
    assert "contract_version" in type(model).model_json_schema()["properties"]
    data = model.model_dump()
    data["unknown_field"] = True
    with pytest.raises(ValidationError):
        type(model).model_validate(data)


@pytest.mark.parametrize("version", [0, 2, True, 1.0, "1"])
def test_unsupported_contract_versions_rejected(definition, version):
    data = definition.model_dump()
    data["contract_version"] = version
    with pytest.raises(ValidationError, match="contract_version"):
        ScenarioVersion.model_validate(data)


@pytest.mark.parametrize("changes", [
    {"id": " "}, {"version": "latest"}, {"transport": "ssh"},
    {"entrypoint": ""}, {"execution_mode": "local"},
    {"credential_refs": ["raw-secret-value"]},
])
def test_agent_spec_validation(definition, changes):
    data = definition.agent.model_dump()
    data.update(changes)
    with pytest.raises(ValidationError):
        AgentSpec.model_validate(data)


def test_entrypoint_validation_never_imports_or_connects(definition):
    data = definition.agent.model_dump()
    data["entrypoint"] = "untrusted_payload:Agent"
    with patch("importlib.import_module", side_effect=AssertionError("must not import")):
        spec = AgentSpec.model_validate(data)
    assert spec.entrypoint == "untrusted_payload:Agent"


@pytest.mark.parametrize("changes", [
    {"input_schema": {}}, {"input_schema": {"bad": float("inf")}},
    {"timeout_seconds": 0}, {"timeout_seconds": float("inf")},
    {"effect_type": "safe"}, {"test_mode": "live"}, {"credential_ref": "password"},
])
def test_tool_definition_rejects_ambiguous_values(definition, changes):
    data = definition.tools[0].model_dump()
    data.update(changes)
    with pytest.raises(ValidationError):
        ToolDefinition.model_validate(data)


def test_unknown_tool_effect_is_preserved_not_declared_safe(definition):
    data = definition.tools[0].model_dump()
    data.pop("effect_type")
    data.pop("idempotency")
    tool = ToolDefinition.model_validate(data)
    assert tool.effect_type == tool.idempotency == "unknown"


@pytest.mark.parametrize("fixture", [{"x": float("nan")}, {"x": object()}, {"x": {1: "value"}}])
def test_environment_requires_json_fixture(definition, fixture):
    data = definition.environment.model_dump()
    data["fixture"] = fixture
    with pytest.raises(ValidationError):
        EnvironmentSpec.model_validate(data)


@pytest.mark.parametrize("change", ["fixture", "duplicate_tool", "duplicate_name", "undeclared_check", "permission", "capability", "provenance"])
def test_scenario_version_validates_references(definition, change):
    data = definition.model_dump()
    if change == "fixture":
        data["environment"]["fixture"] = {}
    elif change in ("duplicate_tool", "duplicate_name"):
        duplicate = deepcopy(data["tools"][0])
        if change == "duplicate_name":
            duplicate["id"] = "another-id"
        data["tools"].append(duplicate)
    elif change == "undeclared_check":
        data["scenario"]["tool_expectations"][0]["tool_name"] = "not.declared"
    elif change == "permission":
        data["permissions"].append("not.declared")
    elif change == "capability":
        data["required_capabilities"].append("not_a_capability")
    else:
        data["provenance"] = []
    with pytest.raises(ValidationError):
        ScenarioVersion.model_validate(data)


def test_capabilities_are_explicit_and_fail_closed():
    assert Capabilities().supported() == []
    with pytest.raises(ValidationError):
        Capabilities(supports_cancellation="yes")
    with pytest.raises(ValidationError):
        Capabilities(unknown=True)


def test_controlled_context_requires_three_separate_guarantees():
    from agentguard.contracts.compatibility import local_context
    data = local_context().model_dump()
    data["execution_mode"] = "controlled"
    with pytest.raises(ValidationError, match="Controlled"):
        ExecutionContext.model_validate(data)
    for name in ("supports_isolation", "supports_tool_interception", "supports_authoritative_evidence"):
        data["capabilities"][name] = True
    assert ExecutionContext.model_validate(data).execution_mode == "controlled"
