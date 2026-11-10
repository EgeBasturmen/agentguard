from copy import deepcopy

import pytest
from pydantic import ValidationError

from agentguard.core.scenario import Scenario
from agentguard.spec.loader import ScenarioLoadError, load_scenario


def valid_spec():
    return dict(name="generic", task="Read a value", initial_state={"value": 1},
                expected_state=[{"path": "value", "value": 1}])


@pytest.mark.parametrize("changes", [
    {"unknown": True}, {"name": " "}, {"task": ""},
    {"expected_state": []},
    {"expected_state": [{"path": "", "value": 1}]},
    {"expected_state": [{"path": "a..b", "value": 1}]},
    {"expected_state": [{"path": "a.", "value": 1}]},
    {"expected_state": [{"path": "a\\q", "value": 1}]},
    {"expected_state": [{"path": "value", "value": 1, "typo": True}]},
    {"tool_expectations": [{"tool_name": "read"}]},
    {"tool_expectations": [{"tool_name": "read", "times": -1}]},
    {"tool_expectations": [{"tool_name": "read", "times": True}]},
    {"tool_expectations": [{"tool_name": "read", "times": "1"}]},
    {"tool_expectations": [{"tool_name": "read", "times": 2, "max_times": 1}]},
    {"tool_expectations": [{"tool_name": " ", "times": 0}]},
    {"invariants": [{"name": "check", "kind": "unknown"}]},
    {"invariants": [{"name": "check", "kind": "refund_total_lte_order_total", "params": {}}]},
    {"invariants": [{"name": "check", "kind": "refund_total_lte_order_total",
                     "params": {"order_id": " ", "typo": True}}]},
])
def test_invalid_specs_are_rejected(changes):
    data = valid_spec()
    data.update(changes)
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


@pytest.mark.parametrize("field,checks", [
    ("expected_state", [{"path": "value", "value": 1}] * 2),
    ("forbidden_changes", [{"path": "value"}] * 2),
    ("tool_expectations", [{"tool_name": "read", "times": 1}] * 2),
    ("invariants", [{"name": "check", "kind": "refund_total_lte_order_total",
                     "params": {"order_id": "one"}}] * 2),
])
def test_duplicate_checks_are_rejected(field, checks):
    data = valid_spec()
    data[field] = deepcopy(checks)
    with pytest.raises(ValidationError, match="Duplicate"):
        Scenario.model_validate(data)


@pytest.mark.parametrize("expected,forbidden", [
    ({"path": "a.b", "value": 2}, "a"),
    ({"path": "a", "value": {"b": 2}}, "a.b"),
    ({"path": "a", "value": {}}, "a.b"),
    ({"path": "new", "value": None}, "new"),
])
def test_expected_and_forbidden_contradictions(expected, forbidden):
    with pytest.raises(ValidationError, match="forbidden"):
        Scenario(name="contradiction", task="x", initial_state={"a": {"b": 1}},
                 expected_state=[expected], forbidden_changes=[{"path": forbidden}])


def test_blank_expected_values_and_unchanged_protected_values_are_valid():
    scenario = Scenario(name="read", task="x", initial_state={"a": {"b": ""}},
                        expected_state=[{"path": "a", "value": {"b": "", "c": 2}}],
                        forbidden_changes=[{"path": "a.b"}])
    assert scenario.expected_state[0].value["b"] == ""


@pytest.mark.parametrize("parent,child", [({"b": 1}, 2), ({}, 1)])
@pytest.mark.parametrize("reverse", [False, True])
def test_overlapping_expected_state_contradictions(parent, child, reverse):
    checks = [{"path": "a", "value": parent}, {"path": "a.b", "value": child}]
    if reverse:
        checks.reverse()
    with pytest.raises(ValidationError, match="contradict"):
        Scenario(name="overlap", task="x", initial_state={}, expected_state=checks)


def test_consistent_overlapping_expectations_are_valid():
    assert Scenario(name="overlap", task="x", initial_state={}, expected_state=[
        {"path": "a", "value": {"b": 1}}, {"path": "a.b", "value": 1},
    ])


def test_loader_wraps_semantic_validation(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("name: empty\ntask: x\ninitial_state: {}\n", encoding="utf-8")
    with pytest.raises(ScenarioLoadError, match="at least one"):
        load_scenario(path)


@pytest.mark.parametrize("yaml_text", [
    "name: x\nname: y\n",
    "name: x\ntask: x\ninitial_state: {a: 1, a: 2}\n",
    "? [a, b]\n: value\n",
])
def test_loader_rejects_ambiguous_yaml_mappings(tmp_path, yaml_text):
    path = tmp_path / "ambiguous.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ScenarioLoadError, match="Invalid YAML"):
        load_scenario(path)


def test_loader_wraps_encoding_errors(tmp_path):
    path = tmp_path / "invalid.yaml"
    path.write_bytes(b"\xff")
    with pytest.raises(ScenarioLoadError, match="Could not read"):
        load_scenario(path)


def test_loader_wraps_os_errors(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path / "valid.yaml"
    path.write_text("name: x", encoding="utf-8")
    def denied(*args, **kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(Path, "open", denied)
    with pytest.raises(ScenarioLoadError, match="Could not read"):
        load_scenario(path)
