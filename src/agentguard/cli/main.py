
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

    if result.passed:
        console.print(
            Panel(
                "[bold green]ALLOW[/bold green]\n"
                "All configured safety checks passed.",
                title="Test Verdict",
            )
        )
    else:
        console.print(
            Panel(
                "[bold red]BLOCK[/bold red]\n"
                f"{failed_count} safety check(s) failed.",
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

    if not result.passed:
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


if __name__ == "__main__":
    app()