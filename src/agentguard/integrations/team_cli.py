"""Explicit sequential team CLI. Canonical reports are private user-requested files."""
import json
import os
from pathlib import Path
import typer
from agentguard.integrations.http import _json
from agentguard.integrations.team import TeamPlan,run_team,verify_team

def team(plan:Path,output:Path=typer.Option(...,'--output')):
    try:
        if output.exists() or output.is_symlink():raise ValueError('Existing output is never overwritten')
        with plan.open('rb') as stream:raw=stream.read(1024*1024+1)
        if len(raw)>1024*1024:raise ValueError('Bounded team plan required')
        spec=TeamPlan.model_validate(_json(raw));result=run_team(spec,base_dir=plan.resolve().parent)
        passed=verify_team(spec,result)
        output.parent.mkdir(parents=True,exist_ok=True)
        descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    except Exception:
        typer.echo(json.dumps(dict(status='error',code='TEAM_EXECUTION_UNVERIFIED')),err=True);raise typer.Exit(2) from None
    typer.echo(json.dumps(dict(passed=passed,gate_decision='ALLOW' if passed else 'BLOCK',authorizes_release=False,members_completed=len(result['runs']))))
    if not passed:raise typer.Exit(1)
