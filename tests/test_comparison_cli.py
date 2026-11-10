import json

import pytest
from typer.testing import CliRunner

from agentguard.adapters.python import load_python_agent
from agentguard.cli.main import app
from agentguard.reporting.export import export_json
from agentguard.runner.runner import ScenarioRunner
from agentguard.spec.loader import load_scenario


@pytest.mark.parametrize("baseline,candidate,expected", [
    ("GoodRefundAgent", "GoodRefundAgent", 0),
    ("GoodRefundAgent", "BadRefundAgent", 1),
    ("GoodRefundAgent", "DuplicateRefundAgent", 1),
    ("GoodRefundAgent", "CrashingRefundAgent", 1),
    ("CrashingRefundAgent", "CrashingRefundAgent", 1),
    ("CrashingRefundAgent", "GoodRefundAgent", 0),
])
def test_live_and_report_cli_have_identical_gate(tmp_path, baseline, candidate, expected):
    runner = CliRunner()
    scenario_path = "scenarios/refund_order.yaml"
    refs = [f"examples.refund_agent:{name}" for name in (baseline, candidate)]
    reports = []
    for index, reference in enumerate(refs):
        result = ScenarioRunner().run(load_scenario(scenario_path), load_python_agent(reference))
        path = tmp_path / f"run-{index}.json"
        export_json(result, path, reference)
        reports.append(path)
    live_json, stored_json = tmp_path / "live.json", tmp_path / "stored.json"
    live = runner.invoke(app, [
        "regression", "--trusted-local", "--baseline", refs[0], "--candidate", refs[1],
        "--scenario", scenario_path, "--json-report", str(live_json),
        "--html-report", str(tmp_path / "live.html"),
    ])
    stored = runner.invoke(app, [
        "compare", str(reports[0]), str(reports[1]),
        "--json-report", str(stored_json), "--html-report", str(tmp_path / "stored.html"),
    ])
    assert live.exit_code == stored.exit_code == expected, (live.output, stored.output)
    left, right = (json.loads(path.read_text(encoding="utf-8")) for path in (live_json, stored_json))
    for field in ("gate_decision", "passed", "no_regression", "regression_detected", "comparison_status", "findings"):
        assert left[field] == right[field]
    expected_label = "ALLOW" if expected == 0 else "BLOCK"
    for filename in ("live.html", "stored.html"):
        assert expected_label in (tmp_path / filename).read_text(encoding="utf-8")
    assert expected_label in live.output and expected_label in stored.output


@pytest.mark.parametrize("mode", ["legacy", "removed_check", "changed_fingerprint", "both_failed"])
def test_report_cli_blocks_without_a_new_behavior_regression(tmp_path, make_run, mode):
    base, candidate = make_run(), make_run()
    if mode == "legacy":
        base.comparison_manifest = candidate.comparison_manifest = None
    elif mode == "removed_check":
        candidate.assertions.pop()
    elif mode == "changed_fingerprint":
        scenario = candidate.comparison_manifest.scenario.model_copy(deep=True)
        scenario.task = "changed"
        candidate = make_run(scenario)
    else:
        for run in (base, candidate):
            run.status, run.passed = "failed", False
            run.assertions[0].passed = False
    paths = [export_json(run, tmp_path / f"{index}.json", "Agent") for index, run in enumerate((base, candidate))]
    json_path = tmp_path / "comparison.json"
    result = CliRunner().invoke(app, [
        "compare", *map(str, paths), "--json-report", str(json_path),
        "--html-report", str(tmp_path / "comparison.html"),
    ])
    assert result.exit_code == 1, result.output
    assert "BLOCK" in result.output
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["gate_decision"] == "BLOCK"
    if mode == "both_failed":
        assert data["no_regression"] is True


def test_forged_report_cli_is_input_error(tmp_path, make_run):
    path = export_json(make_run(), tmp_path / "run.json", "Agent")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["result"]["cleanup_status"] = "unverified"
    path.write_text(json.dumps(payload), encoding="utf-8")
    html = tmp_path / "comparison.html"
    result = CliRunner().invoke(app, ["compare", str(path), str(path), "--html-report", str(html)])
    assert result.exit_code == 2
    assert "Comparison error" in result.output
    assert not html.exists()
