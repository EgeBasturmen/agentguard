"""Actual generated/approved/frozen suites on Python and language-neutral Docker workers."""
import json
from pathlib import Path
import subprocess
import pytest
from agentguard.generation.models import GenerationRequest
from agentguard.generation.generator import generate
from agentguard.generation.repository import ScenarioRepository
from agentguard.generation.execution import SuiteRunner
from agentguard.generation.validation import validate_candidate
from agentguard.generation.coverage import coverage
from test_scenario_repository import approved



def generation_request(tmp_path,agent=None):
    (tmp_path/'generated_agent.py').write_text("class Agent:\n    async def run(self,task,sandbox):\n        sandbox.call('file.write',{'filename':'note.txt','content':'done'})\n        return 'done'\n")
    return GenerationRequest.model_validate(dict(task='Update note.txt to done',fixture={'files':{'note.txt':'draft','protected.txt':'keep'}},
        runtime=dict(agent=agent or dict(transport='python',reference='generated_agent:Agent',source_dir=str(tmp_path)),environment=dict(kind='filesystem'),tools=[dict(connector='environment')],
            policy=dict(allowed_tools=['file.write'],resource_scopes={'file.write':{'filename':['note.txt']}})),
        rules=[dict(id='goal',text='expect files.note\\.txt = "done"'),dict(id='safe',text='unchanged files.protected\\.txt'),dict(id='call',text='tool file.write times 1')]))


@pytest.mark.docker
def test_generated_python_suite_executes_real_container_and_records_coverage(tmp_path):
    batch=generate(generation_request(tmp_path));case=next(case for case in batch.cases if case.axis=='benign')
    assert validate_candidate(case).ready
    repo=ScenarioRepository(tmp_path/'r.sqlite');snapshot=approved(repo,case)
    run=SuiteRunner().run(repo,snapshot,base_dir=tmp_path)
    assert run.passed,run.model_dump()
    result=run.cases[0].result
    assert result.cleanup_status=='verified' and result.run_manifest.completed
    assert result.audit and result.worker_output=='done'
    matrix=coverage(batch.cases,batch.requirements,repository=repo,execution=run)
    assert any(row['requirement']=='contract:file.write' and row['passed']==1 for row in matrix['rows'])
    assert all(row['passed']==0 for row in matrix['rows'] if row['axis']=='edge')


@pytest.mark.integration
def test_boundary_oracle_is_not_approved_from_schema_alone(tmp_path):
    batch=generate(generation_request(tmp_path))
    edge=next(case for case in batch.cases if case.axis=='edge')
    assert not validate_candidate(edge).ready
    repo=ScenarioRepository(tmp_path/'r.sqlite');repo.add(edge)
    with pytest.raises(ValueError):repo.review(edge.id,1,reviewer='human',fixture_confirmed=True,classification_confirmed=True,decisions={key:'confirmed' for key in edge.oracle_checks})


@pytest.mark.docker
def test_unknown_draft_can_be_resolved_only_as_a_new_reviewed_version(tmp_path):
    req=generation_request(tmp_path);data=req.model_dump();data['rules']=[]
    case=generate(GenerationRequest.model_validate(data)).cases[0]
    repo=ScenarioRepository(tmp_path/'r.sqlite');repo.add(case)
    raw=dict(case.scenario);raw['expected_state']=[dict(path=r'files.note\.txt',value='done')]
    resolved=repo.revise(case.id,1,scenario=raw,reason='Independent fixture/oracle established by operator')
    assert resolved.revision==2 and repo.get(case.id,2)[1]=='draft'
    snapshot=approved(repo,resolved)
    run=SuiteRunner().run(repo,snapshot,base_dir=tmp_path)
    assert run.passed,run.model_dump()


def test_generation_has_no_host_agent_import(tmp_path,monkeypatch):
    req=generation_request(tmp_path)
    marker=tmp_path/'imported'
    (tmp_path/'generated_agent.py').write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("unsafe")\nraise RuntimeError("host import")\n')
    batch=generate(req)
    assert batch.cases and not marker.exists()


