
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agentguard.adapters.python import (
    AgentLoadError,
    load_python_agent,
)
from agentguard.core.result import RunResult
from agentguard.regression.comparator import RegressionComparator
from agentguard.regression.models import RegressionResult
from agentguard.runner.runner import ScenarioRunner
from agentguard.spec.loader import (
    ScenarioLoadError,
    load_scenario,
)
from agentguard.adapters.sandbox import (
    SandboxLoadError,
    load_python_sandbox,
)
from agentguard.sandbox.commerce import CommerceSandbox

from agentguard.reporting.export import (
    effective_pass,
    export_html,
    export_json,
)

from agentguard.reporting.comparison import (
    load_report,
    compare_reports,
)
from agentguard.reporting.dashboard import export_comparison_html
from agentguard.demo.runner import run_demo
from agentguard.core.outcome import effective_pass
app = typer.Typer(
    name="agentguard",
    help="Regression and side-effect testing for AI agents.",
    no_args_is_help=True,
)

console = Console()

DEFAULT_SCENARIO = Path("scenarios/refund_order.yaml")
DEFAULT_SANDBOX = (
    "agentguard.sandbox.commerce:CommerceSandbox"
)


def render_report(
    result: RunResult,
    agent_name: str,
) -> None:
    console.print()
    console.print(
        Panel.fit(
            "[bold]AgentGuard v0.1[/bold]\n"
            "Test what your agent does, not just what it says."
        )
    )

    console.print(
        f"\n[bold]Scenario:[/bold] {result.scenario_name}"
    )
    console.print(
        f"[bold]Agent:[/bold] {agent_name}\n"
    )


    console.print(
        f"[bold]Execution status:[/bold] "
        f"{result.status.upper()}"
    )

    if result.status == "error":
        console.print(
            "[bold red]Agent execution error:[/bold red] "
            f"{result.error_type}: {result.error_message}"
        )

    console.print()

    table = Table(title="Safety Checks")
    table.add_column("Status")
    table.add_column("Check")
    table.add_column("Details")

    failed_count = 0

    for assertion in result.assertions:
        if assertion.passed:
            status = "[green]PASS[/green]"
            details = "-"
        else:
            status = "[red]FAIL[/red]"
            details = assertion.message or "Check failed."
            failed_count += 1

        table.add_row(
            status,
            assertion.name,
            details,
        )

    console.print(table)

    if result.mutations:
        mutation_table = Table(
            title="Observed State Mutations"
        )
        mutation_table.add_column("Type")
        mutation_table.add_column("Path")
        mutation_table.add_column("Before")
        mutation_table.add_column("After")

        for mutation in result.mutations:
            mutation_table.add_row(
                mutation.mutation_type,
                mutation.path,
                repr(mutation.before),
                repr(mutation.after),
            )

        console.print()
        console.print(mutation_table)

    console.print()

    if effective_pass(result):
        console.print(
            Panel(
                "[bold green]ALLOW[/bold green]\n"
                "All configured safety checks passed.",
                title="Test Verdict",
            )
        )

    else:
        reasons = []

        if failed_count:
            reasons.append(
                f"{failed_count} safety check(s) failed."
            )

        if result.status == "error":
            reasons.append(
                "Agent execution failed."
            )
        elif result.status != "passed":
            reasons.append(
                f"Execution status: {result.status}."
            )

        if result.cleanup_status == "unverified":
            reasons.append(
                "Container cleanup could not be verified."
            )

        if not result.passed and not reasons:
            reasons.append(
                "Run was not marked as passed."
            )

        if not reasons:
            reasons.append(
                "Run outcome is inconsistent and cannot be accepted."
            )

        console.print(
            Panel(
                "[bold red]BLOCK[/bold red]\n"
                + "\n".join(reasons),
                title="Test Verdict",
            )
        )



