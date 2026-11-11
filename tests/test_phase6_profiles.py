import json
import xml.etree.ElementTree as ET
import pytest
from test_phase6_baseline import baseline_input
from test_phase6_repeats import batches

pytestmark=pytest.mark.integration

def test_profile_cannot_remove_critical_or_release_coverage(baseline_input):
    from agentguard.regression.profiles import SuiteProfile,select_profile
    from agentguard.regression.repeats import RepeatPolicy
    scenarios,execution,_=baseline_input
    with pytest.raises(ValueError):select_profile(scenarios,execution.snapshot,SuiteProfile(name='release',case_ids=[]),RepeatPolicy())
    with pytest.raises(ValueError):select_profile(scenarios,execution.snapshot,SuiteProfile(name='pr',case_ids=['invented']),RepeatPolicy())
    selected=select_profile(scenarios,execution.snapshot,SuiteProfile(name='release'),RepeatPolicy())
    assert selected.snapshot==execution.snapshot and selected.excluded_cases==[]

def test_product_report_surfaces_have_same_gate_without_raw_evidence(tmp_path,baseline_input):
    from agentguard.regression.profiles import export_reports
    from agentguard.regression.repeats import compare_repeats,RepeatBatch
    old,new=batches(baseline_input,count=1)
    raw=new.model_dump(mode='json');raw.pop('fingerprint')
    execution=raw['attempts'][0]['execution'];execution['cases'][0]['result']['cleanup_status']='unverified'
    execution['passed']=False;execution['gate_decision']='BLOCK'
    result=compare_repeats(old,RepeatBatch.model_validate(raw))
    summary=export_reports(result,tmp_path/'report')
    assert summary['gate_decision']=='BLOCK'
    assert json.loads((tmp_path/'report.json').read_text())['gate_decision']=='BLOCK'
    assert 'BLOCK' in (tmp_path/'report.html').read_text()
    xml=ET.parse(tmp_path/'report.xml').getroot()
    assert int(xml.get('failures','0'))+int(xml.get('errors','0'))>0
    assert 'initial_state' not in (tmp_path/'report.json').read_text()
    original=(tmp_path/'report.json').read_bytes()
    with pytest.raises(FileExistsError):export_reports(result,tmp_path/'report')
    assert (tmp_path/'report.json').read_bytes()==original
