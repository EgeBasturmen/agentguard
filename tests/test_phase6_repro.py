import pytest

def test_repro_cli_rejects_invalid_evidence_without_overwriting(tmp_path):
    from typer.testing import CliRunner
    from agentguard.cli.main import app
    import json
    bundle=tmp_path/'bundle.json';bundle.write_text(json.dumps({'schema_version':99}))
    output=tmp_path/'result.json';output.write_text('preserve')
    result=CliRunner().invoke(app,['release','reproduce',str(bundle),'--source-dir',str(tmp_path),'--output',str(output)])
    assert result.exit_code==2 and 'operation failed' in result.output
    assert output.read_text()=='preserve'

def test_repro_export_defaults_to_metadata_and_drops_detected_secrets():
    from agentguard.regression.repro import ExportPolicy,sanitize
    assert ExportPolicy().mode=='metadata'
    secret='AIza'+'x'*35
    data,changed=sanitize({'task':'prefix '+secret,'api_key':'private','note':'safe'},[])
    assert changed and secret not in str(data) and 'private' not in str(data)
    assert data['note']=='safe'

def test_repro_rejects_unknown_version_and_offline_scope_escape():
    from agentguard.regression.repro import ReproBundle,offline_plan
    with pytest.raises(ValueError):ReproBundle(schema_version=2,kind='agentguard.repro',source_digest='0'*64,signature='1'*64,evidence_digest='2'*64)
    from agentguard.integrations.plan import IntegrationPlan
    plan=dict(schema_version=1,scenario=dict(name='repro',task='do work',initial_state={'value':1},expected_state=[dict(path='value',value=1)]),
        agent=dict(transport='python',reference='agent:Agent',source_dir='../../host'),environment=dict(kind='memory'),tools=[],policy={})
    with pytest.raises(ValueError):offline_plan(IntegrationPlan.model_validate(plan))
    plan['agent']['source_dir']='.';plan['policy']={'allow_network':True}
    with pytest.raises(ValueError):offline_plan(IntegrationPlan.model_validate(plan))

@pytest.mark.docker
def test_real_failed_case_exports_and_reproduces_without_granting_release(tmp_path):
    from agentguard.regression.repro import make_bundle,ExportPolicy,reproduce
    from agentguard.integrations.plan import IntegrationPlan,run_integration_plan
    source=tmp_path/'agent.py';source.write_text("class Agent:\n    def run(self,task,sandbox):\n        sandbox.call('file.write',{'filename':'note.txt','content':'wrong'})\n        return 'done'\n")
    plan=IntegrationPlan.model_validate(dict(schema_version=1,scenario=dict(name='repro',task='Write done',initial_state={'files':{'note.txt':'draft'}},
        expected_state=[dict(path='files.note\\.txt',value='done')]),agent=dict(transport='python',reference='agent:Agent'),
        environment=dict(kind='filesystem'),tools=[dict(connector='environment')],policy=dict(allowed_tools=['file.write'])))
    original=run_integration_plan(plan,base_dir=tmp_path)
    assert not original.passed and original.cleanup_status=='verified'
    minimal=make_bundle(plan,original,ExportPolicy())
    assert minimal.plan is None
    with pytest.raises(ValueError):reproduce(minimal,tmp_path)
    policy=ExportPolicy(mode='replay',confirm_case_data=True,allow_fields=['scenario','agent','environment','tools','policy','required_capabilities'])
    bundle=make_bundle(plan,original,policy)
    redacted=make_bundle(plan,original,policy.model_copy(update={'redact_paths':['/scenario/task']}))
    assert redacted.redacted and redacted.plan is None
    result=reproduce(bundle,tmp_path)
    assert result['reproduced'] and result['execution_gate']=='BLOCK' and result['release_eligible'] is False
    from typer.testing import CliRunner
    from agentguard.cli.main import app
    from agentguard.reporting.models import RunReport
    import json
    plan_path=tmp_path/'plan.json';plan_path.write_text(plan.model_dump_json())
    report=tmp_path/'report.json';report.write_text(RunReport.from_result(original,'repro').model_dump_json())
    policy_path=tmp_path/'export-policy.json';policy_path.write_text(policy.model_dump_json())
    exported=tmp_path/'exported.json'
    cli=CliRunner().invoke(app,['release','export-repro',str(plan_path),str(report),'--output',str(exported),'--export-policy',str(policy_path)])
    assert cli.exit_code==0 and json.loads(cli.stdout)['runnable']
    cli=CliRunner().invoke(app,['release','reproduce',str(exported),'--source-dir',str(tmp_path),'--output',str(tmp_path/'reproduced.json')])
    assert cli.exit_code==0 and json.loads(cli.stdout)['reproduced']
    assert json.loads(cli.stdout)['release_eligible'] is False
    source.write_text(source.read_text().replace("'wrong'","'done'"))
    with pytest.raises(ValueError):reproduce(bundle,tmp_path)
