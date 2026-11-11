"""Finite paired observations over the existing suite runner and comparator."""
import math
import uuid
from datetime import datetime,timezone,timedelta
from typing import Literal
from pydantic import Field,model_validator,model_serializer
from agentguard.generation.models import Model
from agentguard.generation.repository import Snapshot
from agentguard.generation.execution import SuiteRunner
from agentguard.regression.baseline import checked_execution
from agentguard.regression.comparator import RegressionComparator
from agentguard.core.manifest import digest,canonical_json
from agentguard.core.outcome import effective_pass


class Quarantine(Model):
    case_id: str=Field(min_length=1,max_length=100)
    owner: str=Field(min_length=1,max_length=200)
    reason: str=Field(min_length=1,max_length=2048)
    expires_at: str
    @model_validator(mode='after')
    def valid(self):
        if not self.owner.strip() or not self.reason.strip():raise ValueError('Quarantine owner and reason required')
        if datetime.fromisoformat(self.expires_at).utcoffset()!=timedelta(0):raise ValueError('UTC quarantine expiry required')
        return self


class RepeatPolicy(Model):
    schema_version: int=1
    mode: Literal['deterministic','stochastic']='deterministic'
    minimum_stochastic_samples: int=Field(default=3,ge=3,le=20)
    infrastructure_retries: int=Field(default=0,ge=0,le=2)
    tool_count_tolerance: dict[str,int]=Field(default_factory=dict)
    critical_cases: list[str]|None=Field(default=None,max_length=200)
    quarantines: list[Quarantine]=Field(default_factory=list,max_length=200)
    @model_validator(mode='after')
    def valid(self):
        if len(self.tool_count_tolerance)>200 or any(not 1<=len(key)<=200 for key in self.tool_count_tolerance):raise ValueError('Bounded tool tolerance keys required')
        if any(type(n) is not int or not 0<=n<=100 for n in self.tool_count_tolerance.values()):raise ValueError('Bounded integer tool count tolerance required')
        critical=self.critical_cases or []
        if len(critical)!=len(set(critical)) or len({q.case_id for q in self.quarantines})!=len(self.quarantines):raise ValueError('Duplicate policy member')
        return self


class CollectionReceipt(Model):
    schema_version: int=1
    collector: Literal['suite-runner-v1']='suite-runner-v1'
    collection_id: str=Field(pattern=r'^[0-9a-f]{32}$')
    execution_digest: str=Field(pattern=r'^[0-9a-f]{64}$')


class RepeatBatch(Model):
    schema_version: int=1
    snapshot: Snapshot
    policy: RepeatPolicy=Field(default_factory=RepeatPolicy)
    expected_attempts: int=Field(ge=1,le=60)
    planned_attempts: int|None=Field(default=None,ge=1,le=20)
    attempts: list[dict]=Field(min_length=1,max_length=60)
    selection: dict|None=None
    fingerprint: str|None=None
    @model_serializer(mode='wrap')
    def compatible(self,handler):
        data=handler(self)
        if self.selection is None:data.pop('selection',None)
        if self.planned_attempts is None:data.pop('planned_attempts',None)
        return data
    @model_validator(mode='after')
    def seal(self):
        if len(self.attempts)!=self.expected_attempts:raise ValueError('Missing or additional attempts')
        if self.selection is not None:
            from agentguard.regression.profiles import ProfileSelection
            selection=ProfileSelection.model_validate(self.selection)
            if selection.snapshot!=self.snapshot or selection.execution_policy()!=self.policy:raise ValueError('Profile scope/policy changed')
        ids={pin['id'] for pin in self.snapshot.cases}
        if set(self.policy.critical_cases or [])-ids or {q.case_id for q in self.policy.quarantines}-ids:raise ValueError('Unknown policy case')
        hashes={};collections=set();run_ids=set();independent=0;retry_chain=0
        for index,row in enumerate(self.attempts,1):
            if not {'index','execution'}<=set(row) or set(row)-{'index','execution','retry_of','receipt'} or type(row['index']) is not int or row['index']!=index:raise ValueError('Attempt ordering mismatch')
            if 'retry_of' in row:
                retry_chain+=1
                if type(row['retry_of']) is not int or row['retry_of']!=index-1 or index==1 or retry_chain>self.policy.infrastructure_retries or not retryable(checked_execution(self.attempts[index-2]['execution'])):raise ValueError('Retry is not an authorized pre-execution provision failure')
            else:independent+=1;retry_chain=0
            execution=checked_execution(row['execution'])
            if execution.snapshot!=self.snapshot:raise ValueError('Attempt suite changed')
            value=digest(row['execution'])
            receipt=CollectionReceipt.model_validate(row['receipt']) if 'receipt' in row else None
            if receipt is not None:
                if receipt.execution_digest!=value or receipt.collection_id in collections:raise ValueError('Collection receipt is copied or contradicts execution')
                collections.add(receipt.collection_id)
            if value in hashes and (receipt is None or not hashes[value]):raise ValueError('Copied legacy evidence is not an independent attempt')
            hashes[value]=receipt is not None and hashes.get(value,True)
            for member in execution.cases:
                if member.result and member.result.run_manifest:
                    run_id=member.result.run_manifest.run_id
                    if run_id in run_ids:raise ValueError('Repeated controlled run identity')
                    run_ids.add(run_id)
        if independent!=(self.planned_attempts if self.planned_attempts is not None else self.expected_attempts):raise ValueError('Independent sample count mismatch')
        value=digest(self.model_dump(mode='json',exclude={'fingerprint'}))
        if len(canonical_json(self.model_dump(mode='json')).encode())>16*1024*1024:raise ValueError('Repeat evidence exceeds 16 MiB')
        if self.fingerprint is not None and self.fingerprint!=value:raise ValueError('Repeat evidence changed')
        self.fingerprint=value
        return self

    def checked(self):return type(self).model_validate_json(self.model_dump_json())


