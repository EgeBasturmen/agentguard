"""Phase 4 red tests: do not infer truth from traces or manufacture release approval."""
import pytest

def request():
    from agentguard.generation.models import GenerationRequest
    return GenerationRequest.model_validate({'task':'Set flag to ready','fixture':{'flag':'draft','protected':'keep'},
        'runtime':{'agent':{'transport':'http','endpoint':{'base_url':'https://example.com/agent'}},
            'environment':{'kind':'http','endpoint':{'base_url':'https://example.com'},'allow_staging_writes':True},'tools':[],'policy':{}},
        'rules':[{'id':'r1','version':'1','text':'expect flag = "ready"'},{'id':'r2','version':'1','text':'unchanged protected'}]})

def test_rules_generate_real_proposals_with_bound_provenance():
    from agentguard.generation.generator import generate
    result=generate(request())
    assert result.cases and all(case.fingerprint for case in result.cases)
    assert any(case.axis=='adversarial' for case in result.cases)
    assert all(case.provenance and case.oracle_checks for case in result.cases)

def test_trace_result_is_not_an_automatic_oracle():
    from agentguard.generation.generator import generate
    req=request().model_copy(update={'rules':[],'traces':[{'id':'incident-1','version':'1','task':'do work','output':'PASS','after_state':{'flag':'ready'}}]})
    case=generate(req).cases[0]
    assert case.unresolved
    from agentguard.generation.validation import validate_candidate
    assert not validate_candidate(case).ready

def test_unknown_natural_language_is_retained_as_unresolved():
    from agentguard.generation.generator import generate
    req=request().model_copy(update={'rules':[{'id':'r','version':'1','text':'Give the customer an appropriate result'}]})
    case=generate(req).cases[0]
    assert case.unresolved

@pytest.mark.integration
def test_draft_cannot_enter_frozen_release_suite(tmp_path):
    from agentguard.generation.generator import generate
    from agentguard.generation.repository import ScenarioRepository
    repo=ScenarioRepository(tmp_path/'scenarios.sqlite')
    case=generate(request()).cases[0];repo.add(case)
    with pytest.raises(ValueError): repo.freeze('release',[(case.id,case.revision)])

def test_conflicting_rules_are_not_silently_deduplicated():
    from agentguard.generation.generator import generate
    from agentguard.generation.validation import validate_candidate
    req=request().model_copy(update={'rules':[{'id':'a','version':'1','text':'expect flag = 1'},{'id':'b','version':'1','text':'expect flag = 2'}]})
    case=generate(req).cases[0]
    check=validate_candidate(case)
    assert not check.ready and any('conflict' in issue.lower() for issue in check.issues)


def test_boolean_and_integer_oracles_are_conflicting_not_deduplicated():
    from agentguard.generation.models import GenerationRequest
    from agentguard.generation.generator import generate
    from agentguard.generation.validation import validate_candidate
    data=request().model_dump();data['rules']=[{'id':'bool','text':'expect flag = true'},{'id':'number','text':'expect flag = 1'}]
    case=generate(GenerationRequest.model_validate(data)).cases[0]
    assert not validate_candidate(case).ready


def test_invalid_rule_path_remains_an_unresolved_draft():
    from agentguard.generation.models import GenerationRequest
    from agentguard.generation.generator import generate
    data=request().model_dump();data['rules']=[{'id':'invalid','text':'expect x..y = 1'}]
    case=generate(GenerationRequest.model_validate(data)).cases[0]
    assert case.unresolved


def test_boundary_generation_is_bounded_even_for_large_schema_minimums():
    from agentguard.generation.models import GenerationRequest
    from agentguard.generation.generator import generate
    data=request().model_dump();data['contracts']=[{'name':'input.test','version':'1.0.0','adapter':'custom@1.0.0',
        'input_schema':{'type':'object','properties':{'blob':{'type':'string','minLength':10000},'n':{'type':'integer','minimum':0}},'required':['blob','n']}}]
    batch=generate(GenerationRequest.model_validate(data))
    assert all(len(case.observations.get('arguments',{}).get('blob',''))<=128 for case in batch.cases)
    assert batch.gaps


