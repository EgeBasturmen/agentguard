"""Receipt idempotency on real SQLite transactions. PYTEST_DONT_REWRITE."""
import pytest
from test_s2_dispatch import fixture
from agentguard.management.store import AccessError
pytestmark=pytest.mark.integration

def test_failure_receipt_survives_restart_and_expired_lease(tmp_path):
    from agentguard.management.service import ControlPlane
    s,o,p,c,suite,w,j,case=fixture(tmp_path);claim=s.claim(w['runner_token']);report={'failure':'RUNNER_EXECUTION_ERROR'}
    first=s.finish(w['runner_token'],j['id'],claim['lease'],report)
    other=ControlPlane(s.root);now=s.clock();other.clock=lambda:now+100
    second=other.finish(w['runner_token'],j['id'],claim['lease'],report)
    assert first==second and second['gate_decision']=='BLOCK' and second['delivery_accepted']
    with other.db() as db:
        assert db.execute('SELECT count(*) FROM artifacts').fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM events WHERE job=? AND kind='RUNNER_EXECUTION_ERROR'",(j['id'],)).fetchone()[0]==1

def test_corrupt_historical_result_cannot_acknowledge(tmp_path):
    from test_phase7_results import execute_http_job
    s,o,p,w,j,claim,report=execute_http_job(tmp_path);s.finish(w['runner_token'],j['id'],claim['lease'],report)
    with s.db() as db:db.execute('UPDATE jobs SET seal=? WHERE id=?',('0'*64,j['id']))
    with pytest.raises(AccessError,match='STORED_RESULT_INTEGRITY_ERROR'):s.finish(w['runner_token'],j['id'],claim['lease'],report)

@pytest.mark.parametrize('change',['digest','lease','runner','revoke'])
def test_failure_receipt_rejects_changed_binding(tmp_path,change):
    from test_phase7_queue import runner
    s,o,p,c,suite,w,j,case=fixture(tmp_path);claim=s.claim(w['runner_token']);report={'failure':'RUNNER_EXECUTION_ERROR'}
    s.finish(w['runner_token'],j['id'],claim['lease'],report)
    token=w['runner_token'];lease=claim['lease']
    if change=='digest':report={'failure':'SOURCE_VERSION_MISMATCH'}
    if change=='lease':lease='different'
    if change=='runner':token=runner(s,o,p)['runner_token']
    if change=='revoke':s.revoke_runner(o,p,w['id'])
    with pytest.raises(AccessError):s.finish(token,j['id'],lease,report)
