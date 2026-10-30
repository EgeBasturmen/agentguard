
"""Compare two exported AgentGuard runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_report(path: str | Path) -> dict[str, Any]:
    return json.loads(
        Path(path).read_text(encoding="utf-8")
    )


def compare_reports(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    base = baseline["result"]
    current = candidate["result"]

    if base["scenario_name"] != current["scenario_name"]:
        raise ValueError(
            "Cannot compare runs from different scenarios."
        )

    def assertion_map(run):
        return {
            item["name"]: item
            for item in run.get("assertions", [])
        }

    before_assertions = assertion_map(base)
    after_assertions = assertion_map(current)

    regressions = []
    improvements = []
    new_assertions = []
    removed_assertions = []

    for name in sorted(
        before_assertions.keys() | after_assertions.keys()
    ):
        old = before_assertions.get(name)
        new = after_assertions.get(name)

        if old is None:
            new_assertions.append(name)
        elif new is None:
            removed_assertions.append(name)
        elif old["passed"] and not new["passed"]:
            regressions.append({
                "name": name,
                "message": new.get("message"),
            })
        elif not old["passed"] and new["passed"]:
            improvements.append(name)

    def tool_calls(run):
        return [
            event.get("tool_name")
            for event in run.get("traces", [])
            if event.get("event_type") == "tool_started"
            and event.get("tool_name")
        ]

    base_tools = tool_calls(base)
    current_tools = tool_calls(current)

    added_tools = [
        tool
        for tool in current_tools
        if tool not in base_tools
    ]

    def mutation_map(run):
        return {
            item["path"]: item
            for item in run.get("mutations", [])
        }

    before_mutations = mutation_map(base)
    after_mutations = mutation_map(current)

    added_mutations = [
        after_mutations[path]
        for path in sorted(
            after_mutations.keys() - before_mutations.keys()
        )
    ]

    baseline_pass = baseline["verdict"] == "PASS"
    candidate_pass = candidate["verdict"] == "PASS"

    return {
        "scenario": base["scenario_name"],
        "baseline_agent": baseline["agent"],
        "candidate_agent": candidate["agent"],
        "baseline_verdict": baseline["verdict"],
        "candidate_verdict": candidate["verdict"],
        "regression_detected": (
            bool(regressions)
            or (baseline_pass and not candidate_pass)
        ),
        "assertion_regressions": regressions,
        "assertion_improvements": improvements,
        "new_assertions": new_assertions,
        "removed_assertions": removed_assertions,
        "added_tools": added_tools,
        "added_mutations": added_mutations,
        "baseline_metrics": {
            "assertions": len(base.get("assertions", [])),
            "mutations": len(base.get("mutations", [])),
            "traces": len(base.get("traces", [])),
        },
        "candidate_metrics": {
            "assertions": len(current.get("assertions", [])),
            "mutations": len(current.get("mutations", [])),
            "traces": len(current.get("traces", [])),
        },
    }
