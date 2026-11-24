"""Native publication and actual child-process crash tests; synthetic data only."""
import multiprocessing
import os
from pathlib import Path

import pytest
from agentguard.management import credentials as c
from scripts.sprint1_verification_support import secure_fixture_directory

pytestmark=pytest.mark.integration
SECRET='ag7_'+'P'*43


@pytest.fixture(autouse=True)
def private_root(tmp_path):secure_fixture_directory(tmp_path)


def test_success_is_complete_private_and_contains_no_secret_metadata(tmp_path):
    path=tmp_path/'key'
    result=c.private_file(path,lambda:dict(token=SECRET,key_id='a'*32))
    assert result==dict(key_id='a'*32)
    assert c.credential(path)==SECRET and path.stat().st_nlink==1
    c._permissions(path)
    assert not list(tmp_path.glob('.agentguard-private-*'))


@pytest.mark.parametrize('fault',['partial_write','flush','rename','permission_before','permission_after','directory_sync'])
def test_faults_are_fail_closed_without_overwrite_or_false_success(tmp_path,monkeypatch,fault):
    path=tmp_path/'key';calls=[]
    def error(*args):raise OSError(28,'SYNTHETIC_PRIVATE_ERROR')
    if fault=='partial_write':
        def partial(fd,data):os.write(fd,data[:2]);error()
        monkeypatch.setattr(c,'_write_secret',partial)
    if fault=='rename':monkeypatch.setattr(c,'_publish_noreplace',error)
    if fault=='directory_sync':monkeypatch.setattr(c,'_sync_parent',error)
    if fault=='permission_after':monkeypatch.setattr(c,'credential',error)
    if fault=='permission_before':
        original=c._permissions
        def permissions(p,*args):
            if p.name=='credential':raise c.CredentialError('CREDENTIAL_PERMISSIONS_UNVERIFIED')
            return original(p,*args)
        monkeypatch.setattr(c,'_permissions',permissions)
    def operation():
        calls.append(1)
        if fault=='flush':monkeypatch.setattr(c.os,'fsync',error)
        return dict(token=SECRET)
    with pytest.raises(c.CredentialError) as caught:c.private_file(path,operation)
    expected_calls=[] if fault=='permission_before' else [1]
    assert calls==expected_calls
    assert caught.value.operation_status==('not_started' if not calls else 'accepted')
    assert 'SYNTHETIC_PRIVATE_ERROR' not in str(caught.value)
    if fault in ('permission_after','directory_sync'):
        assert path.read_text()==SECRET  # Published copy is never deleted on verification failure.
        assert caught.value.publication_status=='unverified'
    else:assert not path.exists()


def test_late_noncooperating_writer_is_never_overwritten(tmp_path):
    path=tmp_path/'key'
    def operation():path.write_bytes(b'other-complete-file');return dict(token=SECRET)
    with pytest.raises(c.CredentialError) as caught:c.private_file(path,operation)
    assert path.read_bytes()==b'other-complete-file'
    assert caught.value.operation_status=='accepted'


def _hold_publication(path,ready,release,result):
    from agentguard.management.credentials import private_file
    def operation():ready.set();release.wait(30);return dict(token='ag7_'+'P'*43)
    try:private_file(path,operation);result.put('complete')
    except Exception:result.put('failed')


def test_two_publishers_only_one_operation_runs(tmp_path):
    context=multiprocessing.get_context('spawn');ready=context.Event();release=context.Event();result=context.Queue()
    path=tmp_path/'key';process=context.Process(target=_hold_publication,args=(path,ready,release,result));process.start()
    try:
        assert ready.wait(30)
        calls=[]
        with pytest.raises(c.CredentialError):c.private_file(path,lambda:calls.append(1))
        assert not calls and not path.exists()
    finally:release.set();process.join(40)
    assert not process.is_alive() and process.exitcode==0 and result.get(timeout=5)=='complete'
    assert c.credential(path)==SECRET


def _crash_publication(path,point):
    from agentguard.management import credentials as module
    original=module._publish_noreplace
    def crash(source,target):
        if point=='before':os._exit(77)
        original(source,target);os._exit(77)
    module._publish_noreplace=crash
    if point=='partial':
        def partial(fd,data):os.write(fd,data[:2]);os._exit(77)
        module._write_secret=partial
    module.private_file(path,lambda:dict(token='ag7_'+'P'*43))


