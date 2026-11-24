"""Local credential publication, not a distributed enrollment transaction.

Only trusted local Windows/Linux filesystems are supported. A fixed, secret-free
guard file coordinates cooperating publishers; a private orphan stage may survive
process death. Existing destinations are never removed, repaired or overwritten.
"""
from contextlib import contextmanager
import ctypes
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid

TOKEN=re.compile(r'ag7r?_[A-Za-z0-9_-]{43}')


class CredentialError(Exception):
    def __init__(self,code,operation_status='not_started',publication_status='absent'):
        self.code=code;self.operation_status=operation_status;self.publication_status=publication_status
        self.recovery_required=operation_status in {'accepted','unknown'}
        super().__init__(code)


def _windows_security(path,mode):
    # No credential bytes enter PowerShell, its argv, output or error diagnostics.
    script=r"""
$ErrorActionPreference='Stop'
try {
$p=$env:AG_CREDENTIAL_PATH;$mode=$env:AG_CREDENTIAL_MODE
$s=[System.Security.Principal.WindowsIdentity]::GetCurrent().User
if($mode -eq 'harden'){
 $item=Get-Item -LiteralPath $p -Force
 if($item.PSIsContainer){$a=[System.Security.AccessControl.DirectorySecurity]::new();$flags='ContainerInherit, ObjectInherit'}
 else {$a=[System.Security.AccessControl.FileSecurity]::new();$flags='None'}
 $a.SetOwner($s);$a.SetAccessRuleProtection($true,$false)
 $a.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new($s,'FullControl',$flags,'None','Allow'))
 Set-Acl -LiteralPath $p -AclObject $a
}
$item=Get-Item -LiteralPath $p -Force
if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'unsafe'}
$a=Get-Acl -LiteralPath $p
$owner=$a.GetOwner([System.Security.Principal.SecurityIdentifier]).Value
$r=$a.GetAccessRules($true,$true,[System.Security.Principal.SecurityIdentifier])
if($mode -eq 'parent'){
 $trusted=@($s.Value,'S-1-5-18','S-1-5-32-544')
 if($owner -notin $trusted){throw 'unsafe'}
 # Reject untrusted write/delete/permission-changing grants; privileged local
 # administrators and SYSTEM remain inside the documented trusted-host boundary.
 foreach($rule in $r){
  if($rule.AccessControlType -eq 'Allow' -and -not ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and $rule.IdentityReference.Value -notin $trusted -and ([int]$rule.FileSystemRights -band 852038)){throw 'unsafe'}
 }
 if($null -eq $a.GetSecurityDescriptorSddlForm('Access') -or $r.Count -eq 0){throw 'unsafe'}
}else{
 if($owner -ne $s.Value -or $r.Count -ne 1 -or $r[0].IdentityReference.Value -ne $s.Value -or $r[0].AccessControlType -ne 'Allow' -or $r[0].FileSystemRights -ne 'FullControl'){throw 'unsafe'}
 if($mode -ne 'read' -and (-not $a.AreAccessRulesProtected -or $r[0].IsInherited)){throw 'unsafe'}
}
[Console]::Out.WriteLine('PRIVATE_OK');exit 0
}catch{[Console]::Error.WriteLine('PRIVATE_UNVERIFIED');exit 41}
"""
    env={k:v for k,v in os.environ.items() if k.casefold()!='psmodulepath'}
    env.update(AG_CREDENTIAL_PATH=str(path),AG_CREDENTIAL_MODE=mode)
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],env=env,capture_output=True,timeout=15)
    except (OSError,subprocess.TimeoutExpired):
        raise CredentialError('CREDENTIAL_PERMISSIONS_UNVERIFIED') from None
    if result.returncode or result.stdout.strip()!=b'PRIVATE_OK' or result.stderr.strip():
        raise CredentialError('CREDENTIAL_PERMISSIONS_UNVERIFIED')


