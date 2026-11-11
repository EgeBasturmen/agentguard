"""Data-only generation and explicit review workflow. No implicit approval commands."""
import json
from pathlib import Path
import typer
from agentguard.generation.generator import generate
from agentguard.generation.models import GenerationRequest
from agentguard.generation.repository import ScenarioRepository,Snapshot
from agentguard.generation.validation import validate_candidate
from agentguard.generation.execution import SuiteRunner
from agentguard.generation.coverage import coverage
from agentguard.generation.io import read_request,read_document,import_rules,incident_from_report,export_dataset,import_dataset,Dataset,write_execution,load_execution

app=typer.Typer(help='Generate drafts; validate, review and explicitly approve versioned release suites.')

def attempt(function):
    try:return function()
    except Exception as exc:
        typer.echo(f'Scenario workflow error: {type(exc).__name__}: {exc}',err=True)
        raise typer.Exit(code=2) from exc

def emit(value):typer.echo(json.dumps(value,ensure_ascii=False,indent=2))
def members(values):
    result=[]
    for value in values:
        name,version=value.rsplit(':',1);result.append((name,int(version)))
    return result

def new_json(value,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)

@app.command('generate')
def generate_command(request_file:Path,db:Path=typer.Option(...,'--db'),output:Path=typer.Option(...,'--output'),
    rules:Path|None=typer.Option(None,'--rules'),incident:Path|None=typer.Option(None,'--incident')):
    def run():
        if output.exists():raise FileExistsError(output)
        request=read_request(request_file)
        data=request.model_dump(mode='json')
        if rules:data['rules'].extend(rule.model_dump() for rule in import_rules(rules))
        if incident:data['traces'].append(incident_from_report(incident,'incident:'+incident.stem))
        batch=generate(GenerationRequest.model_validate(data));repository=ScenarioRepository(db)
        for case in batch.cases:repository.add(case)
        export_dataset(batch,output)
        return dict(drafts=len(batch.cases),approved=0,review_required=True,output=str(output),gaps=batch.gaps)
    emit(attempt(run))

@app.command('show')
def show_command(identity:str,revision:int,db:Path=typer.Option(...,'--db')):
    def run():
        case,status,review=ScenarioRepository(db).get(identity,revision)
        return dict(status=status,case=case.model_dump(),review=review)
    emit(attempt(run))

@app.command('validate')
def validate_command(identity:str,revision:int,db:Path=typer.Option(...,'--db')):
    check=attempt(lambda:validate_candidate(ScenarioRepository(db).get(identity,revision)[0]))
    emit(dict(status='READY_FOR_REVIEW' if check.ready else 'INCONCLUSIVE',ready_for_review=check.ready,review_required=True,issues=check.issues,oracle_ids=check.oracle_ids))
    if not check.ready:raise typer.Exit(code=1)

@app.command('review')
def review_command(identity:str,revision:int,db:Path=typer.Option(...,'--db'),reviewer:str=typer.Option(...,'--reviewer'),
    oracle:list[str]=typer.Option([],'--confirm-oracle'),fixture:bool=typer.Option(False,'--confirm-fixture'),
    classification:bool=typer.Option(False,'--confirm-classification'),notes:str=typer.Option('','--notes')):
    def run():
        repository=ScenarioRepository(db);case=repository.get(identity,revision)[0]
        if set(oracle)-set(case.oracle_checks):raise ValueError('Unknown oracle confirmation')
        return repository.review(identity,revision,reviewer=reviewer,fixture_confirmed=fixture,classification_confirmed=classification,notes=notes,
            decisions={key:'confirmed' if key in oracle else 'unknown' for key in case.oracle_checks})
    emit(attempt(run))

@app.command('approve')
def approve_command(identity:str,revision:int,db:Path=typer.Option(...,'--db'),reviewer:str=typer.Option(...,'--reviewer'),fingerprint:str=typer.Option(...,'--fingerprint')):
    case=attempt(lambda:ScenarioRepository(db).approve(identity,revision,reviewer=reviewer,expected_digest=fingerprint))
    emit(dict(id=case.id,revision=case.revision,status='approved',fingerprint=case.fingerprint))

@app.command('revise')
def revise_command(identity:str,revision:int,scenario:Path,db:Path=typer.Option(...,'--db'),reason:str=typer.Option(...,'--reason')):
    case=attempt(lambda:ScenarioRepository(db).revise(identity,revision,scenario=read_document(scenario),reason=reason))
    emit(dict(id=case.id,revision=case.revision,status='draft',fingerprint=case.fingerprint))

@app.command('retire')
def retire_command(identity:str,revision:int,db:Path=typer.Option(...,'--db')):
    attempt(lambda:ScenarioRepository(db).retire(identity,revision));emit(dict(id=identity,revision=revision,status='retired'))

@app.command('freeze')
def freeze_command(name:str,db:Path=typer.Option(...,'--db'),member:list[str]=typer.Option(...,'--member'),
    output:Path=typer.Option(...,'--output'),required_coverage:Path|None=typer.Option(None,'--required-coverage')):
    def run():
        if output.exists():raise FileExistsError(output)
        requirements=import_dataset(required_coverage).requirements if required_coverage else []
        snapshot=ScenarioRepository(db).freeze(name,members(member),requirements=requirements)
        new_json(snapshot.model_dump(),output);return dict(snapshot=snapshot.fingerprint,output=str(output))
    emit(attempt(run))

@app.command('import')
def import_command(dataset:Path,db:Path=typer.Option(...,'--db')):
    cases=attempt(lambda:ScenarioRepository(db).import_cases(import_dataset(dataset).cases))
    emit(dict(imported=[dict(id=c.id,revision=c.revision,status='draft') for c in cases],approved=0))

@app.command('export')
def export_command(db:Path=typer.Option(...,'--db'),member:list[str]=typer.Option(...,'--member'),output:Path=typer.Option(...,'--output')):
    def run():
        repository=ScenarioRepository(db);cases=[repository.get(name,revision)[0] for name,revision in members(member)]
        return str(export_dataset(Dataset(cases=cases,gaps=['Selected revisions only; no global coverage policy is implied']),output))
    emit(dict(output=attempt(run)))

@app.command('run')
def run_command(snapshot:Path,db:Path=typer.Option(...,'--db'),base_dir:Path=typer.Option(Path('.'),'--base-dir'),output:Path=typer.Option(...,'--output')):
    def run():
        if output.exists():raise FileExistsError(output)
        result=SuiteRunner().run(ScenarioRepository(db),Snapshot.model_validate(read_document(snapshot)),base_dir=base_dir)
        write_execution(result,output);return result
    result=attempt(run);emit(dict(gate_decision=result.gate_decision,passed=result.passed,coverage_gaps=result.coverage_gaps,output=str(output)))
    if not result.passed:raise typer.Exit(code=1)

@app.command('coverage')
def coverage_command(dataset:Path,db:Path=typer.Option(...,'--db'),execution:Path|None=typer.Option(None,'--execution'),output:Path|None=typer.Option(None,'--output')):
    def run():
        data=import_dataset(dataset)
        result=coverage(data.cases,data.requirements,repository=ScenarioRepository(db),execution=load_execution(execution) if execution else None)
        if output:new_json(result,output)
        return result
    emit(attempt(run))
