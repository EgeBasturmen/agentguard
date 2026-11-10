import json

import pytest

from agentguard.contracts.compatibility import assess_compatibility
from agentguard.contracts.local import run_local_definition
from agentguard.contracts.models import ScenarioVersion
from agentguard.contracts.results import summarize_result
from agentguard.core.outcome import effective_pass
from agentguard.regression.comparator import RegressionComparator
from agentguard.reporting.export import export_html, export_json
from agentguard.reporting.models import load_report
from examples.phase0 import (
    APIAgent, BadAPIAgent, BadFileAgent, BadResearchAgent, FileAgent, ResearchAgent,
    sample_definitions, sandbox_factory,
)


CASES = [
    ("file-edit", FileAgent, BadFileAgent),
    ("research-retrieval", ResearchAgent, BadResearchAgent),
    ("api-update", APIAgent, BadAPIAgent),
]


@pytest.mark.parametrize("name,good,bad", CASES)
def test_three_workloads_use_the_same_contract_and_existing_core(name, good, bad):
    definition = ScenarioVersion.model_validate_json(sample_definitions()[name].model_dump_json())
    assert assess_compatibility(definition).supported
    factory = sandbox_factory(definition)
    before = definition.model_dump()
    good_result = run_local_definition(definition, good(), factory)
    bad_result = run_local_definition(definition, bad(), factory)
    assert definition.model_dump() == before
    assert good_result.evaluation_status == bad_result.evaluation_status == "complete"
    assert effective_pass(good_result)
    assert not effective_pass(bad_result)
    assert good_result.execution_context.execution_mode == "observed"
    assert not good_result.execution_context.capabilities.supports_authoritative_evidence
    assert all(check.assertion_id for check in good_result.assertions)
    assert not RegressionComparator().compare("good", good_result, "bad", bad_result).passed
    assert summarize_result(good_result).policy_status == "unknown"
    assert summarize_result(bad_result).behavior_status == "failed"


@pytest.mark.parametrize("name,good,bad", CASES)
def test_every_attempt_gets_fresh_simulated_state(name, good, bad):
    definition = sample_definitions()[name]
    factory = sandbox_factory(definition)
    run_local_definition(definition, bad(), factory)
    first = run_local_definition(definition, good(), factory)
    second = run_local_definition(definition, good(), factory)
    assert effective_pass(first) and effective_pass(second)
    assert first.mutations == second.mutations
    assert first.comparison_manifest.fingerprint == second.comparison_manifest.fingerprint


def test_envelope_semantics_are_part_of_comparison_identity():
    definition = sample_definitions()["file-edit"]
    before = run_local_definition(definition, FileAgent(), sandbox_factory(definition))
    definition.tools[0].version = "1.1.0"
    after = run_local_definition(definition, FileAgent(), sandbox_factory(definition))
    assert effective_pass(before) and effective_pass(after)
    assert RegressionComparator().compare("before", before, "after", after).comparison_status == "incompatible"


def test_candidate_artifact_identity_is_not_mistaken_for_a_suite_change():
    definition = sample_definitions()["file-edit"]
    before = run_local_definition(definition, FileAgent(), sandbox_factory(definition))
    definition.agent.version = "2.0.0"
    after = run_local_definition(definition, FileAgent(), sandbox_factory(definition))
    assert RegressionComparator().compare("before", before, "after", after).passed


def test_phase0_result_metadata_survives_report_roundtrip(tmp_path):
    definition = sample_definitions()["research-retrieval"]
    result = run_local_definition(definition, ResearchAgent(), sandbox_factory(definition))
    path = export_json(result, tmp_path / "run.json", "ResearchAgent")
    report = load_report(path)
    assert report.result.execution_context == result.execution_context
    assert summarize_result(report.result).gate_decision == "ALLOW"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["result"]["execution_context"]["capabilities"]["supports_model_usage"] is False
    html = export_html(result, tmp_path / "run.html", "ResearchAgent").read_text(encoding="utf-8")
    assert "Execution assurance" in html and "observed" in html


def test_research_example_uses_retrieved_content_instead_of_fixed_answer():
    definition = sample_definitions()["research-retrieval"]
    definition.scenario.initial_state["documents"]["doc-1"] = "The retry limit is 5."
    definition.environment.fixture["documents"]["doc-1"] = "The retry limit is 5."
    definition.scenario.expected_state[0].value = "5"
    result = run_local_definition(definition, ResearchAgent(), sandbox_factory(definition))
    assert effective_pass(result)
    assert any(change.path == "answer.text" and change.after == "5" for change in result.mutations)


def test_research_without_source_cannot_pass():
    definition = sample_definitions()["research-retrieval"]
    definition.scenario.initial_state["documents"].clear()
    definition.environment.fixture["documents"].clear()
    result = run_local_definition(definition, ResearchAgent(), sandbox_factory(definition))
    assert not effective_pass(result)
    assert result.error_type == "LookupError"
