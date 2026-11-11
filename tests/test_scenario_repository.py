"""Review, immutable versions, stale bindings and import/export regression tests."""
from copy import deepcopy
import pytest
from test_generation import request
from agentguard.generation.generator import generate
from agentguard.generation.repository import ScenarioRepository
from agentguard.generation.models import Source,Candidate
from agentguard.generation.validation import validate_candidate


def approved(repo,case):
    repo.add(case)
    repo.review(case.id,case.revision,reviewer='reviewer',fixture_confirmed=True,classification_confirmed=True,
        decisions={identity:'confirmed' for identity in case.oracle_checks})
    repo.approve(case.id,case.revision,reviewer='approver',expected_digest=case.fingerprint)
    return repo.freeze('release',[(case.id,case.revision)])


def test_review_and_approval_are_explicit_separate_steps(tmp_path):
    repo=ScenarioRepository(tmp_path/'r.sqlite');case=generate(request()).cases[0];repo.add(case)
    with pytest.raises(ValueError):repo.approve(case.id,1,reviewer='x',expected_digest=case.fingerprint)
    repo.review(case.id,1,reviewer='human',fixture_confirmed=False,classification_confirmed=True,decisions={k:'confirmed' for k in case.oracle_checks})
    with pytest.raises(ValueError):repo.approve(case.id,1,reviewer='x',expected_digest=case.fingerprint)
    repo.review(case.id,1,reviewer='human',fixture_confirmed=True,classification_confirmed=True,decisions={k:'confirmed' for k in case.oracle_checks})
    repo.approve(case.id,1,reviewer='approver',expected_digest=case.fingerprint)
    frozen=repo.freeze('release',[(case.id,1)])
    stored,record=repo.admission(frozen,frozen.cases[0])
    assert stored==case and record['approver']=='approver'


def test_source_change_marks_approval_stale(tmp_path):
    repo=ScenarioRepository(tmp_path/'r.sqlite');case=generate(request()).cases[0];suite=approved(repo,case)
    source=next(s for s in case.sources if s.kind=='rule')
    data=source.model_dump(exclude={'fingerprint'});data['version']='2';data['content']={'text':'changed','checks':{}}
    repo.sync_sources([Source.model_validate(data)])
    assert repo.get(case.id,1)[1]=='stale'
    with pytest.raises(ValueError):repo.admission(suite,suite.cases[0])


def test_new_version_cannot_inherit_approval(tmp_path):
    repo=ScenarioRepository(tmp_path/'r.sqlite');case=generate(request()).cases[0];suite=approved(repo,case)
    raw=deepcopy(case.scenario);raw['task']='Do a revised task'
    revised=repo.revise(case.id,1,scenario=raw,reason='Clarified task')
    assert revised.revision==2 and repo.get(case.id,2)[1]=='draft'
    with pytest.raises(ValueError):repo.admission(suite,suite.cases[0])
    with pytest.raises(ValueError):repo.freeze('new',[(case.id,2)])


def test_payload_tampering_does_not_keep_content_hash():
    case=generate(request()).cases[0]
    data=case.model_dump();data['scenario']['expected_state'][0]['value']='forged'
    with pytest.raises(ValueError):Candidate.model_validate(data)


@pytest.mark.parametrize('suffix',['json','yaml','csv'])
def test_dataset_roundtrip_never_imports_release_approval(tmp_path,suffix):
    from agentguard.generation.io import export_dataset,import_dataset
    batch=generate(request());case=batch.cases[0]
    repo=ScenarioRepository(tmp_path/'r.sqlite');approved(repo,case)
    path=tmp_path/('cases.'+suffix);export_dataset(batch,path)
    imported=import_dataset(path)
    assert imported.cases==batch.cases
    versions=repo.import_cases([imported.cases[0]])
    assert versions[0].revision==2 and repo.get(case.id,2)[1]=='draft'
    with pytest.raises(ValueError):repo.freeze('imported',[(case.id,2)])
    with pytest.raises(FileExistsError):export_dataset(batch,path)


def test_unknown_oracle_cannot_be_approved_even_with_confirmed_flags(tmp_path):
    data=request().model_dump();data['rules']=[]
    from agentguard.generation.models import GenerationRequest
    case=generate(GenerationRequest.model_validate(data)).cases[0]
    repo=ScenarioRepository(tmp_path/'r.sqlite');repo.add(case)
    with pytest.raises(ValueError):repo.review(case.id,1,reviewer='x',fixture_confirmed=True,classification_confirmed=True,decisions={})


def test_existing_foreign_database_is_not_modified(tmp_path):
    import sqlite3
    path=tmp_path/'foreign.sqlite'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE user_data(value TEXT)')
    with pytest.raises(ValueError):ScenarioRepository(path)
    with sqlite3.connect(path) as db:assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()==[('user_data',)]


def test_rule_csv_import_preserves_source_id_and_version(tmp_path):
    from agentguard.generation.io import import_rules
    path=tmp_path/'rules.csv';path.write_text('id,version,text,uri\nr1,2,unchanged protected,policy.txt\n')
    rule=import_rules(path)[0]
    assert rule.id=='r1' and rule.version=='2' and rule.uri=='policy.txt'


def test_proposals_are_not_executed_coverage():
    from agentguard.generation.coverage import coverage
    batch=generate(request())
    matrix=coverage(batch.cases,batch.requirements)
    assert any(row['proposed'] for row in matrix['rows'])
    assert all(row['executed']==row['passed']==0 for row in matrix['rows'])
    assert matrix['missing_executed']


def test_adversarial_case_does_not_claim_dropped_positive_rule():
    batch=generate(request())
    adversarial=next(case for case in batch.cases if case.axis=='adversarial')
    assert 'rule:r2' in adversarial.requirements and 'rule:r1' not in adversarial.requirements


def test_raw_generated_scenario_does_not_bypass_release_gate():
    from agentguard.core.scenario import Scenario
    from agentguard.core.manifest import digest
    from agentguard.runner.runner import ScenarioRunner
    from agentguard.sandbox.generic import GenericSandbox
    from agentguard.core.outcome import effective_pass
    case=generate(request()).cases[0]
    scenario=Scenario.model_validate(case.scenario)
    scenario.generation=dict(case_id=case.id,revision=1,candidate_fingerprint=case.fingerprint,scenario_fingerprint=digest(scenario.model_dump(exclude={'generation'})))
    class Agent:
        def run(self,task,sandbox):sandbox.state['flag']='ready'
    result=ScenarioRunner(GenericSandbox).run(scenario,Agent())
    assert result.passed
    assert not effective_pass(result) and result.admission_status=='unapproved'


def test_csv_roundtrip_supports_fixtures_within_declared_byte_budget(tmp_path):
    from agentguard.generation.models import GenerationRequest
    from agentguard.generation.io import export_dataset,import_dataset
    data=request().model_dump();data['fixture']['protected']='x'*150000
    batch=generate(GenerationRequest.model_validate(data))
    path=tmp_path/'large-fixture.csv';export_dataset(batch,path)
    assert import_dataset(path).cases==batch.cases