def _path(path):
    path=Path(path).absolute()
    if '..' in path.parts or not path.name or (os.name=='nt' and (path.drive.startswith('\\') or ':' in path.name or path.name.endswith((' ','.')))):
        raise CredentialError('CREDENTIAL_PATH_UNSAFE')
    if os.name=='nt' and any(re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]|CONIN\$|CONOUT\$)',part.split('.')[0],re.I) for part in path.parts[1:]):
        raise CredentialError('CREDENTIAL_PATH_UNSAFE')
    for item in (path,*path.parents):
        if os.path.lexists(item):
            st=item.lstat()
            if stat.S_ISLNK(st.st_mode) or getattr(st,'st_file_attributes',0)&1024:
                raise CredentialError('CREDENTIAL_PATH_UNSAFE')
    return path


def _permissions(path,mode='verify'):
    if os.name=='nt':
        _windows_security(path,mode)
    else:
        st=path.stat()
        unsafe=st.st_mode & (0o022 if mode=='parent' else 0o077)
        if st.st_uid!=os.getuid() or unsafe:
            raise CredentialError('CREDENTIAL_PERMISSIONS_UNVERIFIED')


def _regular(path):
    st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or getattr(st,'st_file_attributes',0)&1024:
        raise CredentialError('CREDENTIAL_PATH_UNSAFE')
    return st


def _parent(path):
    missing=[];current=path
    while not current.exists():missing.append(current);current=current.parent
    _path(current);_permissions(current,'parent')
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)
        if os.name=='nt':_windows_security(directory,'harden')
        _permissions(directory)
    _permissions(path,'parent')


@contextmanager
def _guard(path):
    guard=path.parent/('.agentguard-credential-'+hashlib.sha256(path.name.casefold().encode()).hexdigest()[:24]+'.lock')
    fd=None;locked=False
    try:
        try:
            fd=os.open(guard,os.O_RDWR|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0),0o600)
            if os.name=='nt':_windows_security(guard,'harden')
            os.fsync(fd)
        except FileExistsError:
            _path(guard);_regular(guard);_permissions(guard)
            fd=os.open(guard,os.O_RDWR|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
        _regular(guard);_permissions(guard)
        st=os.fstat(fd)
        if st.st_size not in (0,1) or st.st_ino!=guard.stat().st_ino:raise CredentialError('CREDENTIAL_PUBLICATION_BUSY')
        try:
            if os.name=='nt':
                import msvcrt
                os.lseek(fd,0,0);msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked=True
        except OSError:raise CredentialError('CREDENTIAL_PUBLICATION_BUSY') from None
        yield
    finally:
        if fd is not None:
            try:
                if locked:
                    if os.name=='nt':
                        import msvcrt
                        os.lseek(fd,0,0);msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(fd,fcntl.LOCK_UN)
            finally:os.close(fd)


def _write_secret(fd,data):
    view=memoryview(data)
    while view:
        written=os.write(fd,view)
        if written<=0:raise OSError('Credential write incomplete')
        view=view[written:]
    os.fsync(fd)


def _publish_noreplace(source,target):
    if os.name=='nt':
        from ctypes import wintypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.MoveFileExW.argtypes=[wintypes.LPCWSTR,wintypes.LPCWSTR,wintypes.DWORD]
        kernel.MoveFileExW.restype=wintypes.BOOL
        if not kernel.MoveFileExW(str(source),str(target),8):raise OSError(ctypes.get_last_error(),'Publication failed')
    elif sys.platform.startswith('linux'):
        library=ctypes.CDLL(None,use_errno=True)
        rename=getattr(library,'renameat2',None)
        if rename is None:raise CredentialError('ATOMIC_PUBLICATION_UNSUPPORTED')
        rename.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint]
        rename.restype=ctypes.c_int
        if rename(-100,os.fsencode(source),-100,os.fsencode(target),1):raise OSError(ctypes.get_errno(),'Publication failed')
    else:raise CredentialError('ATOMIC_PUBLICATION_UNSUPPORTED')


