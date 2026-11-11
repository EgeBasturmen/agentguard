"""Explicit local baseline management; no execution or implicit promotion."""
from pathlib import Path
import json
import typer
from agentguard.regression.baseline import BaselineRepository
from agentguard.generation.repository import ScenarioRepository
from agentguard.generation.io import read_document,load_execution

app=typer.Typer(help='Freeze, approve, promote and retire local immutable baselines.')

def perform(operation):
    try:return operation()
    except Exception as exc:
        # Imported evidence and local paths may contain sensitive content.
        typer.echo('Baseline operation failed; validate inputs and lifecycle state.',err=True)
        raise typer.Exit(code=2) from None

def emit(value):typer.echo(json.dumps(value,ensure_ascii=False,indent=2))

@app.command('create')
def create(name:str,evidence:Path,artifacts:Path=typer.Option(...,'--artifacts'),
    db:Path=typer.Option(...,'--db'),scenario_db:Path=typer.Option(...,'--scenario-db'),
    actor:str=typer.Option(...,'--actor'),reason:str=typer.Option(...,'--reason')):
    def freeze():
        raw=read_document(evidence);repeated=None
        if 'attempts' in raw:
            from agentguard.regression.repeats import RepeatBatch
            from agentguard.regression.baseline import checked_execution
            batch=RepeatBatch.model_validate(raw);repeated=batch.model_dump(mode='json')
            execution=checked_execution(batch.attempts[0]['execution'])
        else:execution=load_execution(evidence)
        return BaselineRepository(db).create(name,ScenarioRepository(scenario_db),execution,
            artifacts=read_document(artifacts),actor=actor,reason=reason,repeat_evidence=repeated)
    record=perform(freeze)
    emit(dict(name=record.name,fingerprint=record.fingerprint,status='draft',suite=record.snapshot.fingerprint))

@app.command('approve')
def approve(fingerprint:str,db:Path=typer.Option(...,'--db'),scenario_db:Path=typer.Option(...,'--scenario-db'),
    actor:str=typer.Option(...,'--actor'),reason:str=typer.Option(...,'--reason')):
    perform(lambda:BaselineRepository(db).approve(fingerprint,scenarios=ScenarioRepository(scenario_db),actor=actor,reason=reason))
    emit(dict(fingerprint=fingerprint,status='approved'))

@app.command('promote')
def promote(fingerprint:str,db:Path=typer.Option(...,'--db'),scenario_db:Path=typer.Option(...,'--scenario-db'),
    actor:str=typer.Option(...,'--actor'),reason:str=typer.Option(...,'--reason'),expected_active:str|None=typer.Option(None,'--expected-active')):
    perform(lambda:BaselineRepository(db).promote(fingerprint,scenarios=ScenarioRepository(scenario_db),
        actor=actor,reason=reason,expected_active=expected_active))
    emit(dict(fingerprint=fingerprint,status='active'))

@app.command('retire')
def retire(fingerprint:str,db:Path=typer.Option(...,'--db'),actor:str=typer.Option(...,'--actor'),reason:str=typer.Option(...,'--reason')):
    perform(lambda:BaselineRepository(db).retire(fingerprint,actor=actor,reason=reason))
    emit(dict(fingerprint=fingerprint,status='retired'))

@app.command('show')
def show(name:str,db:Path=typer.Option(...,'--db')):
    def read():
        repo=BaselineRepository(db);record=repo.active(name)
        return dict(name=record.name,fingerprint=record.fingerprint,suite=record.snapshot.fingerprint,
            artifacts=record.artifacts,status='active',history=repo.history(record.fingerprint))
    emit(perform(read))
