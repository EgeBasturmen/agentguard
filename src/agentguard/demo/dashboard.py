
"""Generate an HTML index for AgentGuard demo reports."""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any


def export_demo_dashboard(
    summary: dict[str, Any],
    output_dir: str | Path,
) -> Path:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cases = summary.get("cases", [])

    scenario_cards = []

    for case in cases:
        name = str(case["name"])
        safe_name = escape(name)

        good_passed = bool(case["good_passed"])
        bad_passed = bool(case["bad_passed"])
        regression = bool(case["regression_detected"])

        good_status = "PASS" if good_passed else "FAIL"
        bad_status = "PASS" if bad_passed else "FAIL"

        good_class = "pass" if good_passed else "fail"
        bad_class = "pass" if bad_passed else "fail"

        regression_text = (
            "REGRESSION DETECTED"
            if regression
            else "NO REGRESSION"
        )

        # Demo reports use predictable filenames.
        # Link only to files in the output directory.
        good_report = f"{name}_good.html"
        bad_report = f"{name}_bad.html"
        regression_report = f"{name}_regression.html"

        scenario_cards.append(
            f"""
            <section class="card">
                <div class="card-header">
                    <h2>{safe_name.upper()}</h2>
                    <span class="regression">
                        {regression_text}
                    </span>
                </div>

                <div class="runs">
                    <div class="run">
                        <span>Baseline Agent</span>
                        <strong class="{good_class}">
                            {good_status}
                        </strong>
                        <a href="{escape(good_report, quote=True)}">
                            View report
                        </a>
                    </div>

                    <div class="run">
                        <span>Candidate Agent</span>
                        <strong class="{bad_class}">
                            {bad_status}
                        </strong>
                        <a href="{escape(bad_report, quote=True)}">
                            View report
                        </a>
                    </div>
                </div>

                <a class="comparison-link"
                   href="{escape(regression_report, quote=True)}">
                    Open Regression Comparison
                </a>
            </section>
            """
        )

    regression_count = sum(
        bool(case["regression_detected"])
        for case in cases
    )

    scenario_count = len(cases)
    run_count = scenario_count * 2

    document = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport"
      content="width=device-width, initial-scale=1">
<title>AgentGuard | Demo Dashboard</title>

<style>
:root {
    color-scheme: dark;
    font-family: Inter, Segoe UI, Arial, sans-serif;
}

* { box-sizing: border-box; }

body {
    margin: 0;
    background: #0b1220;
    color: #e2e8f0;
}

main {
    max-width: 1100px;
    margin: 40px auto;
    padding: 0 24px 60px;
}

h1 {
    font-size: 36px;
    margin-bottom: 8px;
}

.subtitle {
    color: #94a3b8;
    margin-bottom: 32px;
}

.stats {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 16px;
    margin-bottom: 24px;
}

.stat, .card {
    background: #131e31;
    border: 1px solid #26344c;
    border-radius: 14px;
    padding: 24px;
}

.stat span {
    color: #94a3b8;
    font-size: 14px;
}

.stat strong {
    display: block;
    font-size: 32px;
    margin-top: 8px;
}

.card {
    margin-bottom: 20px;
}

.card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
}

.card-header h2 {
    margin: 0;
}

.regression {
    color: #fb7185;
    font-size: 13px;
    font-weight: 700;
}

.runs {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
    margin: 24px 0;
}

.run {
    background: #0e1728;
    border: 1px solid #26344c;
    border-radius: 10px;
    padding: 18px;
}

.run span {
    display: block;
    color: #94a3b8;
    font-size: 13px;
}

.run strong {
    display: block;
    font-size: 24px;
    margin: 12px 0;
}

.pass { color: #4ade80; }
.fail { color: #fb7185; }

a {
    color: #93c5fd;
    text-decoration: none;
}

a:hover {
    text-decoration: underline;
}

.comparison-link {
    display: inline-block;
    background: #2563eb;
    color: white;
    padding: 12px 18px;
    border-radius: 8px;
    font-weight: 600;
}

.comparison-link:hover {
    background: #1d4ed8;
    text-decoration: none;
}

.footer {
    margin-top: 30px;
    color: #94a3b8;
    font-size: 12px;
}

@media (max-width: 650px) {
    .stats, .runs {
        grid-template-columns: 1fr;
    }

    .card-header {
        align-items: flex-start;
        flex-direction: column;
    }
}
</style>
</head>

<body>
<main>
    <h1>AgentGuard</h1>
    <p class="subtitle">
        AI Agent Behavioral Testing · Demo Dashboard
    </p>

    <div class="stats">
        <div class="stat">
            <span>Scenarios</span>
            <strong>__SCENARIOS__</strong>
        </div>
        <div class="stat">
            <span>Agent Runs</span>
            <strong>__RUNS__</strong>
        </div>
        <div class="stat">
            <span>Detected Regressions</span>
            <strong>__REGRESSIONS__</strong>
        </div>
    </div>

    __CARDS__

    <p class="footer">
        Local demonstration with simulated tools.
        This report does not establish secure isolated execution.
    </p>
</main>
</body>
</html>
"""

    document = (
        document
        .replace("__SCENARIOS__", str(scenario_count))
        .replace("__RUNS__", str(run_count))
        .replace("__REGRESSIONS__", str(regression_count))
        .replace("__CARDS__", "\n".join(scenario_cards))
    )

    destination = output_dir / "index.html"
    destination.write_text(document, encoding="utf-8")
    return destination
