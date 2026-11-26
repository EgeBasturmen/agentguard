"""Cache-only native ACL checks; no shell processes or security descriptors in logs."""
import ctypes
import os
from contextlib import contextmanager
from agentguard.management import credentials as secure

def replace(source,target):
    if os.name=='nt':
        from ctypes import wintypes as W
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.MoveFileExW.argtypes=[W.LPCWSTR,W.LPCWSTR,W.DWORD];kernel.MoveFileExW.restype=W.BOOL
        # REPLACE_EXISTING | WRITE_THROUGH, same local volume only.
        if not kernel.MoveFileExW(str(source),str(target),9):raise OSError(ctypes.get_last_error(),'Cache publication failed')
    else:os.replace(source,target)

def windows_private(path,*,harden=False):
    from ctypes import wintypes as W
    P=ctypes.c_void_p;D=W.DWORD
    adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    def api(lib,name,args,result=W.BOOL):
        fn=getattr(lib,name);fn.argtypes=args;fn.restype=result;return fn
    current=api(kernel,'GetCurrentProcess',[],W.HANDLE)
    open_token=api(adv,'OpenProcessToken',[W.HANDLE,D,ctypes.POINTER(W.HANDLE)])
    info=api(adv,'GetTokenInformation',[W.HANDLE,ctypes.c_int,P,D,ctypes.POINTER(D)])
    close=api(kernel,'CloseHandle',[W.HANDLE]);free=api(kernel,'LocalFree',[P],P)
    get_security=api(adv,'GetFileSecurityW',[W.LPCWSTR,D,P,D,ctypes.POINTER(D)])
    owner_fn=api(adv,'GetSecurityDescriptorOwner',[P,ctypes.POINTER(P),ctypes.POINTER(W.BOOL)])
    dacl_fn=api(adv,'GetSecurityDescriptorDacl',[P,ctypes.POINTER(W.BOOL),ctypes.POINTER(P),ctypes.POINTER(W.BOOL)])
    control_fn=api(adv,'GetSecurityDescriptorControl',[P,ctypes.POINTER(W.WORD),ctypes.POINTER(D)])
    ace_fn=api(adv,'GetAce',[P,D,ctypes.POINTER(P)])
    equal=api(adv,'EqualSid',[P,P])
    token=W.HANDLE();sid_text=P();descriptor=P()
    def require(value):
        if not value:raise secure.CredentialError('CREDENTIAL_PERMISSIONS_UNVERIFIED')
    try:
        require(open_token(current(),8,ctypes.byref(token)))
        needed=D();info(token,1,None,0,ctypes.byref(needed));require(0<needed.value<65536)
        user=ctypes.create_string_buffer(needed.value);require(info(token,1,user,len(user),ctypes.byref(needed)))
        user_sid=P.from_buffer(user).value
        if harden:
            to_text=api(adv,'ConvertSidToStringSidW',[P,ctypes.POINTER(P)])
            from_sddl=api(adv,'ConvertStringSecurityDescriptorToSecurityDescriptorW',[W.LPCWSTR,D,ctypes.POINTER(P),ctypes.POINTER(D)])
            set_security=api(adv,'SetFileSecurityW',[W.LPCWSTR,D,P])
            require(to_text(user_sid,ctypes.byref(sid_text)))
            sid=ctypes.wstring_at(sid_text);flags='OICI' if path.is_dir() else ''
            require(from_sddl('O:'+sid+'D:P(A;'+flags+';FA;;;'+sid+')',1,ctypes.byref(descriptor),None))
            require(set_security(str(path),0x80000005,descriptor))
        needed=D();get_security(str(path),5,None,0,ctypes.byref(needed));require(0<needed.value<65536)
        raw=ctypes.create_string_buffer(needed.value);require(get_security(str(path),5,raw,len(raw),ctypes.byref(needed)))
        owner=P();defaulted=W.BOOL();acl=P();present=W.BOOL();control=W.WORD();revision=D()
        require(owner_fn(raw,ctypes.byref(owner),ctypes.byref(defaulted)) and equal(owner,user_sid))
        require(dacl_fn(raw,ctypes.byref(present),ctypes.byref(acl),ctypes.byref(defaulted)) and present and acl.value)
        require(control_fn(raw,ctypes.byref(control),ctypes.byref(revision)) and control.value&0x1000)
        class ACL(ctypes.Structure):_fields_=[('revision',ctypes.c_ubyte),('unused',ctypes.c_ubyte),('size',W.WORD),('count',W.WORD),('unused2',W.WORD)]
        require(ctypes.cast(acl,ctypes.POINTER(ACL)).contents.count==1)
        ace=P();require(ace_fn(acl,0,ctypes.byref(ace)))
        header=ctypes.string_at(ace,8)
        require(header[0]==0 and header[1]==(3 if path.is_dir() else 0))
        require(int.from_bytes(header[4:8],'little')==0x1f01ff and equal(ace.value+8,user_sid))
    finally:
        if descriptor.value:free(descriptor)
        if sid_text.value:free(sid_text)
        if token.value:close(token)

def permissions(path,*,harden=False):
    secure._path(path)
    if os.name=='nt':windows_private(path,harden=harden)
    else:
        if harden:path.chmod(0o700 if path.is_dir() else 0o600)
        secure._permissions(path)

def directory(path):
    if path.exists():permissions(path);return
    permissions(path.parent)
    path.mkdir(mode=0o700);permissions(path,harden=True)

@contextmanager
def guard(path):
    fd=None;locked=False
    try:
        try:
            fd=os.open(path,os.O_RDWR|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0),0o600)
            permissions(path,harden=True);os.fsync(fd)
        except FileExistsError:
            secure._path(path);secure._regular(path);permissions(path)
            fd=os.open(path,os.O_RDWR|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
        st=os.fstat(fd)
        if st.st_ino!=path.stat().st_ino or st.st_size not in (0,1):raise secure.CredentialError('CREDENTIAL_PUBLICATION_BUSY')
        secure._regular(path);permissions(path)
        try:
            if os.name=='nt':
                import msvcrt
                os.lseek(fd,0,0);msvcrt.locking(fd,msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked=True
        except OSError:raise secure.CredentialError('CREDENTIAL_PUBLICATION_BUSY') from None
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
