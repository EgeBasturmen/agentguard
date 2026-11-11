"""AG-061: real fresh attempts, no cherry picking and conservative uncertainty."""
import pytest
from datetime import datetime,timezone,timedelta
from test_phase6_baseline import baseline_input

pytestmark=pytest.mark.integration

def batches(inputs,count=3,mode='deterministic',policy=None):
    from agentguard.regression.repeats import RepeatRunner,RepeatPolicy
    scenarios,execution,_=inputs
    policy=policy or RepeatPolicy(mode=mode)
    runner=RepeatRunner()
    args=dict(base_dir=scenarios.path.parent,count=count,policy=policy,allow_external=True)
    return runner.run(scenarios,execution.snapshot,**args),runner.run(scenarios,execution.snapshot,**args)

def test_repeat_runner_really_resets_and_pairs_every_attempt(baseline_input):
    from agentguard.regression.repeats import compare_repeats
    baseline,candidate=batches(baseline_input)
    result=compare_repeats(baseline,candidate)
    assert result.passed and result.paired_attempts==3
    assert result.cases[0]['candidate_pass_rate']==1.0
    assert result.cases[0]['candidate_interval_95'][0]<1.0
    assert len({row['execution']['cases'][0]['result']['traces'][0]['timestamp'] for row in candidate.attempts})==3

def test_failure_then_success_is_not_cherry_picked_away(baseline_input):
    from agentguard.regression.repeats import compare_repeats,RepeatBatch
    baseline,candidate=batches(baseline_input)
    data=candidate.model_dump(mode='json');data.pop('fingerprint')
    execution=data['attempts'][0]['execution'];run=execution['cases'][0]['result']
    run['status']='failed';run['passed']=False;run['assertions'][0]['passed']=False
    execution['passed']=False;execution['gate_decision']='BLOCK'
    candidate=RepeatBatch.model_validate(data)
    result=compare_repeats(baseline,candidate)
    assert not result.passed and result.cases[0]['candidate_pass_rate']==pytest.approx(2/3)
    assert len(result.comparisons)==3

def test_duplicate_evidence_is_not_three_independent_samples(baseline_input):
    from agentguard.regression.repeats import RepeatBatch
    baseline,_=batches(baseline_input,count=1)
    raw=baseline.model_dump(mode='json');raw.pop('fingerprint');raw['expected_attempts']=2
    raw['attempts'].append({'index':2,'execution':raw['attempts'][0]['execution']})
    with pytest.raises(ValueError):RepeatBatch.model_validate(raw)

def test_stochastic_small_sample_is_inconclusive_not_statistical_pass(baseline_input):
    from agentguard.regression.repeats import compare_repeats
    baseline,candidate=batches(baseline_input,count=1,mode='stochastic')
    result=compare_repeats(baseline,candidate)
    assert not result.passed and 'insufficient_samples' in result.reasons

def test_quarantine_requires_owner_expiry_and_never_hides_coverage(baseline_input):
    from agentguard.regression.repeats import RepeatPolicy,compare_repeats
    case_id=baseline_input[1].cases[0].id
    with pytest.raises(ValueError):RepeatPolicy(quarantines=[{'case_id':case_id,'owner':'','reason':'flaky','expires_at':'2099-01-01T00:00:00+00:00'}])
    policy=RepeatPolicy(quarantines=[dict(case_id=case_id,owner='team',reason='investigating',expires_at=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())])
    baseline,candidate=batches(baseline_input,count=1,policy=policy)
    result=compare_repeats(baseline,candidate)
    assert not result.passed and result.coverage_gaps==[case_id]

def test_default_repeat_execution_refuses_external_side_effects(baseline_input):
    from agentguard.regression.repeats import RepeatRunner,RepeatPolicy
    scenarios,execution,_=baseline_input
    with pytest.raises(ValueError):RepeatRunner().run(scenarios,execution.snapshot,base_dir=scenarios.path.parent,count=2,policy=RepeatPolicy())

