"""AG-060: approved suite evidence, immutable identities and explicit local audit."""
import json
import sqlite3
import pytest
from phase3_service import Service
from test_generated_execution import http_request
from test_scenario_repository import approved
from agentguard.generation.generator import generate
from agentguard.generation.repository import ScenarioRepository
from agentguard.generation.execution import SuiteRunner

pytestmark=pytest.mark.integration

@pytest.fixture
def baseline_input(tmp_path):
    with Service() as service:
        scenarios=ScenarioRepository(tmp_path/'scenarios.sqlite')
        case=generate(http_request(service)).cases[0]
        snapshot=approved(scenarios,case)
        execution=SuiteRunner().run(scenarios,snapshot,base_dir=tmp_path)
        assert execution.passed
        yield scenarios,execution,{case.id:'a'*64}

def create(tmp_path,inputs):
    from agentguard.regression.baseline import BaselineRepository
    repo=BaselineRepository(tmp_path/'baselines.sqlite')
    scenarios,execution,artifacts=inputs
    record=repo.create('release',scenarios,execution,artifacts=artifacts,actor='operator',reason='Verified initial version')
    return repo,record

def test_baseline_requires_approval_before_promotion_and_audits_actor(tmp_path,baseline_input):
    repo,record=create(tmp_path,baseline_input)
    scenarios=baseline_input[0]
    with pytest.raises(ValueError):repo.promote(record.fingerprint,scenarios=scenarios,actor='op',reason='not approved',expected_active=None)
    repo.approve(record.fingerprint,scenarios=scenarios,actor='reviewer',reason='Independent oracle review')
    repo.promote(record.fingerprint,scenarios=scenarios,actor='release-owner',reason='Initial baseline',expected_active=None)
    assert repo.active('release').fingerprint==record.fingerprint
    assert [row['action'] for row in repo.history(record.fingerprint)]==['create','approve','promote']
    assert all(row.get('timestamp','').endswith('+00:00') for row in repo.history(record.fingerprint))
    repo.retire(record.fingerprint,actor='release-owner',reason='Withdrawn')
    with pytest.raises(ValueError):repo.active('release')
    with pytest.raises(ValueError):repo.promote(record.fingerprint,scenarios=scenarios,actor='op',reason='retired',expected_active=None)

def test_missing_actor_reason_and_stale_promotion_are_rejected(tmp_path,baseline_input):
    repo,record=create(tmp_path,baseline_input)
    scenarios=baseline_input[0]
    with pytest.raises(ValueError):repo.approve(record.fingerprint,scenarios=scenarios,actor='',reason='test')
    with pytest.raises(ValueError):repo.approve(record.fingerprint,scenarios=scenarios,actor='op',reason=' ')
    repo.approve(record.fingerprint,scenarios=scenarios,actor='op',reason='reviewed')
    with pytest.raises(ValueError):repo.promote(record.fingerprint,scenarios=scenarios,actor='op',reason='stale writer',expected_active='0'*64)
    assert len(repo.history(record.fingerprint))==2

def test_freeze_rejects_missing_failed_or_stale_suite_evidence(tmp_path,baseline_input):
    from agentguard.regression.baseline import BaselineRepository
    scenarios,execution,artifacts=baseline_input
    repo=BaselineRepository(tmp_path/'baselines.sqlite')
    missing=execution.model_copy(deep=True);missing.cases=[]
    with pytest.raises(ValueError):repo.create('release',scenarios,missing,artifacts=artifacts,actor='op',reason='missing')
    pin=execution.snapshot.cases[0];scenarios.retire(pin['id'],pin['revision'])
    with pytest.raises(ValueError):repo.create('release',scenarios,execution,artifacts=artifacts,actor='op',reason='stale')

def test_baseline_payload_and_audit_tampering_are_detected(tmp_path,baseline_input):
    repo,record=create(tmp_path,baseline_input)
    record.execution['cases']=[]
    assert repo.get(record.fingerprint).execution['cases']
    with sqlite3.connect(repo.path) as db:
        payload=json.loads(db.execute('SELECT payload FROM baselines').fetchone()[0])
        payload['artifacts'][next(iter(payload['artifacts']))]='b'*64
        db.execute('UPDATE baselines SET payload=?',(json.dumps(payload),))
    with pytest.raises(ValueError):repo.get(record.fingerprint)

