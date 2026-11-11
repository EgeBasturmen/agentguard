"""Execute only current approved frozen members through the existing integration engine/gate."""
from pathlib import Path
from pydantic import Field,computed_field
from agentguard.core.manifest import digest
from agentguard.core.result import RunResult
from agentguard.core.outcome import effective_pass
from agentguard.core.scenario import Scenario
from agentguard.integrations.plan import IntegrationPlan,run_integration_plan
from agentguard.generation.models import Model
from agentguard.generation.repository import Snapshot
from agentguard.generation.evidence import ApprovalEvidence

class CaseExecution(Model):
    id: str
    revision: int
    result: RunResult|None=None
    admission: str='blocked'
    reason: str|None=None

class SuiteExecution(Model):
    schema_version: int=1
    snapshot: Snapshot
    cases: list[CaseExecution]
    coverage_gaps: list[str]=Field(default_factory=list)
    admission_issues: list[str]=Field(default_factory=list)
    @computed_field
    @property
    def passed(self)->bool:
        expected={(row['id'],row['revision']) for row in self.snapshot.cases}
        actual=[(row.id,row.revision) for row in self.cases]
        for row in self.cases:
            pin=next((pin for pin in self.snapshot.cases if pin['id']==row.id and pin['revision']==row.revision),None)
            if row.result is None or pin is None:return False
            reference=row.result.generation_ref or {};receipt=row.result.approval_evidence or {}
            if reference.get('case_id')!=row.id or reference.get('revision')!=row.revision or reference.get('candidate_fingerprint')!=pin['fingerprint'] or receipt.get('suite_fingerprint')!=self.snapshot.fingerprint or receipt.get('review_fingerprint')!=pin['review_fingerprint']:return False
        return not self.coverage_gaps and not self.admission_issues and bool(expected) and len(actual)==len(set(actual)) and set(actual)==expected and all(row.admission=='approved' and row.result is not None and effective_pass(row.result) for row in self.cases)
    @computed_field
    @property
    def gate_decision(self)->str:return 'ALLOW' if self.passed else 'BLOCK'

class SuiteRunner:
    def run(self,repository,snapshot,*,base_dir,cancel_event=None):
        snapshot=Snapshot.model_validate(snapshot.model_dump())
        executions=[]
        for pin in snapshot.cases:
            entry=CaseExecution(id=pin['id'],revision=pin['revision'])
            executions.append(entry)
            try:
                if cancel_event is not None and cancel_event.is_set():raise ValueError('Suite cancelled; unexecuted cases are not passing')
                case,review=repository.admission(snapshot,pin)
                raw=case.plan()
                scenario=Scenario.model_validate(raw['scenario'])
                reference=dict(case_id=case.id,revision=case.revision,candidate_fingerprint=case.fingerprint,scenario_fingerprint=digest(scenario.model_dump(mode='json',exclude={'generation'})))
                raw['scenario']={**raw['scenario'],'generation':reference}
                result=run_integration_plan(IntegrationPlan.model_validate(raw),base_dir=Path(base_dir),cancel_event=cancel_event,expected_contracts=[s.content['contract'] for s in case.sources if s.kind=='contract'])
                entry.result=result
                # Recheck after external execution: a concurrent edit/retirement cannot retain admission.
                repository.admission(snapshot,pin)
                if case.axis=='edge' and case.observations.get('arguments') is not None:
                    from agentguard.core.paths import values_equal
                    if not any(event.tool_name==case.observations.get('tool') and event.status=='completed' and values_equal(event.arguments,case.observations['arguments']) for event in result.audit):
                        raise ValueError('Declared boundary stimulus was not observed in controller audit')
                receipt=ApprovalEvidence(**reference,suite_fingerprint=snapshot.fingerprint,review_fingerprint=review['fingerprint'],
                    comparison_fingerprint=result.comparison_manifest.fingerprint,
                    sources_fingerprint=digest(sorted([[source.id,source.fingerprint] for source in case.sources])))
                result.generation_ref=reference;result.approval_evidence=receipt.model_dump();result.admission_status='approved'
                entry.admission='approved'
            except Exception as exc:
                entry.reason=f'{type(exc).__name__}: {exc}'
                if entry.result is not None:entry.result.admission_status='stale'
        report=SuiteExecution(snapshot=snapshot,cases=executions)
        try:repository.check_snapshot(snapshot)
        except Exception as exc:report.admission_issues.append('Final admission fence: '+str(exc))
        if snapshot.requirements:
            from agentguard.generation.coverage import coverage
            candidates=[]
            for pin in snapshot.cases:
                try: candidates.append(repository.get(pin['id'],pin['revision'])[0])
                except Exception: pass
            matrix=coverage(candidates,snapshot.requirements,repository=repository,execution=report)
            report.coverage_gaps=matrix['missing_executed']
        return report
