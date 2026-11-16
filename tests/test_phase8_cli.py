import json
import pytest
from typer.testing import CliRunner
from agentguard.cli.main import app

pytestmark=pytest.mark.integration

def test_team_cli_invalid_input_safe_and_no_overwrite(tmp_path):
    assert CliRunner().invoke(app,['team','--help']).exit_code==0
    plan=tmp_path/'team.json';plan.write_text('{}')
    output=tmp_path/'existing.json';output.write_text('user data')
    result=CliRunner().invoke(app,['team',str(plan),'--output',str(output)])
    assert result.exit_code!=0 and output.read_text()=='user data'
    assert 'Traceback' not in result.output

def test_team_cli_pre_cancelled_no_host_import(tmp_path):
    from agentguard.integrations.team import TeamPlan,run_team,verify_team
    import threading
    base=dict(schema_version=1,scenario=dict(name='s',task='task',initial_state={'tables':{}},expected_state=[dict(path='tables',value={})]),agent=dict(transport='python',reference='missing:Agent'),environment=dict(kind='database'))
    second=json.loads(json.dumps(base));second['scenario']['initial_state']={}
    plan=TeamPlan.model_validate(dict(steps=[dict(id='a',plan=base),dict(id='b',plan=second)]))
    cancel=threading.Event();cancel.set();result=run_team(plan,base_dir=tmp_path,cancel_event=cancel)
    assert not verify_team(plan,result) and not result['passed'] and result['runs']==[]