def render_regression_report(
    result: RegressionResult,
) -> None:
    console.print()
    console.print(
        Panel.fit(
            "[bold]AgentGuard Regression[/bold]\n"
            "Behavior change analysis"
        )
    )

    console.print(
        f"\n[bold]Baseline:[/bold] "
        f"{result.baseline_name}"
    )
    console.print(
        f"[bold]Candidate:[/bold] "
        f"{result.candidate_name}\n"
    )

    table = Table(
        title="Regression Findings",
        expand=False,
    )

    table.add_column("Severity", no_wrap=True)
    table.add_column("Type", no_wrap=True)
    table.add_column("Behavior")
    table.add_column("Baseline")
    table.add_column("Candidate")

    for finding in result.findings:
        severity = finding.severity.upper()

        if finding.severity == "critical":
            severity_text = (
                f"[bold red]{severity}[/bold red]"
            )
        elif finding.severity == "error":
            severity_text = f"[red]{severity}[/red]"
        elif finding.severity == "warning":
            severity_text = f"[yellow]{severity}[/yellow]"
        else:
            severity_text = f"[blue]{severity}[/blue]"

        table.add_row(
            severity_text,
            finding.finding_type,
            finding.key,
            repr(finding.baseline),
            repr(finding.candidate),
        )

    console.print(table)
    console.print()

    if result.passed:
        console.print(
            Panel(
                "[bold green]ALLOW[/bold green]\n"
                "No blocking behavioral regressions detected.",
                title="Regression Verdict",
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]BLOCK[/bold red]\n"
                f"{result.blocking_regressions} "
                "blocking regression finding(s) detected.",
                title="Regression Verdict",
            )
        )


def load_inputs(
    scenario_path: Path,
    agent_reference: str,
    sandbox_reference: str = DEFAULT_SANDBOX,
):
    try:
        scenario = load_scenario(scenario_path)

    except ScenarioLoadError as exc:
        console.print(
            f"[bold red]Scenario error[/bold red]\n{exc}"
        )
        raise typer.Exit(code=2) from exc

    try:
        agent = load_python_agent(agent_reference)

    except AgentLoadError as exc:
        console.print(
            f"[bold red]Agent error[/bold red]\n{exc}"
        )
        raise typer.Exit(code=2) from exc

    try:
        sandbox_factory = load_python_sandbox(
            sandbox_reference
        )

    except SandboxLoadError as exc:
        console.print(
            f"[bold red]Sandbox error[/bold red]\n{exc}"
        )
        raise typer.Exit(code=2) from exc

    return scenario, agent, sandbox_factory


@app.command("test")
def test_command(
    agent: str = typer.Option(
        ...,
        "--agent",
        "-a",
        help=(
            "Python agent reference, e.g. "
            "examples.refund_agent:GoodRefundAgent"
        ),
    ),
    scenario: Path = typer.Option(
        DEFAULT_SCENARIO,
        "--scenario",
        "-s",
        help="Path to scenario YAML.",
    ),
    sandbox: str = typer.Option(
        DEFAULT_SANDBOX,
        "--sandbox",
        help=(
            "Python sandbox reference, e.g. "
            "agentguard.sandbox.files:FileSandbox"
        ),
    ),
    html_report: Path | None = typer.Option(
        None,
        "--html-report",
        help="Save a standalone HTML report.",
    ),
    json_report: Path | None = typer.Option(
        None,
        "--json-report",
        help="Save a machine-readable JSON report.",
    ),
) -> None:

    (
        loaded_scenario,
        loaded_agent,
        sandbox_factory,
    ) = load_inputs(
        scenario_path=scenario,
        agent_reference=agent,
        sandbox_reference=sandbox,
    )

    runner = ScenarioRunner(
        sandbox_factory=sandbox_factory,
    )

    result = runner.run(
        scenario=loaded_scenario,
        agent=loaded_agent,
    )

    render_report(
        result=result,
        agent_name=loaded_agent.__class__.__name__,
    )
    if html_report is not None:
        export_html(result, html_report, agent)
        console.print(f"HTML report: {html_report}")

    if json_report is not None:
        export_json(result, json_report, agent)
        console.print(f"JSON report: {json_report}")

    if not effective_pass(result):
        raise typer.Exit(code=1)


