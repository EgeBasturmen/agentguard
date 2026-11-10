
"""Standalone HTML dashboard for AgentGuard regression comparisons."""

from __future__ import annotations

import html
import re
from pathlib import Path
from agentguard.regression.models import ComparisonResult
from agentguard.reporting.comparison import comparison_view


def export_comparison_html(
    comparison: ComparisonResult,
    destination: str | Path,
) -> Path:
    comparison = comparison_view(comparison)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def esc(value: object) -> str:
        return html.escape(str(value), quote=True)

    def render_list(items: list[str]) -> str:
        if not items:
            return "<p class='muted'>None detected</p>"
        return "<ul>" + "".join(
            f"<li>{esc(item)}</li>" for item in items
        ) + "</ul>"

    def render_mutations(items: list[dict]) -> str:
        if not items:
            return "<p class='muted'>No additional mutations</p>"

        rows = []
        for item in items:
            rows.append(
                "<tr>"
                f"<td><code>{esc(item.get('path', ''))}</code></td>"
                f"<td>{esc(item.get('mutation_type', ''))}</td>"
                f"<td><code>{esc(item.get('before', ''))}</code></td>"
                f"<td><code>{esc(item.get('after', ''))}</code></td>"
                "</tr>"
            )

        return (
            "<table><thead><tr>"
            "<th>State path</th><th>Type</th>"
            "<th>Before</th><th>After</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )

    regressions = comparison.get("assertion_regressions", [])

    regression_items = [
        f"{item.get('name', '')}: {item.get('message') or ''}"
        for item in regressions
    ]

    detected = comparison.get("regression_detected", False)
    outcome = "REGRESSION DETECTED" if detected else "NO REGRESSION DETECTED"
    if comparison["comparison_status"] != "verified":
        outcome = "COMPARISON UNVERIFIED"
    outcome_class = "success" if comparison["gate_decision"] == "ALLOW" else "danger"

    base_metrics = comparison.get("baseline_metrics", {})
    candidate_metrics = comparison.get("candidate_metrics", {})

    document = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentGuard | Regression Dashboard</title>
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
h1 { font-size: 32px; margin-bottom: 8px; }
h2 { font-size: 19px; margin: 0 0 18px; }
.muted { color: #94a3b8; }
.card {
    background: #131e31;
    border: 1px solid #26344c;
    border-radius: 14px;
    padding: 24px;
    margin: 18px 0;
    overflow-x: auto;
}
.grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
}
.grid .card { margin: 0; }
.success { color: #4ade80; }
.danger { color: #fb7185; }
.verdict { font-size: 28px; font-weight: 800; }
.agent {
    overflow-wrap: anywhere;
    font-size: 14px;
}
table {
    width: 100%;
    border-collapse: collapse;
    text-align: left;
}
th, td {
    padding: 12px;
    border-bottom: 1px solid #26344c;
    vertical-align: top;
}
th { color: #94a3b8; }
code {
    color: #c4b5fd;
    overflow-wrap: anywhere;
}
li {
    padding: 8px 0;
    overflow-wrap: anywhere;
}
@media (max-width: 650px) {
    .grid { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<main>
<h1>AgentGuard Regression Dashboard</h1>
<p class="muted">Scenario: __SCENARIO__</p>

<div class="card">
    <div class="verdict __OUTCOME_CLASS__">__GATE_DECISION__</div>
    <div class="verdict __OUTCOME_CLASS__">__OUTCOME__</div>
    <p>Comparison: __COMPARISON_STATUS__</p>
    __FAILURE_REASONS__
    <p class="muted">
        Comparison of recorded local agent test runs.
    </p>
</div>

<div class="grid">
    <div class="card">
        <h2>Baseline</h2>
        <div class="verdict">__BASE_VERDICT__</div>
        <p class="agent">__BASE_AGENT__</p>
        <p class="muted">
            __BASE_ASSERTIONS__ assertions ·
            __BASE_MUTATIONS__ mutations ·
            __BASE_TRACES__ traces
        </p>
    </div>
    <div class="card">
        <h2>Candidate</h2>
        <div class="verdict">__CANDIDATE_VERDICT__</div>
        <p class="agent">__CANDIDATE_AGENT__</p>
        <p class="muted">
            __CANDIDATE_ASSERTIONS__ assertions ·
            __CANDIDATE_MUTATIONS__ mutations ·
            __CANDIDATE_TRACES__ traces
        </p>
    </div>
</div>

<div class="card">
    <h2>Removed Evaluation Checks</h2>
    __REMOVED_CHECKS__
</div>

<div class="card">
    <h2>Assertion Regressions</h2>
    __REGRESSIONS__
</div>

<div class="card">
    <h2>New Tool Calls</h2>
    __TOOLS__
</div>

<div class="card">
    <h2>Additional State Mutations</h2>
    __MUTATIONS__
</div>

<div class="card">
    <h2>Assertion Improvements</h2>
    __IMPROVEMENTS__
</div>

<p class="muted">
Comparison reflects the recorded scenario and assertions.
It does not establish secure isolated execution.
</p>
</main>
</body>
</html>"""

    replacements = {
        "__SCENARIO__": esc(comparison.get("scenario", "")),
        "__OUTCOME_CLASS__": outcome_class,
        "__OUTCOME__": outcome,
        "__GATE_DECISION__": comparison["gate_decision"],
        "__COMPARISON_STATUS__": esc(comparison["comparison_status"]),
        "__FAILURE_REASONS__": render_list(comparison["failure_reasons"]),
        "__REMOVED_CHECKS__": render_list(comparison["removed_assertions"]),
        "__BASE_VERDICT__": esc(comparison.get("baseline_verdict", "")),
        "__CANDIDATE_VERDICT__": esc(comparison.get("candidate_verdict", "")),
        "__BASE_AGENT__": esc(comparison.get("baseline_agent", "")),
        "__CANDIDATE_AGENT__": esc(comparison.get("candidate_agent", "")),
        "__BASE_ASSERTIONS__": str(base_metrics.get("assertions", 0)),
        "__BASE_MUTATIONS__": str(base_metrics.get("mutations", 0)),
        "__BASE_TRACES__": str(base_metrics.get("traces", 0)),
        "__CANDIDATE_ASSERTIONS__": str(candidate_metrics.get("assertions", 0)),
        "__CANDIDATE_MUTATIONS__": str(candidate_metrics.get("mutations", 0)),
        "__CANDIDATE_TRACES__": str(candidate_metrics.get("traces", 0)),
        "__REGRESSIONS__": render_list(regression_items),
        "__TOOLS__": render_list(comparison.get("added_tools", [])),
        "__MUTATIONS__": render_mutations(
            comparison.get("added_mutations", [])
        ),
        "__IMPROVEMENTS__": render_list(
            comparison.get("assertion_improvements", [])
        ),
    }

    document = re.sub(r"__[A-Z_]+__", lambda match: replacements[match.group()], document)

    destination.write_text(document, encoding="utf-8")
    return destination