@pytest.mark.parametrize('point',['before','after','partial'])
def test_process_crash_never_publishes_partial_credential_and_releases_guard(tmp_path,point):
    path=tmp_path/'key';context=multiprocessing.get_context('spawn')
    process=context.Process(target=_crash_publication,args=(path,point));process.start();process.join(40)
    assert not process.is_alive() and process.exitcode==77
    if point in ('before','partial'):assert not path.exists()
    else:assert c.credential(path)==SECRET and path.stat().st_nlink==1
    for stage in tmp_path.glob('.agentguard-private-*'):
        c._permissions(stage)
        for file in stage.iterdir():c._permissions(file)
    with c._guard(path):pass  # OS released the dead publisher's lock.


def test_invalid_response_never_publishes_secret_or_returns_metadata(tmp_path):
    path=tmp_path/'key'
    with pytest.raises(c.CredentialError) as caught:c.private_file(path,lambda:dict(token='bad',debug=SECRET))
    assert caught.value.operation_status=='accepted' and not path.exists()


def test_existing_link_is_not_followed_or_modified(tmp_path):
    original=tmp_path/'original';original.write_bytes(b'untouched')
    path=tmp_path/'key';os.link(original,path)
    with pytest.raises(c.CredentialError):c.private_file(path,lambda:dict(token=SECRET))
    assert original.read_bytes()==path.read_bytes()==b'untouched'


def test_response_metadata_cannot_echo_enrollment_secret(tmp_path):
    invitation='ag7e_'+'E'*43
    with pytest.raises(c.CredentialError):
        c.private_file(tmp_path/'key',lambda:dict(token=SECRET,user=invitation))
    assert not (tmp_path/'key').exists()


def test_native_permission_contract_and_unauthorized_read_denial(tmp_path):
    path=tmp_path/'key'
    def operation():
        stage=next(tmp_path.glob('.agentguard-private-*'))
        c._permissions(stage);c._permissions(stage/'credential')
        return dict(token=SECRET)
    c.private_file(path,operation)
    if os.name=='nt':
        from scripts.test_sprint1_windows_acl import acl_metadata,access_denied_to_world_sid
        metadata=acl_metadata(path)
        assert metadata['OwnerMatches'] and metadata['Protected'] and metadata['OwnerOnly']
        assert not metadata['Inherited'] and metadata['RuleCount']==1
        assert access_denied_to_world_sid(path)
    else:
        assert path.stat().st_mode&0o777==0o600
        assert path.stat().st_uid==os.getuid()


def test_unsafe_permissions_are_rejected_not_repaired(tmp_path):
    path=tmp_path/'key';c.private_file(path,lambda:dict(token=SECRET))
    if os.name=='nt':
        # Change only the synthetic file's DACL, never request owner/SACL privileges.
        import ctypes
        from ctypes import wintypes
        adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        descriptor=ctypes.c_void_p()
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
        adv.SetFileSecurityW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,ctypes.c_void_p]
        kernel.LocalFree.argtypes=[ctypes.c_void_p]
        assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:(A;;FR;;;WD)',1,ctypes.byref(descriptor),None)
        try:assert adv.SetFileSecurityW(str(path),4,descriptor)
        finally:kernel.LocalFree(descriptor)
    else:os.chmod(path,0o644)
    before=path.stat().st_mtime_ns
    with pytest.raises(c.CredentialError):c.credential(path)
    assert path.read_text()==SECRET and path.stat().st_mtime_ns==before
    with pytest.raises(c.CredentialError):c.credential(path)  # No permission repair.


def test_close_error_does_not_mask_accepted_exchange_status(tmp_path,monkeypatch):
    original=c.os.close;failed=[]
    def close(fd):
        original(fd)
        if not failed:failed.append(True);raise OSError(5,'SYNTHETIC_PRIVATE_ERROR')
    def operation():
        monkeypatch.setattr(c.os,'close',close)
        return dict(token=SECRET)
    with pytest.raises(c.CredentialError) as caught:c.private_file(tmp_path/'key',operation)
    assert caught.value.operation_status=='accepted'
    assert not (tmp_path/'key').exists()