def test_immutable_manifest_rejects_future_schema_and_changed_runtime(tmp_path,baseline_input):
    from agentguard.regression.baseline import FrozenBaseline
    repo,record=create(tmp_path,baseline_input)
    data=record.model_dump(mode='json');data['schema_version']=2
    with pytest.raises(ValueError):FrozenBaseline.model_validate(data)
    data=record.model_dump(mode='json');data['runtimes'][next(iter(data['runtimes']))]['policy']['allow_network']=True
    with pytest.raises(ValueError):FrozenBaseline.model_validate(data)


def test_baseline_cannot_be_approved_or_promoted_after_suite_retirement(tmp_path,baseline_input):
    repo,record=create(tmp_path,baseline_input)
    scenarios=baseline_input[0];pin=record.snapshot.cases[0]
    scenarios.retire(pin['id'],pin['revision'])
    with pytest.raises(ValueError):repo.approve(record.fingerprint,scenarios=scenarios,actor='op',reason='stale')
    assert len(repo.history(record.fingerprint))==1


def test_audit_and_database_sequence_tampering_are_detected(tmp_path,baseline_input):
    repo,record=create(tmp_path,baseline_input)
    with sqlite3.connect(repo.path) as db:
        db.execute('UPDATE baseline_events SET seq=100')
    with pytest.raises(ValueError):repo.get(record.fingerprint)


def test_explicit_promotion_supersedes_old_baseline_without_mutating_it(tmp_path,baseline_input):
    repo,first=create(tmp_path,baseline_input)
    scenarios,execution,artifacts=baseline_input
    repo.approve(first.fingerprint,scenarios=scenarios,actor='op',reason='approved')
    repo.promote(first.fingerprint,scenarios=scenarios,actor='op',reason='initial',expected_active=None)
    second=repo.create('release',scenarios,execution,artifacts={key:'b'*64 for key in artifacts},actor='op',reason='New declared remote version')
    repo.approve(second.fingerprint,scenarios=scenarios,actor='op',reason='approved')
    repo.promote(second.fingerprint,scenarios=scenarios,actor='op',reason='upgrade',expected_active=first.fingerprint)
    assert repo.active('release').fingerprint==second.fingerprint
    assert repo.get(first.fingerprint)==first
    assert repo.history(first.fingerprint)[-1]['action']=='supersede'
    repo.retire(first.fingerprint,actor='op',reason='old')
    assert repo.active('release').fingerprint==second.fingerprint


def test_baseline_cli_uses_explicit_review_and_preserves_existing_reports(tmp_path,baseline_input):
    from typer.testing import CliRunner
    from agentguard.cli.main import app
    from agentguard.generation.io import write_execution
    scenarios,execution,artifacts=baseline_input
    evidence=tmp_path/'execution.json';write_execution(execution,evidence)
    pins=tmp_path/'artifacts.json';pins.write_text(json.dumps(artifacts))
    runner=CliRunner();options=['--db',str(tmp_path/'baselines.sqlite'),'--scenario-db',str(scenarios.path),'--actor','operator','--reason','Reviewed candidate']
    result=runner.invoke(app,['baseline','create','release',str(evidence),'--artifacts',str(pins),*options])
    assert result.exit_code==0,result.output
    fingerprint=json.loads(result.output)['fingerprint']
    assert runner.invoke(app,['baseline','promote',fingerprint,*options]).exit_code==2
    assert runner.invoke(app,['baseline','approve',fingerprint,*options]).exit_code==0
    assert runner.invoke(app,['baseline','promote',fingerprint,*options]).exit_code==0
    view=runner.invoke(app,['baseline','show','release','--db',str(tmp_path/'baselines.sqlite')])
    assert json.loads(view.output)['fingerprint']==fingerprint


def test_repository_operations_close_connections_without_waiting_for_gc(tmp_path,baseline_input,monkeypatch):
    from agentguard.regression.baseline import BaselineRepository
    original=sqlite3.connect;opened=set()
    class TrackingConnection(sqlite3.Connection):
        def close(self):
            opened.discard(id(self));return super().close()
    def connect(*args,**kwargs):
        result=original(*args,factory=TrackingConnection,**kwargs);opened.add(id(result));return result
    monkeypatch.setattr(sqlite3,'connect',connect)
    scenarios,execution,artifacts=baseline_input
    repo=BaselineRepository(tmp_path/'connections.sqlite')
    record=repo.create('release',scenarios,execution,artifacts=artifacts,actor='op',reason='test cleanup')
    repo.get(record.fingerprint);scenarios.check_snapshot(execution.snapshot)
    assert not opened
