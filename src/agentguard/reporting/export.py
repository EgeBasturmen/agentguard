
"""Self-contained HTML and JSON reports for AgentGuard runs."""

from __future__ import annotations

import html
import json
from pathlib import Path

from agentguard.core.result import RunResult

from agentguard.core.outcome import effective_pass



def export_json(
    result: RunResult,
    destination: Path,
    agent_name: str,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": 1,
        "agent": agent_name,
        "verdict": "PASS" if effective_pass(result) else "FAIL",
        "result": result.model_dump(mode="json"),
    }

    destination.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )

    return destination


def export_html(
    result: RunResult,
    destination: Path,
    agent_name: str,
) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def esc(value: object) -> str:
        return html.escape(str(value), quote=True)

    passed = effective_pass(result)
    verdict = "PASS" if passed else "FAIL"
    badge = "pass" if passed else "fail"

    assertions = "".join(
        f"<tr>"
        f"<td><span class='{'pass' if a.passed else 'fail'}'>"
        f"{'PASS' if a.passed else 'FAIL'}</span></td>"
        f"<td>{esc(a.name)}</td>"
        f"<td>{esc(a.message or '—')}</td>"
        f"</tr>"
        for a in result.assertions
    ) or "<tr><td colspan='3'>No assertions recorded</td></tr>"

    mutations = "".join(
        f"<tr>"
        f"<td>{esc(m.mutation_type)}</td>"
        f"<td>{esc(m.path)}</td>"
        f"<td><code>{esc(repr(m.before))}</code></td>"
        f"<td><code>{esc(repr(m.after))}</code></td>"
        f"</tr>"
        for m in result.mutations
    ) or "<tr><td colspan='4'>No state mutations recorded</td></tr>"

    traces = "".join(
        f"<tr>"
        f"<td>{esc(t.timestamp.isoformat())}</td>"
        f"<td>{esc(t.event_type)}</td>"
        f"<td>{esc(t.tool_name or '—')}</td>"
        f"<td><code>"
        f"{esc(json.dumps(t.payload, ensure_ascii=False, default=str))}"
        f"</code></td>"
        f"</tr>"
        for t in result.traces
    ) or "<tr><td colspan='4'>No trace events recorded</td></tr>"

    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentGuard | {esc(result.scenario_name)}</title>
<style>
:root {{
    color-scheme: dark;
    font-family: Inter, Segoe UI, Arial, sans-serif;
}}
body {{
    margin: 0;
    background: #0b1220;
    color: #e2e8f0;
    line-height: 1.5;
}}
main {{
    max-width: 1120px;
    margin: 42px auto;
    padding: 0 22px 64px;
}}
h1 {{
    font-size: 32px;
    margin: 0 0 8px;
}}
.subtitle {{
    color: #94a3b8;
    margin-bottom: 28px;
}}
.card {{
    background: #131e31;
    border: 1px solid #26344c;
    border-radius: 14px;
    padding: 24px;
    margin: 18px 0;
    overflow-x: auto;
}}
.stats {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
}}
.stat {{
    flex: 1;
    min-width: 160px;
}}
.stat small {{
    display: block;
    color: #94a3b8;
}}
.stat strong {{
    font-size: 22px;
}}
.pass {{
    color: #4ade80;
    font-weight: 750;
}}
.fail {{
    color: #fb7185;
    font-weight: 750;
}}
table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 14px;
}}
th {{
    text-align: left;
    color: #94a3b8;
    font-weight: 600;
}}
th, td {{
    padding: 12px 10px;
    border-bottom: 1px solid #28374c;
    vertical-align: top;
}}
code {{
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    color: #c4b5fd;
}}
h2 {{
    margin: 0 0 14px;
    font-size: 20px;
}}
.note {{
    color: #94a3b8;
    font-size: 13px;
}}


</style>
</head>
<body>
<main>

<h1>AgentGuard <span class="{badge}">{verdict}</span></h1>
<p class="subtitle">
AI agent behavioral testing · Local execution report
</p>

<div class="card stats">
    <div class="stat">
        <small>Scenario</small>
        <strong>{esc(result.scenario_name)}</strong>
    </div>
    <div class="stat">
        <small>Agent</small>
        <strong>{esc(agent_name)}</strong>
    </div>
    <div class="stat">
        <small>Execution status</small>
        <strong>{esc(result.status)}</strong>
    </div>
    <div class="stat">
        <small>Cleanup</small>
        <strong>{esc(result.cleanup_status)}</strong>
    </div>
</div>

<div class="card">
    <h2>Assertions ({len(result.assertions)})</h2>
    <table>
        <thead>
            <tr>
                <th>Result</th>
                <th>Check</th>
                <th>Details</th>
            </tr>
        </thead>
        <tbody>{assertions}</tbody>
    </table>
</div>

<div class="card">
    <h2>State mutations ({len(result.mutations)})</h2>
    <table>
        <thead>
            <tr>
                <th>Type</th>
                <th>Path</th>
                <th>Before</th>
                <th>After</th>
            </tr>
        </thead>
        <tbody>{mutations}</tbody>
    </table>
</div>

<div class="card">
    <h2>Trace events ({len(result.traces)})</h2>
    <table>
        <thead>
            <tr>
                <th>Time</th>
                <th>Event</th>
                <th>Tool</th>
                <th>Payload</th>
            </tr>
        </thead>
        <tbody>{traces}</tbody>
    </table>
</div>

<div class="card">
    <h2>Execution errors</h2>
    <p>
        {esc(result.error_type or 'None')}:
        {esc(result.error_message or '—')}
    </p>
</div>

<p class="note">
This report reflects configured checks and recorded events.
It is not proof of secure isolated execution.
</p>

</main>
</body>
</html>"""

    destination.write_text(document, encoding="utf-8")
    return destination