def test_generation_is_reproducible_and_records_configuration():
    from agentguard.generation.generator import generate
    from agentguard.core.manifest import digest
    req=request()
    first=generate(req);second=generate(req)
    assert first==second
    record=first.cases[0].provenance[0]
    assert record['request_fingerprint']==digest(req.model_dump(mode='json'))
    assert record['configuration']=={'seed':0,'max_cases':100,'risk_packs':['instruction_override','schema_boundary']}


def test_duplicate_oracles_keep_both_sources_and_only_one_assertion():
    from agentguard.generation.generator import generate
    from agentguard.generation.models import GenerationRequest
    data=request().model_dump();data['rules']=[{'id':'a','text':'expect flag = "ready"'},{'id':'b','text':'expect flag = "ready"'}]
    case=generate(GenerationRequest.model_validate(data)).cases[0]
    assert len(case.scenario['expected_state'])==1
    assert list(case.oracle_sources.values())==[['rule:a','rule:b']]


@pytest.mark.parametrize('name,content',[
    ('duplicate.json','{"task":"a","task":"b"}'),
    ('duplicate.yaml','task: a\ntask: b\n'),
    ('alias.yaml','x: &data [1]\ny: *data\n'),
])
def test_ambiguous_imports_are_rejected(tmp_path,name,content):
    from agentguard.generation.io import read_document
    path=tmp_path/name;path.write_text(content)
    with pytest.raises(ValueError):read_document(path)


def test_observed_http_agent_cannot_claim_controlled_tool_capabilities():
    from agentguard.generation.models import GenerationRequest
    from agentguard.generation.generator import generate
    from agentguard.generation.validation import validate_candidate
    data=request().model_dump();data['rules'].append({'id':'isolation','text':'require supports_isolation'})
    check=validate_candidate(generate(GenerationRequest.model_validate(data)).cases[0])
    assert not check.ready and any('Missing capability' in issue for issue in check.issues)


def test_pinned_contract_drift_distinguishes_boolean_and_integer(tmp_path):
    from copy import deepcopy
    from agentguard.integrations.plan import IntegrationPlan,run_integration_plan
    from agentguard.generation.generator import generate
    from agentguard.integrations.tools import ToolContract
    contract=ToolContract(name='choice',version='1.0.0',adapter='custom@1.0.0',
        input_schema={'type':'object','properties':{'choice':{'enum':[True]}},'required':['choice']})
    plan=generate(request()).cases[0].plan()
    plan['tools']=[{'connector':'mock','contract':contract.model_dump()}]
    expected=deepcopy(contract.model_dump());expected['input_schema']['properties']['choice']['enum']=[1]
    with pytest.raises(ValueError,match='Pinned generation contract'):
        run_integration_plan(IntegrationPlan.model_validate(plan),base_dir=tmp_path,preflight_only=True,expected_contracts=[expected])


def test_generation_budget_limits_materialization_not_only_final_output(monkeypatch):
    from agentguard.generation import generator
    from agentguard.generation.models import GenerationRequest,Candidate
    data=request().model_dump()
    data['contracts']=[dict(name='tool'+str(index),version='1.0.0',adapter='custom@1.0.0',
        input_schema={'type':'object','properties':{str(n):{'type':'integer','minimum':0,'maximum':1} for n in range(51)}}) for index in range(2)]
    count=0
    def bounded(*args,**kwargs):
        nonlocal count
        count+=1
        assert count<=200,'Generator materialized more than the hard proposal budget'
        return Candidate(*args,**kwargs)
    bounded.model_validate=Candidate.model_validate
    monkeypatch.setattr(generator,'Candidate',bounded)
    batch=generator.generate(GenerationRequest.model_validate(data))
    assert batch.gaps
