"""Installed-package operations journey; synthetic manual payment only, no external services."""
import argparse
import json
from pathlib import Path
import tempfile

def verify(docker=False):
    from agentguard.management.service import ControlPlane,AccessError
    from agentguard.management.operations import backup,restore
    with tempfile.TemporaryDirectory(prefix='agentguard-operations-wheel-') as directory:
        root=Path(directory);service=ControlPlane(root/'state');owner=service.bootstrap('synthetic-wheel-owner');token=owner['token']
        project=service.create_project(token,'Operations fixture')['id']
        assert service.onboarding_funnel(token,project)['verified_current_runs']==0
        if docker:
            from agentguard.execution.agent_spec import AgentLaunchSpec
            from agentguard.management.http import running_server
            from agentguard.management.client import Client
            from agentguard.management.runner import PrivateRunner
            source=root/'source';source.mkdir()
            (source/'agent.py').write_text("class Agent:\n    def run(self,task,sandbox):sandbox.call('file.write',{'filename':'note.txt','content':'done'})\n",encoding='utf-8')
            _,digest=AgentLaunchSpec(reference='agent:Agent',source_dir=source).sources()
            runtime=dict(agent=dict(transport='python',reference='agent:Agent'),environment=dict(kind='filesystem'),tools=[dict(connector='environment')],policy=dict(allowed_tools=['file.write']))
            connection=service.connect(token,project,'Explicit fixture',runtime,artifact_digest=digest)
            batch=service.generate_cases(token,project,connection['id'],task='Write done',fixture={'files':{'note.txt':'draft'}},rules=[dict(id='goal',text='expect files.note\\.txt = "done"')])
            case=service.case(token,project,batch['cases'][0]['id']);service.review_case(token,project,case['id'],decisions={k:'confirmed' for k in case['oracle_checks']},fixture=True,classification=True)
            service.approve_case(token,project,case['id'],case['case']['fingerprint']);suite=service.freeze_suite(token,project,'Fixture',[case['id']])
            invite=service.enroll_runner(token,project,'Wheel fixture');worker=service.exchange_enrollment(invite['enrollment_token'])
            with running_server(service) as url:
                service.enqueue(token,project,suite['id'],'actual-wheel-run')
                result=PrivateRunner(Client(url,worker['runner_token']),source_root=source,cache_root=root/'cache').run_once()
                assert result['gate_decision']=='ALLOW' and result['cleanup_status']=='verified'
        request=service.request_plan(token,project,'pilot')
        service.operator_activate_plan(request['id'],operator='synthetic-fixture',receipt_reference='synthetic-no-real-payment')
        assert service.reconcile_billing(project,operator='synthetic-fixture',reference='synthetic-no-real-payment',paid=True)['status']=='matched'
        cancel=service.request_cancellation(token,project);service.operator_cancel_plan(cancel['id'],operator='synthetic-fixture',reason='Fixture only')
        assert service.operations_health(token,project)['status']=='ready'
        archive=root/'private.agbackup';backup(service,archive)
        restored_path=root/'restored';restore(archive,restored_path,signing_key=service.key_path,deletions=service.root/'deletions.json')
        restored=ControlPlane(restored_path)
        try:restored.project(token,project)
        except AccessError:pass
        else:raise AssertionError('Restore did not revoke old credentials')
        recovered=restored.operator_recover_owner(owner['workspace'],'synthetic-wheel-owner',operator='fixture',reason='Restore recovery')
        assert restored.project(recovered['token'],project)['plan']=='developer'
        if docker:assert restored.job(recovered['token'],project,result['id'])['gate_decision']=='ALLOW'
        service.delete_project(token,project,confirmation='Operations fixture')
        service.delete_workspace(token,confirmation='Initial workspace')
        restore(archive,root/'after-delete',signing_key=service.key_path,deletions=service.root/'deletions.json')
        with ControlPlane(root/'after-delete').db() as db:
            assert db.execute('SELECT 1 FROM projects WHERE id=?',(project,)).fetchone() is None
            assert db.execute('SELECT count(*) FROM workspaces').fetchone()[0]==0
    return dict(phase9_operations=True,phase9_backup_restore=True,phase9_billing_reconciliation=True,phase9_worker=docker,
        external_pilot_verified=False,real_payment_verified=False,live_api_calls=0)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--docker',action='store_true')
    print(json.dumps(verify(parser.parse_args().docker)))