def test_unknown_policy_case_is_rejected_before_any_execution(baseline_input,monkeypatch):
    import agentguard.regression.repeats as module
    scenarios,execution,_=baseline_input
    calls=[]
    def execute(*args,**kwargs):calls.append(True);return execution
    monkeypatch.setattr(module.SuiteRunner,'run',execute)
    with pytest.raises(ValueError):module.RepeatRunner().run(scenarios,execution.snapshot,base_dir=scenarios.path.parent,
        count=1,policy=module.RepeatPolicy(critical_cases=['unknown']),allow_external=True)
    assert calls==[]


def test_explicit_tool_tolerance_does_not_override_critical_or_hard_failure():
    from agentguard.regression.repeats import RepeatPolicy,apply_tolerance
    from agentguard.regression.comparator import RegressionComparator
    from agentguard.core.scenario import Scenario
    from agentguard.runner.runner import ScenarioRunner
    from agentguard.sandbox.generic import GenericSandbox
    scenario=Scenario(name='bounded-read',task='read',initial_state={'value':1},expected_state=[{'path':'value','value':1}],
        tool_expectations=[{'tool_name':'read','max_times':3}])
    class Agent:
        def __init__(self,count):self.count=count
        def run(self,task,sandbox):
            sandbox.register_tool('read',lambda state:state['value'])
            for _ in range(self.count):sandbox.call_tool('read')
    before=ScenarioRunner(GenericSandbox).run(scenario,Agent(1))
    after=ScenarioRunner(GenericSandbox).run(scenario,Agent(2))
    comparison=RegressionComparator().compare('old',before,'new',after)
    assert not comparison.passed
    policy=RepeatPolicy(tool_count_tolerance={'read':1})
    assert apply_tolerance(comparison,policy,critical=False).passed
    assert not apply_tolerance(comparison,policy,critical=True).passed
    after.cleanup_status='unverified'
    comparison=RegressionComparator().compare('old',before,'new',after)
    assert not apply_tolerance(comparison,policy,critical=False).passed


def test_repeated_baseline_freezes_all_attempts_and_policy(tmp_path,baseline_input):
    from agentguard.regression.baseline import BaselineRepository
    from agentguard.regression.baseline import checked_execution
    baseline,_=batches(baseline_input)
    scenarios,_,artifacts=baseline_input
    repo=BaselineRepository(tmp_path/'baseline.sqlite')
    frozen=repo.create('release',scenarios,checked_execution(baseline.attempts[0]['execution']),
        artifacts=artifacts,actor='op',reason='Repeated verified observations',repeat_evidence=baseline.model_dump(mode='json'))
    assert frozen.repeat_evidence['fingerprint']==baseline.fingerprint
    frozen.repeat_evidence['policy']['tool_count_tolerance']={'anything':100}
    assert repo.get(frozen.fingerprint).repeat_evidence['policy']['tool_count_tolerance']=={}


def test_explicit_provision_retry_preserves_error_and_does_not_inflate_samples(baseline_input,monkeypatch):
    import agentguard.regression.repeats as module
    scenarios,execution,_=baseline_input
    real=module.SuiteRunner.run;calls=[0]
    def provisional(self,*args,**kwargs):
        calls[0]+=1
        if calls[0]==1:
            result=execution.model_copy(deep=True)
            run=result.cases[0].result
            run.status='error';run.passed=False;run.error_stage='provision';run.error_type='ProvisionUnavailable'
            run.cleanup_status='verified'
            return result
        return real(self,*args,**kwargs)
    monkeypatch.setattr(module.SuiteRunner,'run',provisional)
    batch=module.RepeatRunner().run(scenarios,execution.snapshot,base_dir=scenarios.path.parent,count=2,
        policy=module.RepeatPolicy(infrastructure_retries=1),allow_external=True)
    assert len(batch.attempts)==3 and batch.attempts[1]['retry_of']==1
    assert batch.planned_attempts==2
    result=module.compare_repeats(batch,batch)
    assert not result.passed and result.cases[0]['candidate_samples']==2
    assert result.cases[0]['candidate_attempts']==3

def test_provision_retry_never_reexecutes_completed_suite_members():
    from types import SimpleNamespace as N
    from agentguard.regression.repeats import retryable
    failed=N(status='error',error_stage='provision',cleanup_status='verified',audit=[],run_manifest=None,model_evidence=None)
    completed=N(status='passed')
    assert not retryable(N(cases=[N(result=failed),N(result=completed)]))
