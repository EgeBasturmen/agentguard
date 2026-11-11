"""Single public gate service. Presentation summaries are never release authority."""
import re
from typing import Literal
from pydantic import Field,computed_field
from agentguard.generation.models import Model
from agentguard.core.result import RunResult
from agentguard.core.outcome import effective_pass,evidence_errors
from agentguard.core.manifest import digest
from agentguard.generation.execution import SuiteExecution
from agentguard.regression.baseline import checked_execution
from agentguard.regression.models import ComparisonResult
from agentguard.regression.comparator import RegressionComparator
from agentguard.regression.repeats import RepeatBatch,RepeatPolicy,RepeatComparison,compare_repeats
from agentguard.reporting.models import RunReport,validate_report


class GateDecision(Model):
    schema_version: int=1
    kind: Literal['agentguard.gate-decision']='agentguard.gate-decision'
    decision_status: Literal['verified','unverified']='verified'
    reasons: list[str]=Field(default_factory=list)
    cases: list[dict]=Field(default_factory=list)
    coverage_gaps: list[str]=Field(default_factory=list)
    paired_attempts: int=0
    profile: str='full'
    excluded_from_profile: list[str]=Field(default_factory=list)
    assurance: str='Finite evidence only; hashes are not signatures.'
    authorizes_release: bool=False
    infrastructure_error: bool=False
    @computed_field
    @property
    def passed(self)->bool:return self.decision_status=='verified' and not self.reasons
    @computed_field
    @property
    def gate_decision(self)->str:return 'ALLOW' if self.passed else 'BLOCK'
    @computed_field
    @property
    def exit_code(self)->int:return 2 if self.decision_status=='unverified' else 0 if self.passed else 1
    @computed_field
    @property
    def outcome(self)->str:
        if self.passed:return 'PASS'
        if self.decision_status=='unverified' or self.infrastructure_error:return 'ERROR'
        if any(reason.startswith('quarantin') or reason=='insufficient_samples' for reason in self.reasons):return 'INCONCLUSIVE'
        return 'FAIL'


def artifact_pins(batch,declared=None):
    expected={p['id'] for p in batch.snapshot.cases};pins={}
    if declared is not None and (set(declared)!=expected or any(not isinstance(v,str) or not re.fullmatch(r'[0-9a-f]{64}',v) for v in declared.values())):raise ValueError('Invalid declared artifact pins')
    for identity in expected:
        observed=set();unobserved=False
        for attempt in batch.attempts:
            execution=checked_execution(attempt['execution'])
            member=next((r for r in execution.cases if r.id==identity),None)
            if member is None or member.result is None:raise ValueError('Missing case artifact observation')
            manifest=member.result.run_manifest
            if manifest is None:unobserved=True
            else:observed.add(manifest.source_digest)
        if observed:
            if unobserved or len(observed)!=1 or not re.fullmatch(r'[0-9a-f]{64}',next(iter(observed))):raise ValueError('Candidate artifact changed across attempts')
            value=next(iter(observed))
            if declared is not None and declared[identity]!=value:raise ValueError('Declared artifact contradicts observation')
            pins[identity]=value
        elif declared is not None:pins[identity]=declared[identity]
        else:raise ValueError('Observed-only agent needs explicit declared version identity')
    return pins


def infrastructure_problem(run):
    return run is None or bool(evidence_errors(run)) or run.cleanup_status=='unverified' or run.evaluation_status!='complete' or (run.status=='error' and run.policy_status!='violation')


class GateAPI:
    @staticmethod
    def evaluate(value,*,baseline_repository=None,baseline_name=None,scenario_repository=None,candidate_artifacts=None):
        try:
            if isinstance(value,dict):
                if value.get('kind') in {'agentguard.gate-decision','agentguard.release-summary','agentguard.repro'}:raise ValueError('Summary/export is not authority')
                if 'agent' in value and 'result' in value:value=validate_report(value).result
                elif 'attempts' in value:value=RepeatBatch.model_validate(value)
                elif 'snapshot' in value and 'cases' in value:value=checked_execution(value)
                else:raise ValueError('Unknown evidence type')
            if isinstance(value,RunReport):value=validate_report(value).result
            if isinstance(value,RunResult):
                run=value.validated_copy();passed=effective_pass(run)
                return GateDecision(reasons=[] if passed else ['run_not_verified'],infrastructure_error=infrastructure_problem(run),
                    cases=[dict(case_id=digest(run.scenario_name),candidate_pass_rate=float(passed),candidate_samples=1)])
            if isinstance(value,SuiteExecution):
                run=checked_execution(value)
                return GateDecision(reasons=[] if run.passed else ['suite_not_verified'],coverage_gaps=run.coverage_gaps,
                    infrastructure_error=any(infrastructure_problem(row.result) for row in run.cases),
                    cases=[dict(case_id=row.id,candidate_pass_rate=float(row.result is not None and effective_pass(row.result)),candidate_samples=1) for row in run.cases])
            if isinstance(value,ComparisonResult):
                checked=RegressionComparator().compare(value.baseline_name,value.baseline_run,value.candidate_name,value.candidate_run)
                return GateDecision(reasons=[] if checked.passed else ['blocking_comparison'],
                    infrastructure_error=checked.comparison_status!='verified' or infrastructure_problem(checked.candidate_run),
                    cases=[dict(case_id=digest(checked.candidate_run.scenario_name),candidate_pass_rate=float(checked.passed),candidate_samples=1)])
            authorized=False
            if isinstance(value,RepeatBatch):
                if baseline_repository is None or scenario_repository is None or baseline_name is None:raise ValueError('Active baseline required')
                frozen=baseline_repository.active(baseline_name)
                scenario_repository.check_snapshot(frozen.snapshot)
                scenario_repository.check_snapshot(value.snapshot)
                artifact_pins(value,candidate_artifacts)
                baseline=RepeatBatch.model_validate(frozen.repeat_evidence) if frozen.repeat_evidence is not None else RepeatBatch(
                    snapshot=frozen.snapshot,policy=RepeatPolicy(),expected_attempts=1,attempts=[dict(index=1,execution=frozen.execution)])
                value=compare_repeats(baseline,value);authorized=True
            if isinstance(value,RepeatComparison):
                raw=value.model_dump()
                fields={key:raw[key] for key in ('reasons','cases','coverage_gaps','paired_attempts','profile','excluded_from_profile','assurance')}
                error=False
                for attempt in value.candidate.attempts:
                    for member in checked_execution(attempt['execution']).cases:
                        result=member.result
                        if infrastructure_problem(result):error=True
                return GateDecision(**fields,authorizes_release=authorized and raw['passed'],infrastructure_error=error)
            raise ValueError('Unsupported evidence input')
        except Exception:
            return GateDecision(decision_status='unverified',reasons=['invalid_or_incompatible_evidence'])