@app.command("regression")
def regression_command(
    baseline: str = typer.Option(
        ...,
        "--baseline",
        "-b",
        help="Baseline Python agent reference.",
    ),
    candidate: str = typer.Option(
        ...,
        "--candidate",
        "-c",
        help="Candidate Python agent reference.",
    ),
    scenario: Path = typer.Option(
        DEFAULT_SCENARIO,
        "--scenario",
        "-s",
        help="Path to scenario YAML.",
    ),
    sandbox: str = typer.Option(
        DEFAULT_SANDBOX,
        "--sandbox",
        help="Python sandbox reference.",
    ),
) -> None:

    try:
        loaded_scenario = load_scenario(scenario)

    except ScenarioLoadError as exc:
        console.print(
            f"[bold red]Scenario error[/bold red]\n{exc}"
        )
        raise typer.Exit(code=2) from exc

    try:
        baseline_agent = load_python_agent(baseline)
        candidate_agent = load_python_agent(candidate)

    except AgentLoadError as exc:
        console.print(
            f"[bold red]Agent error[/bold red]\n{exc}"
        )
        raise typer.Exit(code=2) from exc

    try:
        sandbox_factory = load_python_sandbox(sandbox)

    except SandboxLoadError as exc:
        console.print(
            f"[bold red]Sandbox error[/bold red]\n{exc}"
        )
        raise typer.Exit(code=2) from exc

    runner = ScenarioRunner(
        sandbox_factory=sandbox_factory,
    )

    baseline_result = runner.run(
        scenario=loaded_scenario,
        agent=baseline_agent,
    )

    candidate_result = runner.run(
        scenario=loaded_scenario,
        agent=candidate_agent,
    )

    comparator = RegressionComparator()

    regression_result = comparator.compare(
        baseline_name=baseline,
        baseline=baseline_result,
        candidate_name=candidate,
        candidate=candidate_result,
    )

    render_regression_report(regression_result)

    if not regression_result.passed:
        raise typer.Exit(code=1)


@app.command("compare")
def compare_command(
    baseline: Path = typer.Argument(
        ...,
        help="Baseline AgentGuard JSON report.",
    ),
    candidate: Path = typer.Argument(
        ...,
        help="Candidate AgentGuard JSON report.",
    ),
    html_report: Path = typer.Option(
        Path("reports/regression.html"),
        "--html-report",
        help="Destination for the regression dashboard.",
    ),
):
    """Compare two agent runs and export an HTML dashboard."""

    try:
        baseline_data = load_report(baseline)
        candidate_data = load_report(candidate)

        comparison = compare_reports(
            baseline_data,
            candidate_data,
        )

        report_path = export_comparison_html(
            comparison,
            html_report,
        )

    except (OSError, ValueError, KeyError, TypeError) as exc:
        typer.echo(f"Comparison error: {exc}", err=True)
        raise typer.Exit(code=2)

    if comparison["regression_detected"]:
        console.print("[bold red]REGRESSION DETECTED[/bold red]")
    else:
        console.print("[bold green]NO REGRESSION DETECTED[/bold green]")

    console.print(f"Scenario: {comparison['scenario']}")
    console.print(
        f"Baseline: {comparison['baseline_verdict']} "
        f"({comparison['baseline_agent']})"
    )
    console.print(
        f"Candidate: {comparison['candidate_verdict']} "
        f"({comparison['candidate_agent']})"
    )

    for regression in comparison["assertion_regressions"]:
        console.print(
            f"[red]FAIL[/red] {regression['name']}"
        )

    for tool in comparison["added_tools"]:
        console.print(f"[yellow]New tool:[/yellow] {tool}")

    console.print(f"HTML dashboard: {report_path}")

    if comparison["regression_detected"]:
        raise typer.Exit(code=1)

@app.command("demo")
def demo_command(
    output_dir: Path = typer.Option(
        Path("reports/demo"),
        "--output-dir",
        help="Directory for demo reports.",
    ),
):
    """Run the bundled AgentGuard demo scenarios."""

    console.print(
        "[bold cyan]Running AgentGuard Demo...[/bold cyan]"
    )

    summary = run_demo(output_dir)

    for case in summary["cases"]:
        console.print()
        console.print(f"[bold]{case['name'].upper()}[/bold]")
        console.print(
            f"Good agent: {'PASS' if case['good_passed'] else 'FAIL'}"
        )
        console.print(
            f"Bad agent: {'FAIL' if not case['bad_passed'] else 'PASS'}"
        )
        console.print(
            f"Regression: {case['regression_detected']}"
        )
        console.print(
            f"Dashboard: {case['dashboard']}"
        )



    if summary["success"]:
        console.print(
            "\n[bold green]DEMO COMPLETED SUCCESSFULLY[/bold green]"
        )
    else:
        console.print(
            "\n[bold red]DEMO VALIDATION FAILED[/bold red]"
        )
        raise typer.Exit(code=1)

    console.print()
    console.print(
        f"[bold cyan]Demo dashboard:[/bold cyan] "
        f"{summary['index']}"
    )

if __name__ == "__main__":
    app()
