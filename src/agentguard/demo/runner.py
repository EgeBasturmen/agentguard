
"""Run bundled AgentGuard demonstration scenarios."""

import json
from dataclasses import dataclass
from pathlib import Path

from agentguard.core.outcome import effective_pass
from agentguard.core.result import RunResult
from agentguard.demo.dashboard import export_demo_dashboard
from agentguard.demo.fixtures.refund_agent import (
    GoodRefundAgent,
    BadRefundAgent,
)
from agentguard.demo.fixtures.crm_agent import (
    GoodCRMAgent,
    BadCRMAgent,
)
from agentguard.reporting.comparison import compare_reports
from agentguard.reporting.dashboard import export_comparison_html
from agentguard.reporting.export import export_html, export_json
from agentguard.runner.runner import ScenarioRunner
from agentguard.spec.loader import load_scenario
from agentguard.sandbox.commerce import CommerceSandbox
from agentguard.sandbox.crm import CRMSandbox


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@dataclass(frozen=True)
class DemoCase:
    name: str
    scenario_path: Path
    sandbox_factory: type
    good_agent: type
    bad_agent: type


DEMO_CASES = [
    DemoCase(
        name="refund",
        scenario_path=FIXTURES_DIR / "refund_order.yaml",
        sandbox_factory=CommerceSandbox,
        good_agent=GoodRefundAgent,
        bad_agent=BadRefundAgent,
    ),
    DemoCase(
        name="crm",
        scenario_path=FIXTURES_DIR / "crm_update.yaml",
        sandbox_factory=CRMSandbox,
        good_agent=GoodCRMAgent,
        bad_agent=BadCRMAgent,
    ),
]


def run_demo(output_dir: Path = Path("reports/demo")) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "cases": [],
        "success": True,
    }

    for case in DEMO_CASES:
        scenario = load_scenario(case.scenario_path)

        runner = ScenarioRunner(
            sandbox_factory=case.sandbox_factory,
        )

        good_result: RunResult = runner.run(
            scenario,
            case.good_agent(),
        )

        bad_result: RunResult = runner.run(
            scenario,
            case.bad_agent(),
        )

        good_json = output_dir / f"{case.name}_good.json"
        bad_json = output_dir / f"{case.name}_bad.json"

        export_json(
            good_result,
            good_json,
            case.good_agent.__name__,
        )

        export_json(
            bad_result,
            bad_json,
            case.bad_agent.__name__,
        )

        export_html(
            good_result,
            output_dir / f"{case.name}_good.html",
            case.good_agent.__name__,
        )

        export_html(
            bad_result,
            output_dir / f"{case.name}_bad.html",
            case.bad_agent.__name__,
        )

        baseline = json.loads(
            good_json.read_text(encoding="utf-8")
        )

        candidate = json.loads(
            bad_json.read_text(encoding="utf-8")
        )

        comparison = compare_reports(
            baseline,
            candidate,
        )

        dashboard = output_dir / f"{case.name}_regression.html"

        export_comparison_html(
            comparison,
            dashboard,
        )

        good_accepted = effective_pass(good_result)
        bad_accepted = effective_pass(bad_result)

        expected = (
            good_accepted
            and not bad_accepted
            and comparison["regression_detected"]
        )

        if not expected:
            summary["success"] = False


        summary["cases"].append(
            {
                "name": case.name,
                "good_passed": good_accepted,
                "bad_passed": bad_accepted,
                "regression_detected": comparison[
                    "regression_detected"
                ],
                "dashboard": str(dashboard),
            }
        )


    dashboard_path = export_demo_dashboard(
        summary,
        output_dir,
    )

    summary["index"] = str(dashboard_path)

    return summary