@pytest.mark.docker
def test_generated_suite_supports_non_python_container_protocol(tmp_path):
    info=json.loads(subprocess.check_output(['docker','image','inspect','python:3.11-slim']))[0]
    base=info['RepoDigests'][0]
    script="#!/bin/sh\nIFS= read -r start\nid=$(printf '%s' \"$start\" | sed -n 's/.*\"run_id\":\"\\([^\" ]*\\)\".*/\\1/p')\nprintf '{\"protocol_version\":2,\"type\":\"started\",\"run_id\":\"%s\"}\\n' \"$id\"\nprintf '%s\\n' '{\"protocol_version\":1,\"type\":\"tool_call\",\"call_id\":\"generated-shell\",\"tool_name\":\"file.write\",\"arguments\":{\"filename\":\"note.txt\",\"content\":\"done\"}}'\nIFS= read -r result\nprintf '{\"protocol_version\":2,\"type\":\"completed\",\"run_id\":\"%s\",\"status\":\"ok\",\"output\":\"generated shell\"}\\n' \"$id\"\n"
    (tmp_path/'worker.sh').write_text(script,encoding='utf-8',newline='')
    (tmp_path/'Dockerfile').write_text('FROM '+base+'\nCOPY worker.sh /worker.sh\nENTRYPOINT ["/bin/sh","/worker.sh"]\n')
    build=subprocess.run(['docker','build','--network=none','--quiet',str(tmp_path)],capture_output=True,text=True,check=True,timeout=60)
    image=build.stdout.strip().splitlines()[-1]
    req=generation_request(tmp_path,dict(transport='container',image=image))
    case=generate(req).cases[0];repo=ScenarioRepository(tmp_path/'r.sqlite');snapshot=approved(repo,case)
    run=SuiteRunner().run(repo,snapshot,base_dir=tmp_path)
    assert run.passed,run.model_dump()
    assert run.cases[0].result.run_manifest.artifact_kind=='container_image'


@pytest.mark.docker
def test_edge_case_cannot_pass_when_agent_ignores_declared_boundary_input(tmp_path):
    data=generation_request(tmp_path).model_dump();data['runtime']['policy']['resource_scopes']={}
    batch=generate(GenerationRequest.model_validate(data))
    edge=next(case for case in batch.cases if case.axis=='edge' and case.observations['tool']=='file.write')
    repo=ScenarioRepository(tmp_path/'r.sqlite');repo.add(edge)
    resolved=repo.revise(edge.id,1,scenario=edge.scenario,reason='Operator supplied independent expected state for this edge task')
    snapshot=approved(repo,resolved)
    run=SuiteRunner().run(repo,snapshot,base_dir=tmp_path)
    # Agent always writes note.txt/done, never the schema-boundary arguments. Oracle alone is insufficient.
    assert not run.passed,run.model_dump()


@pytest.mark.docker
def test_reviewed_boundary_case_passes_only_with_matching_observed_arguments(tmp_path):
    data=generation_request(tmp_path).model_dump();data['runtime']['policy']['resource_scopes']={}
    batch=generate(GenerationRequest.model_validate(data))
    edge=next(case for case in batch.cases if case.axis=='edge' and case.observations['tool']=='file.write')
    (tmp_path/'generated_agent.py').write_text("import json\nclass Agent:\n    def run(self,task,sandbox):\n        instruction=json.loads(task)\n        sandbox.call(instruction['tool'],instruction['arguments'])\n        return 'boundary applied'\n")
    args=edge.observations['arguments'];raw=dict(edge.scenario)
    raw['expected_state']=[dict(path='files.'+args['filename'].replace('.','\\.'),value=args['content'])]
    repo=ScenarioRepository(tmp_path/'r.sqlite');repo.add(edge)
    revised=repo.revise(edge.id,1,scenario=raw,reason='Independent file-write contract and isolated fixture establish boundary expectation')
    run=SuiteRunner().run(repo,approved(repo,revised),base_dir=tmp_path)
    assert run.passed,run.model_dump()
    matrix=coverage([revised],[{'id':'risk:schema_boundary','axes':['edge']}],repository=repo,execution=run)
    assert matrix['rows'][0]['passed']==1
