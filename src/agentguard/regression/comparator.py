from collections import Counter

from agentguard.core.result import RunResult
from agentguard.regression.models import (
    RegressionFinding,
    RegressionResult,
)
from agentguard.core.paths import values_equal
from agentguard.core.outcome import evidence_errors

class RegressionComparator:

    def compare(
        self,
        baseline_name: str,
        baseline: RunResult,
        candidate_name: str,
        candidate: RunResult,
    ) -> RegressionResult:

        baseline = baseline.validated_copy()
        candidate = candidate.validated_copy()
        findings: list[RegressionFinding] = []
        integrity = self._validate_run(baseline, "baseline") + self._validate_run(candidate, "candidate")
        findings.extend(integrity)
        comparison_status = "verified"
        if baseline.model_evidence and candidate.model_evidence:
            old=baseline.model_evidence.get('config',{});new=candidate.model_evidence.get('config',{})
            if old.get('mode')!=new.get('mode'):
                finding=RegressionFinding(finding_type='model_mode_changed',key='model:mode',severity='critical',message='Mock/live model evidence is not interchangeable',baseline=old.get('mode'),candidate=new.get('mode'))
                integrity.append(finding);findings.append(finding)
            elif any(old.get(key)!=new.get(key) for key in ('provider','model','prompt_version','system_prompt')):
                findings.append(RegressionFinding(finding_type='model_configuration_changed',key='model:config',severity='info',message='Pinned model/provider/prompt configuration changed; checks remain independently defined',baseline={key:old.get(key) for key in ('provider','model','prompt_version')},candidate={key:new.get(key) for key in ('provider','model','prompt_version')}))
        if baseline.scenario_name != candidate.scenario_name:
            comparison_status = "incompatible"
        elif baseline.comparison_manifest is None or candidate.comparison_manifest is None:
            comparison_status = "unverified"
        elif baseline.comparison_manifest.fingerprint != candidate.comparison_manifest.fingerprint:
            comparison_status = "incompatible"
        elif (
            baseline.execution_context.model_dump(exclude={"limitations"}) if baseline.execution_context else None
        ) != (
            candidate.execution_context.model_dump(exclude={"limitations"}) if candidate.execution_context else None
        ):
            comparison_status = "incompatible"
        elif integrity or baseline.evaluation_status != "complete" or candidate.evaluation_status != "complete":
            comparison_status = "unverified"
        if comparison_status != "verified":
            findings.append(RegressionFinding(
                finding_type="incompatible_runs" if comparison_status == "incompatible" else "comparison_not_verified",
                key="run:comparison", severity="critical",
                message="Runs have incompatible scenario/environment/evaluator fingerprints."
                if comparison_status == "incompatible" else "Comparison identity or evidence cannot be verified; rerun with a current manifest.",
                baseline=baseline.comparison_manifest.fingerprint if baseline.comparison_manifest else None,
                candidate=candidate.comparison_manifest.fingerprint if candidate.comparison_manifest else None,
            ))

        if candidate.cleanup_status == "unverified":
            findings.append(
                RegressionFinding(
                    finding_type="candidate_run_not_verified",
                    key="run:cleanup",
                    severity="critical",
                    message=(
                        "Candidate cleanup could not be verified. "
                        "The run cannot be accepted."
                    ),
                    baseline=baseline.cleanup_status,
                    candidate=candidate.cleanup_status,
                )
            )

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


        return RegressionResult(
            baseline_name=baseline_name,
            candidate_name=candidate_name,
            baseline_run=baseline,
            candidate_run=candidate,
            comparison_status=comparison_status,
            findings=findings,
        )

    def _validate_run(self, run: RunResult, label: str) -> list[RegressionFinding]:
        problems = evidence_errors(run)
        return [RegressionFinding(
            finding_type="invalid_run_evidence", key=f"{label}:evidence", severity="critical",
            message=f"{label}: {problem}",
        ) for problem in problems]

    def _compare_assertions(
        self,
        baseline: RunResult,
        candidate: RunResult,
    ) -> list[RegressionFinding]:

        findings = []

        baseline_assertions = {
            assertion.assertion_id or assertion.name: assertion
            for assertion in baseline.assertions
            if assertion.assertion_id is not None or assertion.name != "agent:execution"
        }

        candidate_assertions = {
            assertion.assertion_id or assertion.name: assertion
            for assertion in candidate.assertions
            if assertion.assertion_id is not None or assertion.name != "agent:execution"
        }

        common_names = (
            baseline_assertions.keys()
            & candidate_assertions.keys()
        )

        for identity in sorted(baseline_assertions.keys() - candidate_assertions.keys()):
            check = baseline_assertions[identity]
            findings.append(RegressionFinding(
                finding_type="removed_assertion", key=check.name, severity="critical",
                message=f"Candidate removed evaluation check: {check.name}",
                baseline=identity, candidate=None,
            ))
        for identity in sorted(candidate_assertions.keys() - baseline_assertions.keys()):
            check = candidate_assertions[identity]
            findings.append(RegressionFinding(
                finding_type="added_assertion", key=check.name, severity="info",
                message=f"Candidate added evaluation check: {check.name}",
                baseline=None, candidate=identity,
            ))

        for identity in sorted(common_names):

            before = baseline_assertions[identity]
            after = candidate_assertions[identity]
            name = after.name

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
                or not values_equal(old.before, new.before)
                or not values_equal(old.after, new.after)
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
