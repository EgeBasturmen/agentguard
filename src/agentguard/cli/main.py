
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
    export_comparison_json,
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
            f"[bold red]Run error ({result.error_stage or 'unknown'}):[/bold red] "
            f"{result.error_type}: {result.error_message}"
        )

    console.print(f"[bold]Evaluation status:[/bold] {result.evaluation_status}")
    mode = result.execution_context.execution_mode if result.execution_context else "unknown"
    console.print(f"[bold]Execution assurance:[/bold] {mode}")
    console.print(f"[bold]Policy enforcement:[/bold] {result.policy_status}")
    if result.evaluation_error_type is not None:
        console.print(
            f"Evaluation error: {result.evaluation_error_type}: "
            f"{result.evaluation_error_message}"
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
                "Run execution or evaluation failed."
            )
        elif result.status != "passed":
            reasons.append(
                f"Execution status: {result.status}."
            )

        if result.cleanup_status == "unverified":
            reasons.append(
                "Container cleanup could not be verified."
            )

        if result.evaluation_status != "complete":
            reasons.append(f"Evaluation is {result.evaluation_status}.")
        if not result.assertions:
            reasons.append("No evaluation checks were recorded.")

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

    console.print(f"Comparison status: {result.comparison_status}")
    console.print(f"No regression: {result.no_regression}")

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
                "blocking regression finding(s) detected.\n"
                + "\n".join(result.failure_reasons),
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


def controlled_run(scenario_path, reference, sandbox, source_dir, include, policy_file):
    from agentguard.execution.agent_spec import AgentLaunchSpec
    from agentguard.orchestration.controller import RunController
    from agentguard.security.policy import SecurityPolicy
    environments = {
        DEFAULT_SANDBOX: "commerce",
        "agentguard.sandbox.files:FileSandbox": "files",
    }
    try:
        if sandbox not in environments:
            raise ValueError("Controlled mode requires a supported simulator; custom host sandbox imports require --trusted-local")
        loaded = load_scenario(scenario_path)
        policy = SecurityPolicy.model_validate_json(policy_file.read_bytes()) if policy_file else None
        return RunController().run(loaded, AgentLaunchSpec(reference=reference, source_dir=source_dir, include_files=include),
            environment=environments[sandbox], policy=policy)
    except (OSError, ValueError, ScenarioLoadError) as exc:
        console.print(f"Controlled execution error: {exc}")
        raise typer.Exit(code=2) from exc


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
    trusted_local: bool = typer.Option(False, "--trusted-local", help="Explicitly trust agent and sandbox imports on the host."),
    source_dir: Path = typer.Option(Path("."), "--source-dir", help="Controlled source root; only entrypoint and explicit includes are copied."),
    include: list[str] = typer.Option([], "--include", help="Additional relative Python source file (repeatable)."),
    policy_file: Path | None = typer.Option(None, "--policy", help="Controlled execution SecurityPolicy JSON."),
) -> None:

    if trusted_local:
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
        agent_name = loaded_agent.__class__.__name__
    else:
        result = controlled_run(scenario, agent, sandbox, source_dir, include, policy_file)
        agent_name = agent

    render_report(
        result=result,
        agent_name=agent_name,
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
    html_report: Path | None = typer.Option(None, "--html-report"),
    json_report: Path | None = typer.Option(None, "--json-report"),
    trusted_local: bool = typer.Option(False, "--trusted-local", help="Explicitly trust agent and sandbox imports on the host."),
    source_dir: Path = typer.Option(Path("."), "--source-dir", help="Controlled source root; only entrypoint and explicit includes are copied."),
    include: list[str] = typer.Option([], "--include", help="Additional relative Python source file (repeatable)."),
    policy_file: Path | None = typer.Option(None, "--policy", help="Controlled execution SecurityPolicy JSON."),
) -> None:

    if trusted_local:
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
    else:
        baseline_result = controlled_run(scenario, baseline, sandbox, source_dir, include, policy_file)
        candidate_result = controlled_run(scenario, candidate, sandbox, source_dir, include, policy_file)

    comparator = RegressionComparator()

    regression_result = comparator.compare(
        baseline_name=baseline,
        baseline=baseline_result,
        candidate_name=candidate,
        candidate=candidate_result,
    )

    render_regression_report(regression_result)

    if html_report is not None:
        export_comparison_html(regression_result, html_report)
    if json_report is not None:
        export_comparison_json(regression_result, json_report)

    if not regression_result.passed:
        raise typer.Exit(code=1)


@app.command("integration")
def integration_command(
    plan: Path = typer.Argument(..., help="Versioned integration plan JSON; no host agent imports."),
    preflight_only: bool = typer.Option(False, "--preflight-only", help="Discover and check compatibility; do not provision or invoke an agent."),
    json_report: Path | None = typer.Option(None, "--json-report"),
    html_report: Path | None = typer.Option(None, "--html-report"),
) -> None:
    from agentguard.integrations.plan import run_plan
    try:
        result = run_plan(plan, preflight_only=preflight_only)
    except Exception as exc:
        console.print(f"Integration preflight error: {type(exc).__name__}: {exc}")
        raise typer.Exit(code=2) from exc
    if preflight_only:
        import json
        typer.echo(json.dumps(dict(supported=result.supported,issues=result.issues,coverage_gaps=result.coverage_gaps)))
        if not result.supported: raise typer.Exit(code=1)
        return
    render_report(result, "integration-plan")
    for gap in result.coverage_gaps:
        console.print(f"Coverage gap: {gap}")
    if json_report is not None: export_json(result, json_report, "integration-plan")
    if html_report is not None: export_html(result, html_report, "integration-plan")
    if not effective_pass(result): raise typer.Exit(code=1)


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
    json_report: Path | None = typer.Option(None, "--json-report"),
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

    render_regression_report(comparison)
    console.print(f"HTML dashboard: {report_path}")
    if json_report is not None:
        export_comparison_json(comparison, json_report)
    if not comparison.passed:
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

from agentguard.generation.cli import app as scenario_app
app.add_typer(scenario_app,name="scenario")

if __name__ == "__main__":
    app()
