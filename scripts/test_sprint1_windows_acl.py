"""Run explicitly on Windows CI only; every fixture is new and synthetic."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

import pytest
from scripts import sprint1_verification_support as support


@pytest.fixture
def directory():
    with tempfile.TemporaryDirectory(prefix='agentguard-acl-regression-') as value:
        yield Path(value)


def acl_metadata(path):
    script=r"""$ErrorActionPreference='Stop';$s=[System.Security.Principal.WindowsIdentity]::GetCurrent().User;
    $a=Get-Acl -LiteralPath $env:AGENTGUARD_FIXTURE_DIRECTORY;
    $r=$a.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]);
    [pscustomobject]@{OwnerMatches=($a.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -eq $s.Value);Protected=$a.AreAccessRulesProtected;RuleCount=$r.Count;OwnerOnly=($r.Count -eq 1 -and $r[0].IdentityReference.Value -eq $s.Value -and $r[0].AccessControlType -eq 'Allow' -and $r[0].FileSystemRights -eq 'FullControl');Inherited=($r.Count -eq 1 -and $r[0].IsInherited)}|ConvertTo-Json
    """
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],env=support._powershell_environment(path),capture_output=True,timeout=30)
    assert result.returncode==0
    return json.loads(result.stdout)


def test_real_owner_only_acl_replaces_inherited_rules_and_children_inherit(directory):
    fixture=directory/"empty ' literal space";fixture.mkdir()
    assert support.secure_fixture_directory(fixture)==fixture.resolve()
    meta=acl_metadata(fixture)
    assert meta==dict(OwnerMatches=True,Protected=True,RuleCount=1,OwnerOnly=True,Inherited=False)
    child=fixture/'child';child.mkdir()
    assert acl_metadata(child)==dict(OwnerMatches=True,Protected=False,RuleCount=1,OwnerOnly=True,Inherited=True)


def test_incompatible_inherited_security_module_does_not_break_native_acl(directory,monkeypatch):
    module=directory/'modules'/'Microsoft.PowerShell.Security';module.mkdir(parents=True)
    (module/'Microsoft.PowerShell.Security.psd1').write_text("@{RootModule='Synthetic.psm1';ModuleVersion='99.0';PowerShellVersion='3.0';FunctionsToExport=@('Set-Acl','Get-Acl')}\n")
    (module/'Synthetic.psm1').write_text("throw [System.PlatformNotSupportedException]::new('SYNTHETIC_MODULE')\nfunction Set-Acl {}; function Get-Acl {}\n")
    inherited=str(module.parent)+';'+os.environ.get('PSModulePath','')
    monkeypatch.setenv('PSMODULEPATH',inherited)
    # Actual Windows module autoload fails without the child-environment fix.
    script="try {$ErrorActionPreference='Stop'; Get-Acl -LiteralPath $env:AGENTGUARD_FIXTURE_DIRECTORY|Out-Null;exit 0} catch {exit 41}"
    contaminated=dict(os.environ,AGENTGUARD_FIXTURE_DIRECTORY=str(directory))
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],env=contaminated,capture_output=True,timeout=30)
    assert result.returncode==41
    fixture=directory/'fixture';fixture.mkdir()
    support.secure_fixture_directory(fixture)
    assert acl_metadata(fixture)['OwnerOnly']
    assert os.environ['PSMODULEPATH']==inherited


def test_real_set_acl_failure_leaves_fixture_empty(directory,monkeypatch):
    fixture=directory/'fixture';fixture.mkdir()
    original=support.subprocess.run
    def missing(*args,**kwargs):
        kwargs['env']=dict(kwargs['env'],AGENTGUARD_FIXTURE_DIRECTORY=str(directory/'not-created'))
        return original(*args,**kwargs)
    monkeypatch.setattr(support.subprocess,'run',missing)
    with pytest.raises(support.FixturePermissionError) as caught:support.secure_fixture_directory(fixture)
    assert caught.value.exit_code==41 and caught.value.stage=='set_acl'
    assert not list(fixture.iterdir())


@pytest.mark.parametrize('which',['inheritance','additional_principal','wrong_owner_result'])
def test_verification_fails_closed_for_unsafe_acl(directory,monkeypatch,which):
    fixture=directory/'fixture';fixture.mkdir()
    original=support.subprocess.run
    def tampered(args,**kwargs):
        args=list(args);script=args[-1]
        if which=='inheritance':script=script.replace('SetAccessRuleProtection($true,$false)','SetAccessRuleProtection($false,$true)')
        elif which=='additional_principal':script=script.replace("$stage='set_acl'",r"$fixtureAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new([System.Security.Principal.SecurityIdentifier]::new('S-1-1-0'),'ReadAndExecute','Allow'));$stage='set_acl'")
        else:script=script.replace("$stage='verify_owner'",r"$actual.SetOwner([System.Security.Principal.SecurityIdentifier]::new('S-1-1-0'));$stage='verify_owner'")
        args[-1]=script
        return original(args,**kwargs)
    monkeypatch.setattr(support.subprocess,'run',tampered)
    with pytest.raises(support.FixturePermissionError) as caught:support.secure_fixture_directory(fixture)
    assert caught.value.category=='acl_mismatch'
    assert not list(fixture.iterdir())
