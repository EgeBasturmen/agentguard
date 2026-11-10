import pytest

from agentguard.core.paths import parse_path
from agentguard.core.scenario import ExpectedState, ForbiddenChange
from agentguard.evaluation.assertions import evaluate_expected_state, evaluate_forbidden_change
from agentguard.evaluation.diff import diff_states


@pytest.mark.parametrize("state,path,value", [
    ({"a": [{"b": 1}]}, "a.0.b", 1),
    ({"a.b": {"c\\d": 2}}, r"a\.b.c\\d", 2),
    ({"$": 3}, r"\$", 3),
    ({"a": 1}, "$", {"a": 1}),
    ({"a": None}, "a", None),
])
def test_expected_paths(state, path, value):
    assert evaluate_expected_state(state, ExpectedState(path=path, value=value)).passed


@pytest.mark.parametrize("state,path", [
    ({"a": []}, "a.0"), ({"a": [1]}, "a.-1"),
    ({"a": [1]}, "a.00"), ({"a": [1]}, "a.x"),
    ({"a": 1}, "a.b"), ({"a": None}, "a.b"), ({}, "a"),
])
def test_unresolvable_paths_fail_without_escaping(state, path):
    result = evaluate_expected_state(state, ExpectedState(path=path, value=None))
    assert not result.passed
    assert result.message


@pytest.mark.parametrize("before,after,path", [
    ({"a": {"b": 1}}, {}, "a.b"),
    ({"a": {"b": 1}}, {"a": None}, "a.b"),
    ({"a": [1]}, {"a": [2]}, "a.0"),
    ({"a": [1]}, {"a": []}, "a.0"),
    ({"a": []}, {"a": [None]}, "a.0"),
    ({"a": 1}, None, "a"),
    ({"a": 1}, {"a": 2}, "$"),
    ({"a.b": 1}, {"a.b": 2}, r"a\.b"),
    ({"a": True}, {"a": 1}, "a"),
])
def test_parent_root_list_and_type_changes_block(before, after, path):
    assert not evaluate_forbidden_change(
        diff_states(before, after), ForbiddenChange(path=path),
    ).passed


@pytest.mark.parametrize("before,after,path", [
    ({"a": [1, 2]}, {"a": [1, 3]}, "a.0"),
    ({"a": {"b": 1}}, {"a": {"b": 1, "c": 2}}, "a.b"),
    ({"a.b": 1, "a": {"b": 1}}, {"a.b": 2, "a": {"b": 1}}, "a.b"),
    ({"a": []}, {"a": [2]}, "a.9"),
])
def test_unrelated_changes_do_not_false_block(before, after, path):
    assert evaluate_forbidden_change(
        diff_states(before, after), ForbiddenChange(path=path),
    ).passed


def test_diff_order_is_deterministic_and_paths_roundtrip():
    before = {"z": 0, "a.b": 1, "a": {"c": 1, "b": 0}}
    after = {"z": 1, "a.b": 2, "a": {"c": 2, "b": 1}}
    changes = diff_states(before, after)
    assert [m.path for m in changes] == ["a.b", "a.c", r"a\.b", "z"]
    assert parse_path(changes[2].path) == ("a.b",)


@pytest.mark.parametrize("before,after", [(True, 1), (1, 1.0), ([True], [1])])
def test_type_changes_are_not_equal(before, after):
    assert diff_states(before, after)
    assert not evaluate_expected_state({"a": after}, ExpectedState(path="a", value=before)).passed
