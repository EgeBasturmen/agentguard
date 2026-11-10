import pytest

from agentguard.contracts.compatibility import assess_compatibility, supported_matrix
from agentguard.contracts.local import run_local_definition
from agentguard.contracts.models import ScenarioVersion
from examples.phase0 import sample_definitions, sandbox_factory


@pytest.mark.parametrize("change", ["adapter", "adapter_version", "protocol", "controlled", "transport", "mode", "environment_kind", "tool_adapter", "tool_mode", "evaluator", "budget", "messages", "credentials", "rules"])
def test_unsupported_combinations_are_explicitly_rejected(change):
    definition = sample_definitions()["file-edit"]
    data = definition.model_dump()
    if change == "adapter":
        data["agent"]["adapter"]["id"] = "unknown.adapter"
    elif change == "adapter_version":
        data["agent"]["adapter"]["version"] = "9.0.0"
    elif change == "protocol":
        data["agent"]["adapter"]["protocol_version"] = 2
    elif change == "controlled":
        data["agent"]["execution_mode"] = "controlled"
    elif change == "transport":
        data["agent"]["transport"] = "http"
    elif change == "mode":
        data["environment"]["test_mode"] = "staging"
    elif change == "environment_kind":
        data["environment"]["kind"] = "browser"
    elif change == "tool_adapter":
        data["tools"][0]["adapter"] = {"id": "mcp.tool", "version": "0.0.0", "protocol_version": 1}
    elif change == "tool_mode":
        data["tools"][0]["test_mode"] = "replay"
    elif change == "evaluator":
        data["evaluator"]["id"] = "judge.custom"
    elif change == "budget":
        data["limits"]["budget"] = 1.0
    elif change == "messages":
        data["messages"] = [{"role": "user", "content": "multi-turn"}]
    elif change == "credentials":
        data["agent"]["credential_refs"] = ["secret://project/key"]
    else:
        data["rules"] = [{"id": "rule", "version": "1.0.0"}]
    result = assess_compatibility(ScenarioVersion.model_validate(data))
    assert not result.supported
    assert result.issues


@pytest.mark.parametrize("capability", [
    "supports_isolation", "supports_tool_interception", "supports_authoritative_evidence",
    "supports_reset", "supports_streaming", "supports_cancellation", "supports_model_usage",
    "supports_output", "supports_sessions", "supports_memory_reset",
])
def test_missing_capability_cannot_be_silently_accepted(capability):
    definition = sample_definitions()["file-edit"]
    definition.required_capabilities.append(capability)
    result = assess_compatibility(definition)
    assert not result.supported
    assert result.missing_capabilities == [capability]


def test_controlled_request_never_falls_back_to_local_execution():
    definition = sample_definitions()["file-edit"]
    definition.agent.execution_mode = "controlled"
    class MustNotRun:
        def run(self, **kwargs):
            pytest.fail("Unsupported controlled execution fell back to local")
    def must_not_provision(**kwargs):
        pytest.fail("Unsupported definition provisioned a sandbox")
    result = run_local_definition(definition, MustNotRun(), must_not_provision)
    assert result.status == "error"
    assert result.error_type == "UnsupportedContractError"
    assert result.evaluation_status == "not_run"


def test_matrix_is_version_pinned_and_returns_independent_objects():
    matrix = supported_matrix()
    identities = [(row.component, row.adapter.id, row.adapter.version, row.adapter.protocol_version) for row in matrix]
    assert len(identities) == len(set(identities))
    assert all(row.adapter.protocol_version == (2 if row.adapter.id == "python.container" else 1) for row in matrix)
    assert all(row.execution_modes == (["controlled"] if row.adapter.id == "python.container" else ["observed"]) for row in matrix if row.implementation == "implemented")
    matrix[0].capabilities.supports_isolation = True
    assert not supported_matrix()[0].capabilities.supports_isolation
    assert all(not row.execution_modes for row in matrix if row.implementation == "contract_only")


def test_unknown_effect_is_a_visible_limitation():
    definition = sample_definitions()["file-edit"]
    definition.tools[0].effect_type = "unknown"
    result = assess_compatibility(definition)
    assert result.supported
    assert any("Unknown tool effects" in gap for gap in result.limitations)
