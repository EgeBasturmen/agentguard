from collections import Counter

from agentguard.core.result import RunResult
from agentguard.regression.models import (
    RegressionFinding,
    RegressionResult,
)


BLOCKING_SEVERITIES = {
    "error",
    "critical",
}


class RegressionComparator:

    def compare(
        self,
        baseline_name: str,
        baseline: RunResult,
        candidate_name: str,
        candidate: RunResult,
    ) -> RegressionResult:

        findings: list[RegressionFinding] = []

        if candidate.status == "error":
            findings.append(
                RegressionFinding(
                    finding_type="new_execution_error",
                    key="agent:execution",
                    severity="critical",
                    message=(
                        "Candidate agent failed during execution: "
                        f"{candidate.error_type}: {candidate.error_message}"
                    ),
                    baseline=baseline.status,
                    candidate=candidate.status,
                )
            )

        elif baseline.status == "error":
            findings.append(
                RegressionFinding(
                    finding_type="resolved_execution_error",
                    key="agent:execution",
                    severity="info",
                    message=(
                        "Candidate completed without an execution error."
                    ),
                    baseline=baseline.status,
                    candidate=candidate.status,
                )
            )

        findings.extend(
            self._compare_assertions(
                baseline,
                candidate,
            )
        )

        findings.extend(
            self._compare_tool_calls(
                baseline,
                candidate,
            )
        )

        findings.extend(
            self._compare_mutations(
                baseline,
                candidate,
            )
        )

        blocking_regressions = sum(
            1
            for finding in findings
            if (
                finding.severity in BLOCKING_SEVERITIES
                and finding.finding_type not in {
                    "resolved_assertion_failure",
                    "removed_tool_call",
                    "removed_state_mutation",
                }
            )
        )

        return RegressionResult(
            baseline_name=baseline_name,
            candidate_name=candidate_name,
            baseline_run=baseline,
            candidate_run=candidate,
            findings=findings,
            blocking_regressions=blocking_regressions,
            passed=blocking_regressions == 0,
        )



    def _compare_assertions(
        self,
        baseline: RunResult,
        candidate: RunResult,
    ) -> list[RegressionFinding]:

        findings = []

        baseline_assertions = {
            assertion.name: assertion
            for assertion in baseline.assertions
        }

        candidate_assertions = {
            assertion.name: assertion
            for assertion in candidate.assertions
        }

        common_names = (
            baseline_assertions.keys()
            & candidate_assertions.keys()
        )

        for name in sorted(common_names):

            before = baseline_assertions[name]
            after = candidate_assertions[name]

            if before.passed and not after.passed:

                findings.append(
                    RegressionFinding(
                        finding_type="new_assertion_failure",
                        key=name,
                        severity="critical",
                        message=(
                            f"Previously passing check "
                            f"now fails: {name}"
                        ),
                        baseline="PASS",
                        candidate="FAIL",
                    )
                )

            elif not before.passed and after.passed:

                findings.append(
                    RegressionFinding(
                        finding_type="resolved_assertion_failure",
                        key=name,
                        severity="info",
                        message=(
                            f"Previously failing check "
                            f"now passes: {name}"
                        ),
                        baseline="FAIL",
                        candidate="PASS",
                    )
                )

        return findings

    def _tool_counts(
        self,
        result: RunResult,
    ) -> Counter:

        return Counter(
            event.tool_name
            for event in result.traces
            if (
                event.event_type == "tool_started"
                and event.tool_name is not None
            )
        )

    def _compare_tool_calls(
        self,
        baseline: RunResult,
        candidate: RunResult,
    ) -> list[RegressionFinding]:

        findings = []

        before = self._tool_counts(baseline)
        after = self._tool_counts(candidate)

        all_tools = set(before) | set(after)

        for tool in sorted(all_tools):

            before_count = before.get(tool, 0)
            after_count = after.get(tool, 0)

            if before_count == after_count:
                continue

            if before_count == 0:

                findings.append(
                    RegressionFinding(
                        finding_type="new_tool_call",
                        key=tool,
                        severity="critical",
                        message=(
                            f"Candidate introduced new "
                            f"tool behavior: {tool}"
                        ),
                        baseline=0,
                        candidate=after_count,
                    )
                )

                continue

            if after_count == 0:

                findings.append(
                    RegressionFinding(
                        finding_type="removed_tool_call",
                        key=tool,
                        severity="warning",
                        message=(
                            f"Candidate no longer calls "
                            f"{tool}"
                        ),
                        baseline=before_count,
                        candidate=0,
                    )
                )

                continue

            findings.append(
                RegressionFinding(
                    finding_type="tool_call_count_changed",
                    key=tool,
                    severity="error",
                    message=(
                        f"Tool call count changed "
                        f"for {tool}: "
                        f"{before_count} -> {after_count}"
                    ),
                    baseline=before_count,
                    candidate=after_count,
                )
            )

        return findings

    def _mutation_map(
        self,
        result: RunResult,
    ) -> dict:

        return {
            mutation.path: mutation
            for mutation in result.mutations
        }

    def _compare_mutations(
        self,
        baseline: RunResult,
        candidate: RunResult,
    ) -> list[RegressionFinding]:

        findings = []

        before = self._mutation_map(baseline)
        after = self._mutation_map(candidate)

        all_paths = set(before) | set(after)

        for path in sorted(all_paths):

            old = before.get(path)
            new = after.get(path)

            if old is None and new is not None:

                findings.append(
                    RegressionFinding(
                        finding_type="new_state_mutation",
                        key=path,
                        severity="critical",
                        message=(
                            f"Candidate introduced a new "
                            f"state mutation: {path}"
                        ),
                        baseline=None,
                        candidate={
                            "type": new.mutation_type,
                            "before": new.before,
                            "after": new.after,
                        },
                    )
                )

                continue

            if old is not None and new is None:

                findings.append(
                    RegressionFinding(
                        finding_type="removed_state_mutation",
                        key=path,
                        severity="info",
                        message=(
                            f"Candidate no longer mutates "
                            f"{path}"
                        ),
                        baseline={
                            "type": old.mutation_type,
                            "before": old.before,
                            "after": old.after,
                        },
                        candidate=None,
                    )
                )

                continue

            if (
                old.mutation_type != new.mutation_type
                or old.before != new.before
                or old.after != new.after
            ):

                findings.append(
                    RegressionFinding(
                        finding_type="state_mutation_changed",
                        key=path,
                        severity="error",
                        message=(
                            f"State mutation changed "
                            f"for {path}"
                        ),
                        baseline={
                            "type": old.mutation_type,
                            "before": old.before,
                            "after": old.after,
                        },
                        candidate={
                            "type": new.mutation_type,
                            "before": new.before,
                            "after": new.after,
                        },
                    )
                )

        return findings