class RepeatRunner:
    def run(self,repository,snapshot,*,base_dir,count,policy=None,allow_external=False,allow_live_models=False,cancel_event=None,selection=None):
        if type(count) is not int or not 1<=count<=20:raise ValueError('Repeat count must be 1..20')
        policy=RepeatPolicy.model_validate((policy or RepeatPolicy()).model_dump(mode='json'))
        if selection is not None:
            from agentguard.regression.profiles import ProfileSelection
            selection=ProfileSelection.model_validate(selection.model_dump(mode='json'))
            if selection.snapshot!=snapshot or selection.execution_policy()!=policy:raise ValueError('Profile mismatch before execution')
        repository.check_snapshot(snapshot)
        ids={pin['id'] for pin in snapshot.cases}
        if set(policy.critical_cases or [])-ids or {q.case_id for q in policy.quarantines}-ids:raise ValueError('Unknown policy case before execution')
        for pin in snapshot.cases:
            runtime=repository.admission(snapshot,pin)[0].runtime
            external=runtime.agent.transport=='http' or runtime.environment.kind=='http' or any(tool.endpoint is not None for tool in runtime.tools)
            if external and not allow_external:raise ValueError('Repeated external effects require explicit operator authorization')
            if runtime.model and runtime.model.mode=='live' and not allow_live_models:raise ValueError('Live model repeats require separate operator authorization')
        attempts=[]
        for _ in range(count):
            for retry in range(policy.infrastructure_retries+1):
                collection_id=uuid.uuid4().hex
                execution=SuiteRunner().run(repository,snapshot,base_dir=base_dir,cancel_event=cancel_event)
                row=dict(index=len(attempts)+1,execution=execution.model_dump(mode='json'))
                row['receipt']=CollectionReceipt(collection_id=collection_id,execution_digest=digest(row['execution'])).model_dump(mode='json')
                if retry:row['retry_of']=len(attempts)
                attempts.append(row)
                if not retryable(execution):break
        return RepeatBatch(snapshot=snapshot,policy=policy,expected_attempts=len(attempts),planned_attempts=count,
            attempts=attempts,selection=selection.model_dump(mode='json') if selection else None)


def retryable(execution):
    """Only clean failures before any worker starts; no model/behavior/timeout retries."""
    errors=[row.result for row in execution.cases if row.result is not None and row.result.status=='error']
    return bool(errors) and len(errors)==len(execution.cases) and all(run.error_stage=='provision' and run.cleanup_status in {'verified','not_required'}
        and not run.audit and not (run.run_manifest and run.run_manifest.started)
        and not (run.model_evidence and run.model_evidence.get('calls')) for run in errors)


def wilson(passes,total):
    if total==0:return [0.0,1.0]
    z=1.959963984540054;p=passes/total;denominator=1+z*z/total
    centre=(p+z*z/(2*total))/denominator
    width=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denominator
    return [max(0.,centre-width),min(1.,centre+width)]


def apply_tolerance(comparison,policy,*,critical):
    before=comparison.baseline_run;after=comparison.candidate_run
    if critical or not effective_pass(before) or not effective_pass(after):return comparison
    if not all(run.execution_context and run.execution_context.capabilities.supports_tool_observation for run in (before,after)):return comparison
    findings=[]
    for finding in comparison.findings:
        tolerance=policy.tool_count_tolerance.get(finding.key,0)
        if finding.finding_type=='tool_call_count_changed' and abs(finding.candidate-finding.baseline)<=tolerance:
            finding=finding.model_copy(update={'severity':'info','message':'Tool count change is within explicit noncritical tolerance.'})
        findings.append(finding)
    return comparison.model_copy(update={'findings':findings})


