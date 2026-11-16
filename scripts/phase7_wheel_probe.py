"""Runtime-only private control-plane probe; synthetic fixtures are not external pilots."""
import argparse
import json
from pathlib import Path
import tempfile
from importlib import resources


def verify(docker=False):
    from agentguard.management.service import ControlPlane, AccessError
    from agentguard.management.client import Client
    from agentguard.management.http import running_server
    from agentguard.management.runner import PrivateRunner
    from agentguard.execution.agent_spec import AgentLaunchSpec
    with tempfile.TemporaryDirectory(prefix='agentguard-management-wheel-') as directory:
        root=Path(directory);source=root/'source';source.mkdir()
        (source/'agent.py').write_text("class Agent:\n    def run(self,task,sandbox):\n        sandbox.call('file.write',{'filename':'note.txt','content':'done'})\n        return 'done'\n",encoding='utf-8')
        service=ControlPlane(root/'state');owner=service.bootstrap('synthetic-wheel-operator')
        token=owner['token'];project=service.create_project(token,'Wheel fixture')['id']
        _,digest=AgentLaunchSpec(reference='agent:Agent',source_dir=source).sources()
        runtime=dict(agent=dict(transport='python',reference='agent:Agent',source_dir='.'),environment=dict(kind='filesystem'),tools=[dict(connector='environment')],policy=dict(allowed_tools=['file.write']))
        with running_server(service) as url:
            client=Client(url,token);base='/api/projects/'+project
            connection=client.request(base+'/connections',dict(name='Explicit Python',runtime=runtime,artifact_digest=digest))
            assert not connection['verified'] and not connection['automatic_discovery']
            batch=client.request(base+'/generate',dict(connection=connection['id'],task='Write done',fixture={'files':{'note.txt':'draft'}},rules=[dict(id='goal',text='expect files.note\\.txt = "done"')]))
            case_id=batch['cases'][0]['id'];case=client.request(base+'/cases/'+case_id)
            try:client.request(base+'/suites',dict(name='Unapproved',cases=[case_id]))
            except AccessError:pass
            else:raise AssertionError('Unapproved suite admitted')
            client.request(base+'/cases/'+case_id+'/review',dict(decisions={key:'confirmed' for key in case['oracle_checks']},fixture=True,classification=True))
            client.request(base+'/cases/'+case_id+'/approve',dict(fingerprint=case['case']['fingerprint']))
            suite=client.request(base+'/suites',dict(name='Reviewed',cases=[case_id]))
            enrollment=client.request(base+'/runner-enrollment',dict(name='Synthetic wheel runner'))
            worker=Client(url).request('/api/runner/exchange',dict(enrollment_token=enrollment['enrollment_token']))
            runner=PrivateRunner(Client(url,worker['runner_token']),source_root=source,cache_root=root/'private-cache')
            client.request(base+'/runs',dict(suite=suite['id'],idempotency_key='wheel-preflight',kind='preflight'))
            preflight=runner.run_once()
            assert preflight['state']=='completed' and preflight['gate_decision']!='ALLOW'
            if docker:
                client.request(base+'/runs',dict(suite=suite['id'],idempotency_key='wheel-execution'))
                executed=runner.run_once()
                assert executed['gate_decision']=='ALLOW' and executed['cleanup_status']=='verified'
                assert executed['authorizes_release'] is False
            assert client.request(base+'/pilot')['external_pilot_verified'] is False
            other=service.create_project(token,'Other fixture')['id']
            try:client.request('/api/projects/'+other+'/cases/'+case_id)
            except AccessError:pass
            else:raise AssertionError('Cross-project case access allowed')
        for name in ('index.html','app.js','app.css'):
            assert resources.files('agentguard.management.web').joinpath(name).read_bytes()
        assert token.encode() not in (root/'state'/'control.sqlite').read_bytes()
    return dict(phase7_control=True,phase7_authorization=True,phase7_runner=docker,external_pilot_verified=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--docker',action='store_true')
    print(json.dumps(verify(parser.parse_args().docker)))
