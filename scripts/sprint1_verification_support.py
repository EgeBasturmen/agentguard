"""Permissions for newly created, test-owned directories; never live state paths."""
import os
from pathlib import Path
import subprocess
import tempfile


def secure_fixture_directory(path):
    path = Path(path).resolve(strict=True)
    if not path.is_relative_to(Path(tempfile.gettempdir()).resolve()) or path.is_symlink():
        raise ValueError('Only a new test-owned temporary directory is supported')
    if any(path.iterdir()):
        raise ValueError('Fixture directory must initially be empty')
    if os.name == 'nt':
        script = r"""
$ErrorActionPreference='Stop'
$fixturePath=$env:AGENTGUARD_FIXTURE_DIRECTORY
$fixtureSid=[System.Security.Principal.WindowsIdentity]::GetCurrent().User
$fixtureAcl=[System.Security.AccessControl.DirectorySecurity]::new()
$fixtureAcl.SetOwner($fixtureSid)
$fixtureAcl.SetAccessRuleProtection($true,$false)
$fixtureAcl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($fixtureSid,'FullControl','ContainerInherit, ObjectInherit','None','Allow'))
Set-Acl -LiteralPath $fixturePath -AclObject $fixtureAcl
$actual=Get-Acl -LiteralPath $fixturePath
if(-not $actual.AreAccessRulesProtected -or $actual.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $fixtureSid.Value){throw 'Fixture ACL unverified'}
$rules=$actual.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])
if($rules.Count -ne 1 -or $rules[0].IdentityReference.Value -ne $fixtureSid.Value -or $rules[0].IsInherited -or $rules[0].AccessControlType -ne 'Allow' -or $rules[0].FileSystemRights -ne 'FullControl'){throw 'Fixture ACL unverified'}
"""
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            env={**os.environ, 'AGENTGUARD_FIXTURE_DIRECTORY': str(path)}, capture_output=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Test fixture ACL setup/verification failed; no state was initialized')
    else:
        path.chmod(0o700)
        if path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077:
            raise RuntimeError('Test fixture permissions are unverified')
    return path
