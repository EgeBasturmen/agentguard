"""Permissions for newly created, test-owned directories; never live state paths."""
import os
from pathlib import Path
import subprocess
import tempfile


class FixturePermissionError(RuntimeError):
    """Allowlisted subprocess diagnosis, without stderr, identities or paths."""
    def __init__(self,stage,category,exit_code=None):
        self.stage=stage;self.category=category;self.exit_code=exit_code
        super().__init__(f'Test fixture permissions unverified: stage={stage}, category={category}, exit_code={exit_code}; no test state initialized')


def _powershell_environment(path):
    # pwsh -> Python -> powershell.exe otherwise inherits incompatible PS7 modules.
    # Windows environment variable names are case-insensitive; plain dicts are not.
    env={key:value for key,value in os.environ.items() if key.casefold()!='psmodulepath'}
    env['AGENTGUARD_FIXTURE_DIRECTORY']=str(path)
    return env


def _windows_acl(path):
    script=r"""
$ErrorActionPreference='Stop'
$stage='identity';$category='powershell_error'
try {
$fixturePath=$env:AGENTGUARD_FIXTURE_DIRECTORY
$fixtureSid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User
$fixtureAcl=[System.Security.AccessControl.DirectorySecurity]::new()
$fixtureAcl.SetOwner($fixtureSid)
$fixtureAcl.SetAccessRuleProtection($true,$false)
$fixtureAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($fixtureSid,'FullControl','ContainerInherit, ObjectInherit','None','Allow'))
$stage='set_acl'
Set-Acl -LiteralPath $fixturePath -AclObject $fixtureAcl
$stage='read_acl'
$actual=Get-Acl -LiteralPath $fixturePath
$stage='verify_owner'
if(-not $actual.AreAccessRulesProtected -or $actual.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $fixtureSid.Value){$category='acl_mismatch';throw 'Unverified ACL'}
$stage='verify_rules'
$rules=$actual.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])
if($rules.Count -ne 1 -or $rules[0].IdentityReference.Value -ne $fixtureSid.Value -or $rules[0].IsInherited -or $rules[0].AccessControlType -ne 'Allow' -or $rules[0].FileSystemRights -ne 'FullControl'){$category='acl_mismatch';throw 'Unverified ACL'}
[Console]::Out.WriteLine('AG_ACL_OK')
exit 0
} catch {
if($_.Exception -is [System.Management.Automation.CommandNotFoundException]){$category='module_autoload'}
elseif($_.Exception -is [System.UnauthorizedAccessException] -or $_.Exception -is [System.Security.SecurityException]){$category='access_denied'}
[Console]::Error.WriteLine('AG_ACL|'+$stage+'|'+$category)
exit 41
}
"""
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],
            env=_powershell_environment(path),capture_output=True,timeout=30)
    except subprocess.TimeoutExpired:
        raise FixturePermissionError('powershell','timeout') from None
    except OSError:
        raise FixturePermissionError('powershell','engine_unavailable') from None
    if result.returncode==0 and result.stdout.strip()==b'AG_ACL_OK' and not result.stderr.strip():
        return
    stage,category='powershell','subprocess_error'
    # Parse a fixed protocol only. Never forward raw or untrusted stderr.
    fields=result.stderr.decode('ascii',errors='replace').strip().split('|')
    if len(fields)==3 and fields[0]=='AG_ACL' and fields[1] in {'identity','set_acl','read_acl','verify_owner','verify_rules'} and fields[2] in {'powershell_error','module_autoload','access_denied','acl_mismatch'}:
        stage,category=fields[1:]
    raise FixturePermissionError(stage,category,result.returncode)


def secure_fixture_directory(path):
    path = Path(path).resolve(strict=True)
    if not path.is_relative_to(Path(tempfile.gettempdir()).resolve()) or path.is_symlink():
        raise ValueError('Only a new test-owned temporary directory is supported')
    if any(path.iterdir()):
        raise ValueError('Fixture directory must initially be empty')
    if os.name == 'nt':
        _windows_acl(path)
    else:
        path.chmod(0o700)
        if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise RuntimeError('Test fixture permissions are unverified')
    return path
