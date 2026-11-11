"""Real generated draft ? validation ? review ? freeze ? HTTP agent execution."""
from pathlib import Path
import pytest
from phase3_service import Service
from agentguard.generation.models import GenerationRequest
from agentguard.generation.generator import generate
from agentguard.generation.repository import ScenarioRepository
from agentguard.generation.execution import SuiteRunner
from agentguard.generation.coverage import coverage
from agentguard.core.outcome import effective_pass
from test_scenario_repository import approved


def http_request(service):
    endpoint=dict(base_url=service.url,allow_http=True,private_hosts=['127.0.0.1'])
    return GenerationRequest.model_validate(dict(task='done',fixture={'records':{'note':'draft','protected':'keep'}},
        runtime=dict(agent=dict(transport='http',endpoint={**endpoint,'base_url':service.url+'/agent'}),
            environment=dict(kind='http',endpoint=endpoint,allow_staging_writes=True),tools=[],policy={}),
        rules=[dict(id='goal',version='1',text='expect records.note = "done"'),dict(id='safe',version='1',text='unchanged records.protected')]))


def test_generated_http_suite_uses_real_observer_and_approved_receipts(tmp_path):
    with Service() as service:
        batch=generate(http_request(service));case=batch.cases[0]
        repo=ScenarioRepository(tmp_path/'r.sqlite');snapshot=approved(repo,case)
        run=SuiteRunner().run(repo,snapshot,base_dir=tmp_path)
        assert run.passed,run.model_dump()
        assert run.gate_decision=='ALLOW' and effective_pass(run.cases[0].result)
        assert run.cases[0].result.admission_status=='approved'
        assert not service.spaces
        matrix=coverage(batch.cases,batch.requirements,repository=repo,execution=run)
        assert any(row['passed']==1 for row in matrix['rows'])
        assert matrix['missing_executed'] # no fake adversarial/edge coverage from one benign run


def test_retired_suite_members_are_blocked_without_any_external_call(tmp_path):
    with Service() as service:
        case=generate(http_request(service)).cases[0];repo=ScenarioRepository(tmp_path/'r.sqlite');snapshot=approved(repo,case)
        repo.retire(case.id,case.revision)
        run=SuiteRunner().run(repo,snapshot,base_dir=tmp_path)
        assert not run.passed and run.cases[0].result is None
        assert not service.calls


def test_changed_approved_oracle_does_not_retain_pass_receipt(tmp_path):
    with Service() as service:
        case=generate(http_request(service)).cases[0];repo=ScenarioRepository(tmp_path/'r.sqlite');snapshot=approved(repo,case)
        result=SuiteRunner().run(repo,snapshot,base_dir=tmp_path).cases[0].result
        assert effective_pass(result)
        result.comparison_manifest.scenario.expected_state[0].value='forged'
        assert not effective_pass(result)


def test_missing_suite_member_is_never_successful(tmp_path):
    with Service() as service:
        case=generate(http_request(service)).cases[0];repo=ScenarioRepository(tmp_path/'r.sqlite');snapshot=approved(repo,case)
        run=SuiteRunner().run(repo,snapshot,base_dir=tmp_path)
        run.cases=[]
        assert not run.passed


def test_declared_required_matrix_blocks_partial_execution(tmp_path):
    with Service() as service:
        batch=generate(http_request(service));case=batch.cases[0];repo=ScenarioRepository(tmp_path/'r.sqlite');approved(repo,case)
        snapshot=repo.freeze('strict',[(case.id,1)],requirements=batch.requirements)
        result=SuiteRunner().run(repo,snapshot,base_dir=tmp_path)
        assert result.cases[0].result and effective_pass(result.cases[0].result)
        assert not result.passed and result.coverage_gaps


def test_retirement_during_later_case_blocks_entire_suite(tmp_path,monkeypatch):
    import agentguard.generation.execution as execution
    with Service() as service:
        req=http_request(service);first=generate(req).cases[0]
        data=req.model_dump();data['task']='second';data['task_id']='task:second';data['rules']=[{'id':'second','version':'1','text':'expect records.note = "second"'}]
        second=generate(GenerationRequest.model_validate(data)).cases[0]
        repo=ScenarioRepository(tmp_path/'r.sqlite');approved(repo,first);approved(repo,second)
        snapshot=repo.freeze('both',[(first.id,1),(second.id,1)])
        # Retire the already executed member while the later real HTTP case completes.
        earlier=snapshot.cases[0]['id'];real=execution.run_integration_plan;count=[0]
        def during_run(*args,**kwargs):
            result=real(*args,**kwargs);count[0]+=1
            if count[0]==2:repo.retire(earlier,1)
            return result
        monkeypatch.setattr(execution,'run_integration_plan',during_run)
        result=SuiteRunner().run(repo,snapshot,base_dir=tmp_path)
        assert not result.passed


def test_public_generated_plan_remains_unapproved_outside_suite_runner(tmp_path):
    from agentguard.integrations.plan import IntegrationPlan,run_integration_plan
    with Service() as service:
        case=generate(http_request(service)).cases[0]
        result=run_integration_plan(IntegrationPlan.model_validate(case.plan()),base_dir=tmp_path)
        assert not effective_pass(result)
        assert result.admission_status=='unapproved'


def test_approval_receipt_binds_actual_comparison_context(tmp_path):
    from agentguard.core.manifest import ComparisonManifest
    with Service() as service:
        case=generate(http_request(service)).cases[0];repo=ScenarioRepository(tmp_path/'r.sqlite')
        result=SuiteRunner().run(repo,approved(repo,case),base_dir=tmp_path).cases[0].result
        assert effective_pass(result)
        manifest=result.comparison_manifest.model_dump();manifest['evaluator_version']='unreviewed-evaluator';manifest['fingerprint']=None
        result.comparison_manifest=ComparisonManifest.model_validate(manifest)
        assert not effective_pass(result)


def test_real_run_report_incident_import_does_not_promote_observations_to_truth(tmp_path):
    from agentguard.generation.io import incident_from_report
    from agentguard.reporting.models import RunReport
    from agentguard.generation.validation import validate_candidate
    with Service() as service:
        request=http_request(service);case=generate(request).cases[0]
        repo=ScenarioRepository(tmp_path/'r.sqlite')
        result=SuiteRunner().run(repo,approved(repo,case),base_dir=tmp_path).cases[0].result
        path=tmp_path/'incident.json';path.write_text(RunReport.from_result(result,'http-agent').model_dump_json())
        incident=incident_from_report(path,'production-incident')
        data=request.model_dump();data['traces']=[incident]
        batch=generate(GenerationRequest.model_validate(data))
        reproduced=next(case for case in batch.cases if any(source.kind=='trace' for source in case.sources))
        assert incident['observed_status']=='passed' and reproduced.unresolved
        assert not validate_candidate(reproduced).ready
