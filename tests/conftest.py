import pytest

from agentguard.core.scenario import Scenario
from agentguard.runner.runner import ScenarioRunner
from agentguard.sandbox.generic import GenericSandbox


INTEGRATION_MODULES = {
    "test_controlled_ipc.py", "test_controlled_policy.py", "test_cli.py", "test_comparison_cli.py", "test_demo_runner.py",
    "test_demo_dashboard.py", "test_report_export.py", "test_report_dashboard.py",
    "test_report_comparison.py", "test_loader.py", "test_process_ipc.py",
    "test_runner.py", "test_regression.py", "test_crm_agent.py",
    "test_document_sandbox.py", "test_sandbox_factory.py", "test_phase0_examples.py",
    "test_evaluation_errors.py", "test_comparison_integrity.py",
}


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    """Assign every case exactly one execution layer before -m filtering."""
    layers = {"unit", "integration", "docker"}
    for item in items:
        declared = {mark.name for mark in item.iter_markers()} & layers
        if not declared:
            integration = (
                item.path.name in INTEGRATION_MODULES
                or getattr(item, "originalname", None) == "test_docker_create_output_overflow_triggers_cleanup"
            )
            item.add_marker(pytest.mark.integration if integration else pytest.mark.unit)
            declared = {"integration" if integration else "unit"}
        if len(declared) != 1:
            raise pytest.UsageError(f"Test must have exactly one execution layer: {item.nodeid}")


@pytest.fixture
def make_run():
    class Noop:
        def run(self, task, sandbox):
            return "done"

    def run(scenario=None, agent=None):
        if scenario is None:
            scenario = Scenario(
                name="generic", task="Read a value", initial_state={"value": 1},
                expected_state=[{"path": "value", "value": 1}],
                tool_expectations=[{"tool_name": "resource.read", "max_times": 1}],
            )
        return ScenarioRunner(GenericSandbox).run(scenario, agent or Noop())
    return run
