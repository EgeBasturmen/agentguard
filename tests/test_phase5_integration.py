"""Real pipe ordering, typed report integrity and model/prompt comparisons."""
import json
import subprocess
import sys
import pytest
from agentguard.core.scenario import Scenario
from agentguard.core.outcome import effective_pass
from agentguard.runner.runner import ScenarioRunner
from agentguard.sandbox.generic import GenericSandbox
from agentguard.model_gateway.gateway import GatewayConfig,ModelGateway
from agentguard.regression.comparator import RegressionComparator
from phase5_service import ModelService,model_config


def test_actual_model_prompt_versions_are_comparable_and_reported(tmp_path):
    scenario=Scenario(name='prompt-versions',task='Return ready',initial_state={},evaluations=[dict(id='output',kind='output_equals',config={'text':'ready'})])
    def run(service,version):
        gateway=ModelGateway(GatewayConfig.model_validate(model_config(service,prompt_version=version)))
        class Agent:
            def run(self,task,sandbox):return gateway.generate([dict(role='user',content=task)],max_output_tokens=32)
        result=ScenarioRunner(GenericSandbox).run(scenario,Agent())
        result.model_evidence=gateway.evidence().model_dump(mode='json');return result
    with ModelService() as service:
        first=run(service,'v1');second=run(service,'v2')
        result=RegressionComparator().compare('v1',first,'v2',second)
        assert result.passed and result.comparison_status=='verified'
        assert any(f.finding_type=='model_configuration_changed' for f in result.findings)
        from agentguard.reporting.export import export_json,export_html
        from agentguard.reporting.models import load_report
        path=tmp_path/'run.json';export_json(second,path,'model-agent');export_html(second,tmp_path/'run.html','model-agent')
        assert load_report(path).result.model_evidence['calls'][0]['usage_source']=='provider-reported'
        payload=json.loads(path.read_text());payload['result']['model_evidence']['calls'][0]['estimated_cost_micro_usd']=0
        forged=tmp_path/'forged.json';forged.write_text(json.dumps(payload))
        with pytest.raises(ValueError):load_report(forged)


@pytest.mark.parametrize('turns',[[],[2],[1,1],[1,3]])
def test_missing_replayed_or_out_of_order_session_frames_are_rejected(turns):
    from agentguard.execution.process_ipc import ProcessIPC,ProcessIPCError
    from agentguard.execution.ipc_dispatcher import IPCDispatcher
    from agentguard.execution.tool_broker import ToolBroker
    from agentguard.execution.tool_registry import ToolRegistry
    from agentguard.execution.records import RuntimeManifest
    run_id='a'*32
    frames=[dict(protocol_version=2,type='started',run_id=run_id)]+[dict(protocol_version=2,type='turn_completed',run_id=run_id,turn=turn,output='ready') for turn in turns]+[dict(protocol_version=2,type='completed',run_id=run_id,status='ok',output='ready')]
    program='\n'.join('print('+repr(json.dumps(frame))+',flush=True)' for frame in frames)
    process=subprocess.Popen([sys.executable,'-u','-c',program],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    ipc=ProcessIPC(process,IPCDispatcher(ToolBroker(registry=ToolRegistry())),timeout_seconds=5,run_manifest=RuntimeManifest(run_id=run_id),expected_turns=2)
    try:
        with pytest.raises(ProcessIPCError):ipc.serve()
    finally:
        if process.poll() is None:process.kill()
        process.wait(timeout=2);ipc.close()
        for stream in (process.stdin,process.stdout,process.stderr):stream.close()


def test_mock_live_comparison_explains_its_blocking_mode_difference():
    scenario=Scenario(name='modes',task='ready',initial_state={},evaluations=[dict(id='text',kind='output_equals',config={'text':'ready'})])
    class Agent:
        def run(self,task,sandbox):return 'ready'
    first=ScenarioRunner(GenericSandbox).run(scenario,Agent());second=ScenarioRunner(GenericSandbox).run(scenario,Agent())
    with ModelService() as service:
        live=ModelGateway(GatewayConfig.model_validate(model_config(service)));live.generate([dict(role='user',content='ready')],max_output_tokens=32)
        mock=ModelGateway(GatewayConfig(provider='mock',model='fixture',mode='mock',mock_output='ready'));mock.generate([dict(role='user',content='ready')],max_output_tokens=32)
        first.model_evidence=live.evidence().model_dump(mode='json');second.model_evidence=mock.evidence().model_dump(mode='json')
        result=RegressionComparator().compare('live',first,'mock',second)
        assert not result.passed
        assert any(f.finding_type=='model_mode_changed' for f in result.findings)


def test_phase4_reviewed_suite_can_execute_a_new_output_evaluator(tmp_path):
    from phase3_service import Service
    from test_generated_execution import http_request
    from test_scenario_repository import approved
    from agentguard.generation.generator import generate
    from agentguard.generation.repository import ScenarioRepository
    from agentguard.generation.execution import SuiteRunner
    with Service() as service:
        case=generate(http_request(service)).cases[0];repo=ScenarioRepository(tmp_path/'scenarios.sqlite');repo.add(case)
        scenario=dict(case.scenario);scenario['evaluations']=[dict(id='output',kind='output_equals',config={'text':'remote agent complete'})]
        revised=repo.revise(case.id,1,scenario=scenario,reason='Independently confirmed HTTP output contract')
        report=SuiteRunner().run(repo,approved(repo,revised),base_dir=tmp_path)
        assert report.passed,report.model_dump()
        assert report.cases[0].result.evaluations[0].status=='pass'
