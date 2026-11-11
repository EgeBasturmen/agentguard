import pytest

def test_unverified_cleanup_is_infrastructure_error(make_run):
    from agentguard.regression.gate import GateAPI
    run=make_run().model_copy(update={'cleanup_status':'unverified'})
    decision=GateAPI.evaluate(run)
    assert decision.gate_decision=='BLOCK' and decision.outcome=='ERROR'

def test_public_gate_rejects_unknown_schema_and_summary_as_authority(make_run):
    from agentguard.regression.gate import GateAPI
    from agentguard.reporting.models import RunReport
    report=RunReport.from_result(make_run(),'trusted-local').model_dump(mode='json')
    decision=GateAPI.evaluate(report)
    assert decision.passed and decision.exit_code==0
    report['schema_version']=99
    assert GateAPI.evaluate(report).exit_code==2
    assert not GateAPI.evaluate(decision.model_dump()).passed

def test_public_gate_recomputes_comparison_instead_of_trusting_findings():
    from agentguard.regression.gate import GateAPI
    from agentguard.regression.comparator import RegressionComparator
    from agentguard.runner.runner import ScenarioRunner
    from agentguard.core.scenario import Scenario
    from agentguard.sandbox.generic import GenericSandbox
    class Agent:
        def __init__(self,count):self.count=count
        def run(self,task,sandbox):
            sandbox.register_tool('read',lambda state:state['value'])
            for _ in range(self.count):sandbox.call_tool('read')
    scenario=Scenario(name='same',task='read',initial_state={'value':1},expected_state=[dict(path='value',value=1)])
    before=ScenarioRunner(GenericSandbox).run(scenario,Agent(1));after=ScenarioRunner(GenericSandbox).run(scenario,Agent(2))
    comparison=RegressionComparator().compare('old',before,'new',after)
    assert not comparison.passed
    comparison.findings=[]
    assert comparison.passed
    assert GateAPI.evaluate(comparison).gate_decision=='BLOCK'

@pytest.mark.integration
def test_release_gate_requires_active_baseline_and_pinned_repeat_policy(tmp_path):
    from phase3_service import Service
    from test_generated_execution import http_request
    from test_scenario_repository import approved
    from agentguard.generation.generator import generate
    from agentguard.generation.repository import ScenarioRepository
    from agentguard.regression.repeats import RepeatRunner,RepeatPolicy
    from agentguard.regression.baseline import BaselineRepository,checked_execution
    from agentguard.regression.gate import GateAPI
    with Service() as service:
        scenarios=ScenarioRepository(tmp_path/'scenarios.sqlite')
        case=generate(http_request(service)).cases[0];snapshot=approved(scenarios,case)
        args=dict(base_dir=tmp_path,count=1,policy=RepeatPolicy(),allow_external=True)
        old=RepeatRunner().run(scenarios,snapshot,**args);new=RepeatRunner().run(scenarios,snapshot,**args)
        repo=BaselineRepository(tmp_path/'baseline.sqlite');pins={case.id:'a'*64}
        record=repo.create('release',scenarios,checked_execution(old.attempts[0]['execution']),artifacts=pins,
            actor='op',reason='Initial reference',repeat_evidence=old.model_dump(mode='json'))
        repo.approve(record.fingerprint,scenarios=scenarios,actor='op',reason='Reviewed')
        repo.promote(record.fingerprint,scenarios=scenarios,actor='op',reason='Promote',expected_active=None)
        kwargs=dict(baseline_repository=repo,baseline_name='release',scenario_repository=scenarios,candidate_artifacts=pins)
        decision=GateAPI.evaluate(new,**kwargs)
        assert decision.passed and decision.authorizes_release
        from typer.testing import CliRunner
        from agentguard.cli.main import app
        import json
        evidence=tmp_path/'candidate.json';evidence.write_text(new.model_dump_json())
        artifacts=tmp_path/'artifacts.json';artifacts.write_text(json.dumps(pins))
        options=['release','gate',str(evidence),'--baseline-name','release','--baseline-db',str(repo.path),
            '--scenario-db',str(scenarios.path),'--artifacts',str(artifacts)]
        result=CliRunner().invoke(app,[*options,'--output',str(tmp_path/'allowed')])
        assert result.exit_code==0
        summary=json.loads(result.stdout)
        assert summary['gate_decision']==decision.gate_decision and summary['authorizes_release']
        repo.retire(record.fingerprint,actor='op',reason='Withdrawn')
        assert not GateAPI.evaluate(new,**kwargs).passed
        result=CliRunner().invoke(app,[*options,'--output',str(tmp_path/'retired')])
        assert result.exit_code==2 and json.loads(result.stdout)['gate_decision']=='BLOCK'
