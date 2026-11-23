"""Run explicitly on Windows CI only; every fixture is new and synthetic."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import ctypes
from ctypes import wintypes

import pytest
from scripts import sprint1_verification_support as support


@pytest.fixture
def directory():
    with tempfile.TemporaryDirectory(prefix='agentguard-acl-regression-') as value:
        yield Path(value)


def acl_metadata(path):
    script=r"""$ErrorActionPreference='Stop';$identity=[System.Security.Principal.WindowsIdentity]::GetCurrent();$s=$identity.User;
    $a=Get-Acl -LiteralPath $env:AGENTGUARD_FIXTURE_DIRECTORY;
    $r=$a.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier]);
    [pscustomobject]@{OwnerMatches=($a.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -eq $s.Value);DefaultOwnerMatches=($a.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -eq $identity.Owner.Value);Protected=$a.AreAccessRulesProtected;RuleCount=$r.Count;OwnerOnly=($r.Count -eq 1 -and $r[0].IdentityReference.Value -eq $s.Value -and $r[0].AccessControlType -eq 'Allow' -and $r[0].FileSystemRights -eq 'FullControl');Inherited=($r.Count -eq 1 -and $r[0].IsInherited)}|ConvertTo-Json
    """
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],env=support._powershell_environment(path),capture_output=True,timeout=30)
    assert result.returncode==0
    return json.loads(result.stdout)


def assert_child_contract(metadata):
    # DACL inheritance does not inherit the parent's owner. A different owner is
    # expected only when it matches the creating token's default owner. Neither
    # ownership observation substitutes for checking the exact inherited ACE.
    assert {key:metadata[key] for key in ('Protected','RuleCount','OwnerOnly','Inherited')}==dict(
        Protected=False,RuleCount=1,OwnerOnly=True,Inherited=True)
    assert metadata['DefaultOwnerMatches'] is True


def test_real_owner_only_acl_replaces_inherited_rules_and_children_inherit(directory):
    fixture=directory/"empty ' literal space";fixture.mkdir()
    assert support.secure_fixture_directory(fixture)==fixture.resolve()
    meta=acl_metadata(fixture)
    meta.pop('DefaultOwnerMatches')  # Root owner must be User, regardless of TokenOwner.
    assert meta==dict(OwnerMatches=True,Protected=True,RuleCount=1,OwnerOnly=True,Inherited=False)
    child=fixture/'child';child.mkdir()
    assert_child_contract(acl_metadata(child))


def test_ci_child_owner_difference_does_not_replace_dacl_security_check(directory,monkeypatch):
    # Reproduce the observed CI boolean on a new local directory without changing
    # an account, token, actual owner or permission. Native checks still run.
    original=acl_metadata
    def ci_metadata(path):
        value=original(path)
        if path.name=='child':value['OwnerMatches']=False
        return value
    monkeypatch.setattr(__import__(__name__,fromlist=['acl_metadata']),'acl_metadata',ci_metadata)
    test_real_owner_only_acl_replaces_inherited_rules_and_children_inherit(directory)


@pytest.mark.parametrize('change',[
    {'Protected':True},{'RuleCount':2},{'OwnerOnly':False},{'Inherited':False},{'DefaultOwnerMatches':False},
])
def test_child_contract_rejects_unsafe_dacl_or_unexpected_owner(change):
    metadata=dict(OwnerMatches=False,DefaultOwnerMatches=True,Protected=False,RuleCount=1,OwnerOnly=True,Inherited=True)
    metadata.update(change)
    with pytest.raises(AssertionError):assert_child_contract(metadata)


def access_denied_to_world_sid(path):
    """Native Authz check against the real DACL, without logon or impersonation.

    World is a synthetic unauthorized principal, not a second real OS account.
    This tests discretionary list/read access; privileged-owner attacks are outside
    this claim. No SID or security-descriptor bytes leave this function.
    """
    P=ctypes.c_void_p;D=wintypes.DWORD
    class Luid(ctypes.Structure):_fields_=[('low',D),('high',wintypes.LONG)]
    class Request(ctypes.Structure):
        _fields_=[('desired',D),('self_sid',P),('types',P),('type_count',D),('optional',P)]
    class Reply(ctypes.Structure):
        _fields_=[('count',D),('granted',ctypes.POINTER(D)),('sacl',ctypes.POINTER(D)),('errors',ctypes.POINTER(D))]
    authz=ctypes.WinDLL('authz',use_last_error=True)
    adv=ctypes.WinDLL('advapi32',use_last_error=True)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    adv.GetFileSecurityW.argtypes=[wintypes.LPCWSTR,D,P,D,ctypes.POINTER(D)];adv.GetFileSecurityW.restype=wintypes.BOOL
    adv.ConvertStringSidToSidW.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(P)];adv.ConvertStringSidToSidW.restype=wintypes.BOOL
    authz.AuthzInitializeResourceManager.argtypes=[D,P,P,P,wintypes.LPCWSTR,ctypes.POINTER(P)];authz.AuthzInitializeResourceManager.restype=wintypes.BOOL
    authz.AuthzInitializeContextFromSid.argtypes=[D,P,P,P,Luid,P,ctypes.POINTER(P)];authz.AuthzInitializeContextFromSid.restype=wintypes.BOOL
    authz.AuthzAccessCheck.argtypes=[D,P,ctypes.POINTER(Request),P,P,P,D,ctypes.POINTER(Reply),P];authz.AuthzAccessCheck.restype=wintypes.BOOL
    authz.AuthzFreeContext.argtypes=[P];authz.AuthzFreeResourceManager.argtypes=[P]
    kernel.LocalFree.argtypes=[P];kernel.LocalFree.restype=P
    needed=D();adv.GetFileSecurityW(str(path),5,None,0,ctypes.byref(needed))
    assert needed.value>0
    descriptor=ctypes.create_string_buffer(needed.value)
    assert adv.GetFileSecurityW(str(path),5,descriptor,len(descriptor),ctypes.byref(needed))
    sid=P();manager=P();context=P()
    try:
        assert adv.ConvertStringSidToSidW('S-1-1-0',ctypes.byref(sid))
        assert authz.AuthzInitializeResourceManager(1,None,None,None,'Synthetic ACL test',ctypes.byref(manager))
        # AUTHZ_SKIP_TOKEN_GROUPS prevents account/group lookup; no privileges are granted.
        assert authz.AuthzInitializeContextFromSid(2,sid,manager,None,Luid(),None,ctypes.byref(context))
        granted=D();error=D();reply=Reply(1,ctypes.pointer(granted),None,ctypes.pointer(error))
        assert authz.AuthzAccessCheck(0,context,ctypes.byref(Request(1,None,None,0,None)),None,descriptor,None,0,ctypes.byref(reply),None)
        return error.value==5 and granted.value==0
    finally:
        if context.value:authz.AuthzFreeContext(context)
        if manager.value:authz.AuthzFreeResourceManager(manager)
        if sid.value:kernel.LocalFree(sid)


def test_root_and_child_native_authorization_denies_untrusted_principal(directory):
    root=directory/'fixture';root.mkdir();support.secure_fixture_directory(root)
    child=root/'child';child.mkdir()
    assert access_denied_to_world_sid(root)
    assert access_denied_to_world_sid(child)


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
