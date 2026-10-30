import json

from agentguard.core.result import (
    AssertionResult,
    RunResult,
)
from agentguard.reporting.export import (
    effective_pass,
    export_html,
    export_json,
)


def test_reports_export(tmp_path):
    result = RunResult(
        scenario_name="<refund>",
        passed=False,
        status="failed",
        assertions=[
            AssertionResult(
                name="no double refund",
                passed=False,
                message="<unsafe>",
            )
        ],
    )

    json_path = export_json(
        result,
        tmp_path / "run.json",
        "BadAgent",
    )

    html_path = export_html(
        result,
        tmp_path / "run.html",
        "BadAgent",
    )

    payload = json.loads(
        json_path.read_text(encoding="utf-8")
    )

    assert payload["verdict"] == "FAIL"
    assert payload["result"]["scenario_name"] == "<refund>"

    content = html_path.read_text(encoding="utf-8")

    assert "&lt;refund&gt;" in content
    assert "&lt;unsafe&gt;" in content
    assert "<unsafe>" not in content


def test_cleanup_uncertainty_is_not_pass():
    result = RunResult(
        scenario_name="test",
        passed=True,
        cleanup_status="unverified",
    )

    assert not effective_pass(result)