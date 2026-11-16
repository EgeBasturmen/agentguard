import pytest
from agentguard.generation.models import RuntimeTemplate

pytestmark=pytest.mark.integration

def setup_project(tmp_path):
    from agentguard.management.service import ControlPlane
    service=ControlPlane(tmp_path/'server');owner=service.bootstrap('owner')
    project=service.create_project(owner['token'],'Generic agent')
    return service,owner['token'],project['id']

def runtime():
    return dict(agent=dict(transport='python',reference='agent:Agent',source_dir='.'),environment=dict(kind='filesystem'),
        tools=[dict(connector='environment')],policy=dict(allowed_tools=['file.write']))

def approved_suite(service,token,project):
    connection=service.connect(token,project,'Agent',runtime(),artifact_digest='a'*64)
    batch=service.generate_cases(token,project,connection['id'],task='Write done',fixture={'files':{'note.txt':'draft'}},
        rules=[dict(id='goal',text='expect files.note\\.txt = "done"')])
    case=service.case(token,project,batch['cases'][0]['id'])
    assert isinstance(case['oracle_checks'],dict)
    service.review_case(token,project,case['id'],decisions={key:'confirmed' for key in case['oracle_checks']},fixture=True,classification=True)
    service.approve_case(token,project,case['id'],case['case']['fingerprint'])
    return connection,service.freeze_suite(token,project,'Release',[case['id']])

def test_real_generation_review_and_tenant_scoped_frozen_suite(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path)
    connection=service.connect(token,project,'Agent',runtime(),artifact_digest='a'*64)
    batch=service.generate_cases(token,project,connection['id'],task='Write done',fixture={'files':{'note.txt':'draft'}},
        rules=[dict(id='goal',text='expect files.note\\.txt = "done"')])
    assert batch['cases'] and batch['approved']==0
    case=service.case(token,project,batch['cases'][0]['id'])
    with pytest.raises((ValueError,AccessError)):service.freeze_suite(token,project,'Unreviewed',[case['id']])
    service.review_case(token,project,case['id'],decisions={key:'confirmed' for key in case['oracle_checks']},fixture=True,classification=True)
    service.approve_case(token,project,case['id'],case['case']['fingerprint'])
    frozen=service.freeze_suite(token,project,'Release',[case['id']])
    assert frozen['snapshot']['cases']
    service.add_member(token,project,'viewer','viewer');reader=service.issue_key(token,project,'viewer',['project:read'])
    with pytest.raises(AccessError):service.approve_case(reader['token'],project,case['id'],case['case']['fingerprint'])
    other=service.create_project(token,'Other')
    with pytest.raises(AccessError):service.case(token,other['id'],case['id'])

def test_no_discovery_or_host_import_and_no_live_model_on_connect(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path)
    value=runtime();value['agent']['reference']='does_not_exist:Agent'
    result=service.connect(token,project,'Not imported',value,artifact_digest='a'*64)
    assert result['connection_status']=='configuration_saved' and not result['verified']
    value['agent']['source_dir']='../../host'
    with pytest.raises((ValueError,PermissionError,AccessError)):service.connect(token,project,'Unsafe',value,artifact_digest='a'*64)

def test_endpoint_credentials_are_rejected_before_storage(tmp_path):
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path)
    value=runtime();value['agent']={'transport':'http','endpoint':{'base_url':'https://user:password@example.test/agent'}}
    with pytest.raises((ValueError,AccessError)):service.connect(token,project,'Unsafe',value,artifact_digest='a'*64)

def test_revision_invalidates_old_frozen_admission(tmp_path):
    service,token,project=setup_project(tmp_path)
    _,suite=approved_suite(service,token,project)
    pin=suite['snapshot']['cases'][0]
    case_id=next(row['id'] for row in service.objects(token,project,'case') if row['case']['id']==pin['id'])
    case=service.case(token,project,case_id)
    revised=service.revise_case(token,project,case_id,case['case']['scenario'],'Re-review required')
    assert revised['status']=='draft'
    with pytest.raises(ValueError):service.suite_spec(token,project,suite['id'])
