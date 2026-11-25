"""Synthetic review-smoke selection; never inspect or mutate live state."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys

import pytest

pytestmark=pytest.mark.integration


@pytest.fixture
def smoke(monkeypatch):
    # The CLI script normally has scripts/ on sys.path; reproduce that import context.
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    spec=importlib.util.spec_from_file_location('review_selection_smoke',scripts/'check_sprint1_review_ui.py')
    module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,module);spec.loader.exec_module(module)
    return module


def row(object_id='a'*32,case_id='case-ready',revision=1,ready=True):
    return dict(id=object_id,status='draft',ready=ready,case=dict(id=case_id,revision=revision,fingerprint='f'*64))


@pytest.mark.parametrize('reverse',[False,True])
def test_selects_ready_case_independently_of_order_and_equal_timestamps(smoke,reverse):
    bad=row('0'*32,'case-unready',ready=False);good=row('a'*32)
    another=row('b'*32,'case-z-ready');items=[bad,good,another]
    for item in items:item['created']=1234
    if reverse:items.reverse()
    target=smoke.select_review_case(items)
    assert (target.object_id,target.case_id,target.revision)==(good['id'],good['case']['id'],1)


@pytest.mark.parametrize('items',[[],[row(ready=False)],[row(ready='true')]])
def test_only_unready_is_an_explicit_error_never_a_pass(smoke,items):
    with pytest.raises(smoke.ReviewSmokeError,match='NO_REVIEWABLE_CASE'):
        smoke.select_review_case(items)


@pytest.mark.parametrize('field,value', [('id','unsafe"selector'),('case.id',''),('case.revision',0),('case.revision',True),('case.fingerprint','bad')])
def test_invalid_selection_identity_is_fail_closed(smoke,field,value):
    item=row()
    if field.startswith('case.'):item['case'][field.split('.')[1]]=value
    else:item[field]=value
    with pytest.raises(smoke.ReviewSmokeError):smoke.select_review_case([item])


@pytest.mark.parametrize('field,value',[('id','b'*32),('case.id','case-different'),('case.revision',2),('case.fingerprint','e'*64),('ready',False),('status','approved')])
def test_pre_review_revalidation_rejects_changed_binding(smoke,field,value):
    initial=row();target=smoke.select_review_case([initial]);changed=deepcopy(initial)
    if field.startswith('case.'):changed['case'][field.split('.')[1]]=value
    else:changed[field]=value
    with pytest.raises(smoke.ReviewSmokeError):smoke.validate_review_target(changed,target)


def test_same_timestamp_real_storage_can_put_unreviewable_edge_first(tmp_path,monkeypatch,smoke):
    from scripts.sprint1_verification_support import secure_fixture_directory
    from agentguard.management.service import ControlPlane
    from agentguard.management import objects
    secure_fixture_directory(tmp_path)
    service=ControlPlane(tmp_path/'server',clock=lambda:1800000000)
    owner=service.bootstrap('synthetic-selection-owner')['token'];project=service.create_project(owner,'Synthetic selection')['id']
    runtime=dict(agent=dict(transport='python',reference='agent:Agent',source_dir='.'),environment=dict(kind='filesystem'),tools=[dict(connector='environment')],policy=dict(allowed_tools=['file.write']))
    connection=service.connect(owner,project,'Synthetic connection',runtime,artifact_digest='a'*64)
    identities=iter(['2'*32,'1'*32,'3'*32,'4'*32])
    monkeypatch.setattr(objects,'identity',lambda:next(identities))
    service.generate_cases(owner,project,connection['id'],task='Write done',fixture={'files':{'note.txt':'draft'}},
        rules=[dict(id='goal',text='expect files = {"note.txt":"done"}'),dict(id='tool',text='tool file.write times 1')])
    rows=service.objects(owner,project,'case')
    assert rows[0]['ready'] is False and rows[0]['case']['axis']=='edge'
    with pytest.raises(ValueError):
        service.review_case(owner,project,rows[0]['id'],decisions={k:'confirmed' for k in rows[0]['oracle_checks']},fixture=True,classification=True)
    assert service.case(owner,project,rows[0]['id'])['status']=='draft'
    target=smoke.select_review_case(rows)
    selected=service.case(owner,project,target.object_id)
    smoke.validate_review_target(selected,target)
    assert selected['ready'] is True and selected['case']['axis']=='benign'