def _sync_parent(path):
    if os.name!='nt':
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)


def credential(path):
    try:
        path=_path(path);st=_regular(path);_permissions(path,'read')
        if not 1<=st.st_size<=512:raise CredentialError('CREDENTIAL_INVALID')
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as stream:
            current=os.fstat(stream.fileno())
            if (current.st_dev,current.st_ino)!=(st.st_dev,st.st_ino):raise CredentialError('CREDENTIAL_PATH_UNSAFE')
            value=stream.read(513).decode('ascii').strip()
        if not TOKEN.fullmatch(value):raise CredentialError('CREDENTIAL_INVALID')
        return value
    except CredentialError:raise
    except (OSError,UnicodeError):raise CredentialError('CREDENTIAL_UNVERIFIED') from None


def private_file(path,operation):
    status='not_started';publication='absent';stage=None;fd=None
    try:
        path=_path(path)
        if os.path.lexists(path):raise CredentialError('CREDENTIAL_TARGET_EXISTS')
        _parent(path.parent)
        with _guard(path):
            if os.path.lexists(path):raise CredentialError('CREDENTIAL_TARGET_EXISTS')
            stage=path.parent/('.agentguard-private-'+uuid.uuid4().hex)
            stage.mkdir(mode=0o700)
            if os.name=='nt':_windows_security(stage,'harden')
            _permissions(stage)
            temporary=stage/'credential'
            fd=os.open(temporary,os.O_RDWR|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0),0o600)
            if os.name=='nt':_windows_security(temporary,'harden')
            _permissions(temporary);_regular(temporary)
            status='unknown'
            result=operation()
            status='accepted'
            if not isinstance(result,dict):raise CredentialError('CREDENTIAL_RESPONSE_UNVERIFIED')
            token=result.get('token',result.get('runner_token'))
            if not isinstance(token,str) or not TOKEN.fullmatch(token):raise CredentialError('CREDENTIAL_RESPONSE_UNVERIFIED')
            metadata={key:value for key,value in result.items() if key in {'id','key_id','project','workspace','user'}}
            if any(re.search(r'ag7[er]?_[A-Za-z0-9_-]{43}',str(value)) for value in metadata.values()):
                raise CredentialError('CREDENTIAL_RESPONSE_UNVERIFIED')
            _write_secret(fd,token.encode('ascii'))
            os.lseek(fd,0,0)
            if os.read(fd,513)!=token.encode('ascii'):raise CredentialError('CREDENTIAL_WRITE_UNVERIFIED')
            os.close(fd);fd=None
            _permissions(temporary);_regular(temporary)
            _publish_noreplace(temporary,path)
            publication='unverified'
            _sync_parent(path.parent)
            if credential(path)!=token:raise CredentialError('CREDENTIAL_WRITE_UNVERIFIED')
            publication='complete'
            stage.rmdir();stage=None
            return metadata
    except CredentialError as error:
        # Explicit exchange uncertainty/rejection overrides the surrounding callback.
        if error.operation_status!='not_started':status=error.operation_status
        raise CredentialError(error.code,status,publication) from None
    except BaseException as error:
        from agentguard.management.store import AccessError
        if isinstance(error,AccessError):
            status='rejected' if status=='unknown' else status
            code=error.code
        else:code='CREDENTIAL_PUBLICATION_FAILED' if status=='accepted' else 'CREDENTIAL_OPERATION_UNCERTAIN' if status=='unknown' else 'CREDENTIAL_PREPARATION_FAILED'
        raise CredentialError(code,status,publication) from None
    finally:
        if fd is not None:
            try:os.close(fd)
            except OSError:pass  # Cleanup must not mask the safe operation/publication status.
        if stage is not None:
            # Only this invocation's unpredictable private stage is touched, never target.
            try:
                temporary=stage/'credential'
                if temporary.exists():temporary.unlink()
                stage.rmdir()
            except OSError:pass  # A private orphan is not a success or an auto-retry signal.
