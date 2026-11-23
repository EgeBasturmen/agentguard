"""Portable fail-closed diagnostic and child-environment contracts."""
import subprocess
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from scripts import sprint1_verification_support as support


def test_child_module_path_is_removed_case_insensitively_without_parent_mutation(monkeypatch,tmp_path):
    monkeypatch.setenv('PSMODULEPATH','SYNTHETIC_INCOMPATIBLE_MODULES')
    parent=dict(support.os.environ)
    child=support._powershell_environment(tmp_path)
    assert not any(key.casefold()=='psmodulepath' for key in child)
    assert dict(support.os.environ)==parent
    assert child['AGENTGUARD_FIXTURE_DIRECTORY']==str(tmp_path)


@pytest.mark.parametrize('diagnostic,stage,category',[
    (b'AG_ACL|set_acl|module_autoload\r\n','set_acl','module_autoload'),
    (b'AG_ACL|set_acl|access_denied\r\n','set_acl','access_denied'),
    (b'AG_ACL|verify_rules|acl_mismatch\r\n','verify_rules','acl_mismatch'),
    (b'UNTRUSTED_SECRET_LOCAL_PATH\r\n','powershell','subprocess_error'),
    (b'AG_ACL|UNTRUSTED_SECRET|acl_mismatch\r\n','powershell','subprocess_error'),
])
def test_failure_diagnostics_are_allowlisted_and_never_echo_stderr(monkeypatch,tmp_path,diagnostic,stage,category):
    monkeypatch.setattr(support.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=41,stdout=b'',stderr=diagnostic))
    with pytest.raises(support.FixturePermissionError) as caught:support._windows_acl(tmp_path)
    assert caught.value.exit_code==41
    assert caught.value.stage==stage and caught.value.category==category
    assert 'UNTRUSTED' not in str(caught.value) and str(tmp_path) not in str(caught.value)


def test_zero_exit_without_success_receipt_is_not_accepted(monkeypatch,tmp_path):
    monkeypatch.setattr(support.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=b'',stderr=b''))
    with pytest.raises(support.FixturePermissionError):support._windows_acl(tmp_path)


@pytest.mark.parametrize('error,category',[(FileNotFoundError('SYNTHETIC_SECRET'),'engine_unavailable'),(subprocess.TimeoutExpired('SYNTHETIC_SECRET',30),'timeout')])
def test_engine_errors_do_not_echo_exception_or_command(monkeypatch,tmp_path,error,category):
    def fail(*args,**kwargs):raise error
    monkeypatch.setattr(support.subprocess,'run',fail)
    with pytest.raises(support.FixturePermissionError) as caught:support._windows_acl(tmp_path)
    assert caught.value.category==category
    assert 'SYNTHETIC_SECRET' not in str(caught.value)


@pytest.mark.parametrize('script',['check_sprint1_restore.py','check_sprint1_review_ui.py'])
def test_probe_acl_failure_prints_only_safe_metadata_without_traceback(script,tmp_path):
    scripts=Path(__file__).resolve().parents[1]/'scripts'
    code="""import sys,runpy,types
sys.path.insert(0,sys.argv[1])
# The failure is before browser use; do not require an optional browser package.
p=types.ModuleType('playwright');a=types.ModuleType('playwright.sync_api');a.sync_playwright=None;a.expect=None
sys.modules['playwright']=p;sys.modules['playwright.sync_api']=a
import sprint1_verification_support as support
def denied(path):raise support.FixturePermissionError('set_acl','access_denied',41)
support.secure_fixture_directory=denied
target=sys.argv[2];result=sys.argv[3];sys.argv=[target,'--result',result]
runpy.run_path(target,run_name='__main__')
"""
    output=tmp_path/'must-not-exist.json'
    result=subprocess.run([sys.executable,'-B','-c',code,str(scripts),str(scripts/script),str(output)],capture_output=True,timeout=30)
    assert result.returncode==2
    assert result.stderr==b''
    assert json.loads(result.stdout)==dict(status='failed',stage='set_acl',category='access_denied',exit_code=41)
    assert not output.exists()