def _assessment(baseline,candidate,now):
    baseline=baseline.checked();candidate=candidate.checked()
    reasons=[];coverage=[];comparisons=[];statistics=[]
    if baseline.snapshot!=candidate.snapshot:reasons.append('incompatible_suite')
    if baseline.policy!=candidate.policy:reasons.append('incompatible_policy')
    if baseline.selection!=candidate.selection:reasons.append('incompatible_profile')
    old_samples=baseline.planned_attempts or baseline.expected_attempts
    new_samples=candidate.planned_attempts or candidate.expected_attempts
    if old_samples!=new_samples:reasons.append('unpaired_attempts')
    policy=baseline.policy
    if policy.mode=='stochastic' and min(old_samples,new_samples)<policy.minimum_stochastic_samples:reasons.append('insufficient_samples')
    ids={pin['id'] for pin in baseline.snapshot.cases}
    critical=set(policy.critical_cases) if policy.critical_cases is not None else ids
    for quarantine in policy.quarantines:
        coverage.append(quarantine.case_id)
        expiry=datetime.fromisoformat(quarantine.expires_at)
        reasons.append('quarantine_expired' if expiry<=now else 'quarantine_expiry_exceeds_30_days' if expiry-now>timedelta(days=30) else 'quarantined_coverage')
    for identity in sorted(ids):
        old=[];new=[]
        for batch,rows in ((baseline,old),(candidate,new)):
            for attempt in batch.attempts:
                execution=checked_execution(attempt['execution'])
                member=next((case for case in execution.cases if case.id==identity),None)
                if 'retry_of' not in attempt:rows.append(member.result if member and member.admission=='approved' else None)
                if not execution.passed:reasons.append('unverified_or_failed_attempt')
        for index,(before,after) in enumerate(zip(old,new),1):
            if before is None or after is None:
                reasons.append('missing_case_evidence');continue
            comparison=RegressionComparator().compare('baseline',before,'candidate',after)
            comparison=apply_tolerance(comparison,policy,critical=identity in critical)
            if not comparison.passed:
                reasons.append('blocking_comparison')
                reasons.extend('regression:'+f.finding_type for f in comparison.findings if f.severity in {'error','critical'})
            comparisons.append(dict(case_id=identity,attempt=index,comparison=comparison.model_dump(mode='json')))
        p_old=sum(row is not None and effective_pass(row) for row in old)
        p_new=sum(row is not None and effective_pass(row) for row in new)
        statistics.append(dict(case_id=identity,baseline_samples=len(old),candidate_samples=len(new),
            baseline_attempts=len(baseline.attempts),candidate_attempts=len(candidate.attempts),
            baseline_pass_rate=p_old/len(old),candidate_pass_rate=p_new/len(new),
            baseline_interval_95=wilson(p_old,len(old)),candidate_interval_95=wilson(p_new,len(new)),
            flaky=len({row is not None and effective_pass(row) for row in new})>1))
    selection=baseline.selection
    return dict(passed=not reasons,gate_decision='BLOCK' if reasons else 'ALLOW',reasons=sorted(set(reasons)),
        coverage_gaps=sorted(set(coverage)),cases=statistics,comparisons=comparisons,
        profile=selection['profile']['name'] if selection else 'full',excluded_from_profile=selection['excluded_cases'] if selection else [],
        paired_attempts=min(old_samples,new_samples),
        assurance='Finite observations; Wilson intervals assume independent Bernoulli samples, not general model correctness.')


class RepeatComparison:
    def __init__(self,baseline,candidate,now=None):
        self.baseline=baseline.checked();self.candidate=candidate.checked()
        self.now=now or datetime.now(timezone.utc)
        if self.now.utcoffset()!=timedelta(0):raise ValueError('UTC comparison time required')
    def model_dump(self,**kwargs):return _assessment(self.baseline,self.candidate,self.now)
    @property
    def passed(self):return self.model_dump()['passed']
    @property
    def paired_attempts(self):return self.model_dump()['paired_attempts']
    @property
    def cases(self):return self.model_dump()['cases']
    @property
    def reasons(self):return self.model_dump()['reasons']
    @property
    def comparisons(self):return self.model_dump()['comparisons']
    @property
    def coverage_gaps(self):return self.model_dump()['coverage_gaps']


def compare_repeats(baseline,candidate,*,now=None):return RepeatComparison(baseline,candidate,now)
