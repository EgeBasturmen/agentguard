"""Phase 5 regression contracts: missing/ambiguous evidence never becomes PASS."""
import pytest


def test_output_only_scenario_is_evaluated_and_false_claim_is_not_truth():
    from agentguard.core.scenario import Scenario
    from agentguard.runner.runner import ScenarioRunner
    from agentguard.sandbox.generic import GenericSandbox
    from agentguard.core.outcome import effective_pass
    class Agent:
        def run(self,task,sandbox):return '{"status":"wrong","passed":true}'
    spec=Scenario.model_validate(dict(name='output',task='Return ready',initial_state={},
        evaluations=[dict(id='format',kind='output_json',config={'json_schema':{'type':'object','properties':{'status':{'const':'ready'}},'required':['status']}})]))
    result=ScenarioRunner(GenericSandbox).run(spec,Agent())
    assert not effective_pass(result) and result.evaluations[0].status=='fail'


def test_missing_temporal_observation_is_inconclusive():
    from agentguard.evaluation.registry import evaluate
    from agentguard.evaluation.specs import EvaluationSpec
    finding=evaluate(EvaluationSpec(id='order',kind='temporal_before',config={'before':'approve','after':'write'}),{})
    assert finding.status=='inconclusive'


def test_unregistered_evaluator_cannot_be_loaded_as_host_code():
    from agentguard.evaluation.specs import EvaluationSpec
    with pytest.raises(ValueError):EvaluationSpec(id='custom',kind='some.module:Code',config={})


def test_rag_citation_does_not_prove_grounding_when_quote_is_invented():
    from agentguard.evaluation.registry import evaluate
    from agentguard.evaluation.specs import EvaluationSpec
    finding=evaluate(EvaluationSpec(id='sources',kind='rag_sources',config={'required_documents':['doc1']}),
        {'output':'{"answer":"invented","citations":[{"document_id":"doc1","quote":"invented"}]}',
         'before':{'documents':{'doc1':{'content':'Known factual material'}}},'audit':[],'authoritative':True})
    assert finding.status!='pass'


def test_uncalibrated_judge_does_not_pass():
    from agentguard.evaluation.registry import evaluate
    from agentguard.evaluation.specs import EvaluationSpec
    finding=evaluate(EvaluationSpec(id='judge',kind='judge',config={'rubric':'Assess correctness','rubric_version':'1','labels':[]}),{'output':'PASS'})
    assert finding.status=='inconclusive'


def test_missing_output_is_inconclusive_not_a_schema_success():
    from agentguard.evaluation.registry import evaluate
    from agentguard.evaluation.specs import EvaluationSpec
    finding=evaluate(EvaluationSpec(id='format',kind='output_json',config={'json_schema':{'type':'object'}}),{})
    assert finding.status=='inconclusive'


def test_temporary_mutation_is_detected_even_when_final_state_is_restored():
    from agentguard.evaluation.registry import evaluate
    from agentguard.evaluation.specs import EvaluationSpec
    from agentguard.evaluation.diff import diff_states
    from agentguard.execution.records import AuditEvent
    events=[AuditEvent(sequence=1,tool_name='write',arguments={},status='completed',mutations=diff_states({'safe':0},{'safe':1})),
        AuditEvent(sequence=2,tool_name='write',arguments={},status='completed',mutations=diff_states({'safe':1},{'safe':0}))]
    finding=evaluate(EvaluationSpec(id='never',kind='temporal_never',config={'path':'safe'}),{'audit':events,'authoritative':True})
    assert finding.status=='fail'


def test_removing_evaluation_evidence_cannot_retain_pass():
    from agentguard.core.scenario import Scenario
    from agentguard.runner.runner import ScenarioRunner
    from agentguard.sandbox.generic import GenericSandbox
    from agentguard.core.outcome import effective_pass
    class Agent:
        def run(self,task,sandbox):return 'ready'
    scenario=Scenario(name='output',task='Return ready',initial_state={},evaluations=[dict(id='output',kind='output_equals',config={'text':'ready'})])
    result=ScenarioRunner(GenericSandbox).run(scenario,Agent())
    assert effective_pass(result)
    result.evaluations=[]
    assert not effective_pass(result)


@pytest.mark.parametrize('mock_kind',['model','tool'])
def test_mock_route_cannot_establish_new_business_state_evaluations(tmp_path,mock_kind):
    from agentguard.integrations.sdk import preflight
    from agentguard.integrations.agents import PythonAgent
    from agentguard.execution.agent_spec import AgentLaunchSpec
    from agentguard.core.scenario import Scenario
    from agentguard.environments import MemoryEnvironment
    from agentguard.security.policy import SecurityPolicy
    from agentguard.model_gateway.gateway import GatewayConfig
    from agentguard.integrations.tools import ToolContract,RoutedTool
    scenario=Scenario(name='mock-state',task='Verify real effect',initial_state={'value':1},evaluations=[dict(id='state',kind='state_equals',config={'path':'value','value':1})])
    tools=[RoutedTool(ToolContract(name='lookup',version='1.0.0',adapter='custom@1.0.0',input_schema={'type':'object'}),mode='mock',mock_result={})] if mock_kind=='tool' else []
    model=GatewayConfig(provider='mock',mode='mock',model='fixture') if mock_kind=='model' else None
    result=preflight(scenario,PythonAgent(AgentLaunchSpec(reference='never:Agent',source_dir=tmp_path)),MemoryEnvironment(),tools,SecurityPolicy(),model_config=model)
    assert not result.supported


def test_changed_observed_output_cannot_keep_a_passing_finding():
    from agentguard.core.scenario import Scenario
    from agentguard.runner.runner import ScenarioRunner
    from agentguard.sandbox.generic import GenericSandbox
    from agentguard.core.outcome import effective_pass
    class Agent:
        def run(self,task,sandbox):return 'ready'
    scenario=Scenario(name='output-evidence',task='Return ready',initial_state={},evaluations=[dict(id='output',kind='output_equals',config={'text':'ready'})])
    result=ScenarioRunner(GenericSandbox).run(scenario,Agent())
    assert effective_pass(result)
    result.worker_output='wrong'
    assert not effective_pass(result),'A contradictory recorded output must invalidate the evaluation evidence'


@pytest.mark.parametrize('name',['CON','NUL.txt','folder/AUX','COM1','LPT\u00b9.log','name.','name ','a?b'])
def test_workspace_paths_reject_devices_and_windows_aliases_before_file_io(name):
    from agentguard.environments.adapters import safe_relative
    with pytest.raises(PermissionError):safe_relative(name)


@pytest.mark.parametrize('files',[
    {'main.py':"print('ready')",'MAIN.py':"print('wrong')"},
    {'main.py':"print('ready')",'Folder/a.py':'a','folder/b.py':'b'},
])
def test_coding_snapshot_case_aliases_are_rejected_before_build_or_host_writes(monkeypatch,files):
    from agentguard.evaluation import coding
    from agentguard.evaluation.registry import MissingEvidence
    monkeypatch.setattr(coding,'command',lambda *args,**kwargs:pytest.fail('Unsafe snapshot reached Docker/build'))
    with pytest.raises(MissingEvidence):coding.run_tests({'entrypoint':'main.py','cases':[{'arguments':[],'expected_stdout':'ready'}],'timeout_seconds':10},files)
