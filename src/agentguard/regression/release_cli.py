"""CI orchestration; costly/external repeats require explicit operator flags."""
import json
from pathlib import Path
import typer
from agentguard.regression.cli import perform,emit
from agentguard.generation.repository import ScenarioRepository,Snapshot
from agentguard.generation.io import read_document
from agentguard.regression.repeats import RepeatPolicy,RepeatRunner,RepeatBatch,compare_repeats
from agentguard.regression.profiles import SuiteProfile,select_profile,export_reports,public_summary
from agentguard.regression.gate import GateAPI

app=typer.Typer(help='Execute bounded approved suite repeats and export consistent CI reports.')

@app.command('repeat')
def repeat(snapshot:Path,db:Path=typer.Option(...,'--db'),output:Path=typer.Option(...,'--output'),
    base_dir:Path=typer.Option(Path('.'),'--base-dir'),count:int=typer.Option(1,'--count'),
    policy:Path|None=typer.Option(None,'--policy'),profile:Path|None=typer.Option(None,'--profile'),
    allow_external:bool=typer.Option(False,'--allow-external'),allow_live_models:bool=typer.Option(False,'--allow-live-models')):
    def execute():
        if output.exists() or output.is_symlink():raise FileExistsError('Evidence output exists')
        repository=ScenarioRepository(db);parent=Snapshot.model_validate(read_document(snapshot))
        rules=RepeatPolicy.model_validate(read_document(policy)) if policy else RepeatPolicy()
        selected=select_profile(repository,parent,SuiteProfile.model_validate(read_document(profile)),rules) if profile else None
        actual=selected.snapshot if selected else parent;rules=selected.execution_policy() if selected else rules
        output.parent.mkdir(parents=True,exist_ok=True)
        # Reserve the output before external execution; crashes leave invalid evidence, never PASS.
        with output.open('x',encoding='utf-8') as stream:
            result=RepeatRunner().run(repository,actual,base_dir=base_dir,count=count,policy=rules,
                allow_external=allow_external,allow_live_models=allow_live_models,selection=selected)
            stream.write(result.model_dump_json(indent=2))
        return result
    result=perform(execute)
    emit(dict(attempts=len(result.attempts),fingerprint=result.fingerprint,output=str(output)))

@app.command('compare')
def compare(baseline:Path,candidate:Path,output:Path=typer.Option(...,'--output')):
    def evaluate():
        result=compare_repeats(RepeatBatch.model_validate(read_document(baseline)),RepeatBatch.model_validate(read_document(candidate)))
        return export_reports(result,output)
    summary=perform(evaluate);emit(summary)
    if not summary['passed']:raise typer.Exit(code=1)

@app.command('gate')
def gate(candidate:Path,baseline_name:str=typer.Option(...,'--baseline-name'),baseline_db:Path=typer.Option(...,'--baseline-db'),
    scenario_db:Path=typer.Option(...,'--scenario-db'),output:Path=typer.Option(...,'--output'),artifacts:Path|None=typer.Option(None,'--artifacts')):
    from agentguard.regression.baseline import BaselineRepository
    def evaluate():
        decision=GateAPI.evaluate(read_document(candidate),baseline_repository=BaselineRepository(baseline_db),baseline_name=baseline_name,
            scenario_repository=ScenarioRepository(scenario_db),candidate_artifacts=read_document(artifacts) if artifacts else None)
        return decision,export_reports(decision,output)
    decision,summary=perform(evaluate);emit(summary)
    if decision.exit_code:raise typer.Exit(code=decision.exit_code)

@app.command('export-repro')
def export_repro(plan:Path,report:Path,output:Path=typer.Option(...,'--output'),export_policy:Path|None=typer.Option(None,'--export-policy')):
    """Metadata by default; runnable case data needs an explicit reviewed export policy."""
    from agentguard.integrations.plan import IntegrationPlan
    from agentguard.reporting.models import validate_report
    from agentguard.regression.repro import ExportPolicy,make_bundle
    def export():
        if output.exists() or output.is_symlink():raise FileExistsError()
        bundle=make_bundle(IntegrationPlan.model_validate(read_document(plan)),validate_report(read_document(report)).result,
            ExportPolicy.model_validate(read_document(export_policy)) if export_policy else ExportPolicy())
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x',encoding='utf-8') as stream:stream.write(bundle.model_dump_json(indent=2))
        return dict(fingerprint=bundle.fingerprint,runnable=bundle.plan is not None,redacted=bundle.redacted)
    emit(perform(export))

@app.command('reproduce')
def reproduce_case(bundle:Path,source_dir:Path=typer.Option(...,'--source-dir'),output:Path=typer.Option(...,'--output')):
    """Run a pinned local Python case in Docker, without granting release approval."""
    from agentguard.regression.repro import ReproBundle,reproduce
    def execute():
        if output.exists() or output.is_symlink():raise FileExistsError()
        checked=ReproBundle.model_validate(read_document(bundle))
        output.parent.mkdir(parents=True,exist_ok=True)
        with output.open('x',encoding='utf-8') as stream:
            result=reproduce(checked,source_dir)
            stream.write(json.dumps(result,indent=2))
        return result
    result=perform(execute);emit(result)
    if not result['reproduced']:raise typer.Exit(code=1)
