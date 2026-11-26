"""Synthetic production cache filesystem tests. PYTEST_DONT_REWRITE: private records."""
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import pytest
from agentguard.management.cache import EvidenceCache,CacheError
from agentguard.core.manifest import digest

pytestmark=pytest.mark.integration
SERVER='http://127.0.0.1:8766'
TOKEN='ag7r_'+'s'*43
@pytest.fixture(autouse=True)
def private_fixture(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    secure_fixture_directory(tmp_path)
def cache(tmp_path,**kwargs):return EvidenceCache(tmp_path/'cache',server=SERVER,credential=TOKEN,**kwargs)
def claim():return dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1},max_seconds=30)
def report():return {'failure':'RUNNER_EXECUTION_ERROR'}
def ack(c,r):return dict(id=c['id'],project=c['project'],state='error',gate_decision='BLOCK',cleanup_status='unverified',authorizes_release=False,delivery_accepted=True,report_digest=digest(r))
def complete(c):
    q=claim();c.begin(q);c.complete(q,report());return q

def test_atomic_report_metadata_and_verified_ack(tmp_path):
    c=cache(tmp_path);q=complete(c)
    assert c.scan()[0]['state']=='pending_upload'
    assert c.delivery(q['project'],q['id'])['report']==report()
    assert json.loads((c.root/q['project']/(q['id']+'.json')).read_text())==report()
    c.acknowledge(q,report(),ack(q,report()))
    assert c.scan()[0]['state']=='acknowledged'
    assert cache(tmp_path).scan()[0]['state']=='acknowledged'

def test_pending_restart_can_ack_without_original_claim_spec(tmp_path):
    c=cache(tmp_path);q=complete(c);restarted=cache(tmp_path)
    delivery=restarted.delivery(q['project'],q['id'])
    restarted.acknowledge_delivery(delivery,ack(q,report()))
    assert restarted.scan()[0]['state']=='acknowledged'

def test_recovery_ack_is_bound_to_execution_identity(tmp_path):
    from dataclasses import replace
    c=cache(tmp_path);q=complete(c);delivery=c.delivery(q['project'],q['id'])
    with pytest.raises(CacheError):c.acknowledge_delivery(replace(delivery,execution='d'*32),ack(q,report()))
    assert c.scan()[0]['state']=='pending_upload'

@pytest.mark.parametrize('field',['id','project','report_digest','delivery_accepted','state','gate_decision','authorizes_release'])
def test_unverified_acceptance_never_marks_acknowledged(tmp_path,field):
    c=cache(tmp_path);q=complete(c);reply=ack(q,report());reply[field]=None
    with pytest.raises(CacheError):c.acknowledge(q,report(),reply)
    assert c.scan()[0]['state']=='pending_upload'

@pytest.mark.parametrize('change',['json','checksum','job','owner','lease','report'])
def test_corrupt_record_is_not_delivery_evidence(tmp_path,change):
    c=cache(tmp_path);q=complete(c);p=c.root/q['project']/(q['id']+'.entry.json')
    v=json.loads(p.read_text())
    if change=='json':p.write_text('{')
    else:
        if change=='checksum':v['checksum']='0'*64
        else:v['payload'][{'job':'job','owner':'owner','lease':'lease','report':'report'}[change]]='modified'
        p.write_text(json.dumps(v))
    assert c.scan()[0]['state']=='corrupt'
    with pytest.raises(CacheError):c.delivery(q['project'],q['id'])

def test_pending_and_incomplete_block_new_execution_and_survive_ttl(tmp_path):
    now=[10.0];c=cache(tmp_path,clock=lambda:now[0]);q=complete(c);now[0]+=100*86400
    assert c.purge()==0 and c.scan()[0]['state']=='pending_upload'
    with pytest.raises(CacheError,match='CACHE_RECOVERY_REQUIRED'):c.ensure_ready()
    assert cache(tmp_path).delivery(q['project'],q['id'])['report']==report()

def test_only_acknowledged_can_expire(tmp_path):
    now=[10.0];c=cache(tmp_path,clock=lambda:now[0]);q=complete(c);c.acknowledge(q,report(),ack(q,report()));now[0]+=2*86400
    assert c.purge()==1 and c.scan()==[]

def test_no_overwrite_or_cross_runner_recovery(tmp_path):
    c=cache(tmp_path);q=complete(c);before=(c.root/q['project']/(q['id']+'.entry.json')).read_bytes()
    with pytest.raises(CacheError):c.begin(q)
    with pytest.raises(CacheError):c.complete(dict(q,lease='wrong'),report())
    with pytest.raises(CacheError):EvidenceCache(c.root,server=SERVER,credential='ag7r_'+'t'*43)
    assert (c.root/q['project']/(q['id']+'.entry.json')).read_bytes()==before

def test_concurrent_sessions_fail_closed(tmp_path):
    c=cache(tmp_path);other=cache(tmp_path)
    with c.session():
        with ThreadPoolExecutor(1) as pool:
            f=pool.submit(other.ensure_ready)
            with pytest.raises(CacheError):f.result(30)
    other.ensure_ready()

@pytest.mark.parametrize('failure',['write','fsync','rename','sync_parent'])
def test_disk_and_publication_errors_preserve_previous_record(tmp_path,monkeypatch,failure):
    from agentguard.management import cache as module
    c=cache(tmp_path);q=claim();c.begin(q)
    target=c.root/q['project']/(q['id']+'.entry.json');before=target.read_bytes()
    def fail(*args,**kwargs):raise OSError(28,'synthetic disk failure')
    if failure=='write':monkeypatch.setattr(module.secure,'_write_secret',fail)
    elif failure=='fsync':monkeypatch.setattr(module.os,'fsync',fail)
    elif failure=='rename':monkeypatch.setattr(module.private,'replace',fail)
    else:monkeypatch.setattr(module.secure,'_sync_parent',fail)
    with pytest.raises(CacheError):c.complete(q,report())
    # Directory-sync failure may leave a complete visible record, never partial JSON.
    if failure!='sync_parent':assert target.read_bytes()==before
    assert json.loads(target.read_text())['payload']['state'] in ('incomplete','pending_upload')

@pytest.mark.parametrize('point',['during_write','before_publish','after_publish'])
def test_real_process_crash_never_exposes_partial_delivery(tmp_path,point):
    c=cache(tmp_path);q=claim();c.begin(q)
    script="""
import os,sys
from agentguard.management.cache import EvidenceCache
from agentguard.management import cache as m
c=EvidenceCache(sys.argv[1],server=sys.argv[2],credential=sys.argv[3])
q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1},max_seconds=30)
point=sys.argv[4]
if point=='during_write':
 def crash(fd,data):os.write(fd,data[:max(1,len(data)//2)]);os._exit(71)
 m.secure._write_secret=crash
elif point=='before_publish':
 def crash(*a,**k):os._exit(71)
 m.private.replace=crash
else:
 original=m.private.replace
 def crash(*a,**k):original(*a,**k);os._exit(71)
 m.private.replace=crash
c.complete(q,{'failure':'RUNNER_EXECUTION_ERROR'})
"""
    p=subprocess.run([sys.executable,'-B','-c',script,str(c.root),SERVER,TOKEN,point],capture_output=True,timeout=60)
    assert p.returncode==71 and not p.stdout and not p.stderr
    restarted=cache(tmp_path);states={x['state'] for x in restarted.scan()}
    assert states<= {'incomplete','pending_upload'} and states
    if point=='after_publish':assert restarted.delivery(q['project'],q['id'])['report']==report()
    else:
        with pytest.raises(CacheError):restarted.delivery(q['project'],q['id'])
    with pytest.raises(CacheError):restarted.ensure_ready()

def test_legacy_cache_is_preserved_and_not_promoted(tmp_path):
    from agentguard.management import credentials as secure
    root=tmp_path/'cache';root.mkdir(mode=0o700)
    if os.name=='nt':secure._windows_security(root,'harden')
    marker=root/'.agentguard-runner-cache';marker.write_text('private-runner-cache-v1')
    if os.name=='nt':secure._windows_security(marker,'harden')
    else:marker.chmod(0o600)
    c=cache(tmp_path)
    assert c.scan()[0]['state']=='legacy_unverified'
    with pytest.raises(CacheError):c.ensure_ready()
    assert marker.read_text()=='private-runner-cache-v1'

def test_native_private_permissions_and_unauthorized_denial(tmp_path):
    from agentguard.management import credentials as secure
    c=cache(tmp_path);q=complete(c);p=c.root/q['project']/(q['id']+'.entry.json')
    secure._permissions(c.root);secure._permissions(p)
    if os.name=='nt':
        from scripts.test_sprint1_windows_acl import access_denied_to_world_sid
        assert access_denied_to_world_sid(p)
    else:
        assert p.stat().st_mode&0o077==0 and c.root.stat().st_mode&0o077==0
    raw=p.read_bytes()
    assert TOKEN.encode() not in raw
    assert q['lease'] not in json.dumps(c.scan())
    assert q['lease'] not in repr(c.delivery(q['project'],q['id']))

def test_quota_refuses_new_work_without_purging_pending(tmp_path):
    c=cache(tmp_path);complete(c);limited=cache(tmp_path,max_bytes=1)
    with pytest.raises(CacheError):limited.ensure_ready()
    assert c.scan()[0]['state']=='pending_upload'

def test_foreign_project_and_duplicate_complete_cannot_reuse_report(tmp_path):
    c=cache(tmp_path);q=complete(c)
    with pytest.raises(CacheError):c.delivery('c'*32,q['id'])
    with pytest.raises(CacheError):c.complete(q,report())
    assert c.delivery(q['project'],q['id'])['report']==report()

@pytest.mark.parametrize('kind',['root','record'])
def test_native_unsafe_acl_fails_closed_without_repair(tmp_path,kind):
    from agentguard.management import cache_security as private
    c=cache(tmp_path);q=complete(c)
    target=c.root if kind=='root' else c.root/q['project']/(q['id']+'.entry.json')
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes as W
        adv=ctypes.WinDLL('advapi32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        p=ctypes.c_void_p()
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes=[W.LPCWSTR,W.DWORD,ctypes.POINTER(ctypes.c_void_p),ctypes.c_void_p]
        adv.SetFileSecurityW.argtypes=[W.LPCWSTR,W.DWORD,ctypes.c_void_p]
        adv.GetFileSecurityW.argtypes=[W.LPCWSTR,W.DWORD,ctypes.c_void_p,W.DWORD,ctypes.POINTER(W.DWORD)]
        kernel.LocalFree.argtypes=[ctypes.c_void_p]
        needed=W.DWORD();adv.GetFileSecurityW(str(target),4,None,0,ctypes.byref(needed))
        original=ctypes.create_string_buffer(needed.value)
        assert adv.GetFileSecurityW(str(target),4,original,len(original),ctypes.byref(needed))
        assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW('D:P(A;;FR;;;WD)',1,ctypes.byref(p),None)
        try:assert adv.SetFileSecurityW(str(target),0x80000004,p)
        finally:kernel.LocalFree(p)
    else:target.chmod(0o755 if kind=='root' else 0o644)
    try:
        with pytest.raises(Exception):private.permissions(target)
        if kind=='root':
            with pytest.raises(CacheError):c.ensure_ready()
        else:
            assert c.scan()[0]['state']=='corrupt'
            with pytest.raises(CacheError):c.delivery(q['project'],q['id'])
    finally:
        # The owner retains WRITE_DAC, not implicit WRITE_OWNER. Restore only the
        # test DACL; never assume ownership privileges after deliberately removing grants.
        if os.name=='nt':assert adv.SetFileSecurityW(str(target),0x80000004,original)
        else:target.chmod(0o700 if kind=='root' else 0o600)
        private.permissions(target)

def test_real_child_cannot_share_active_cache_session(tmp_path):
    c=cache(tmp_path)
    script="""
import sys
from agentguard.management.cache import EvidenceCache,CacheError
try:EvidenceCache(sys.argv[1],server=sys.argv[2],credential=sys.argv[3]);sys.exit(1)
except CacheError:sys.exit(0)
"""
    with c.session():
        p=subprocess.run([sys.executable,'-B','-c',script,str(c.root),SERVER,TOKEN],capture_output=True,timeout=30)
        assert p.returncode==0 and not p.stdout and not p.stderr
    cache(tmp_path).ensure_ready()

def test_export_orphan_cannot_be_recovered_as_valid_entry(tmp_path):
    from agentguard.management import cache_security as private
    c=cache(tmp_path);folder=c.root/('b'*32);private.directory(folder)
    orphan=folder/('a'*32+'.json');orphan.write_text('{}')
    private.permissions(orphan,harden=True)
    assert c.scan()[0]['state']=='corrupt'
    with pytest.raises(CacheError):c.ensure_ready()

def test_corrupt_filename_cannot_escape_into_safe_status(tmp_path):
    from agentguard.management import cache_security as private
    c=cache(tmp_path);folder=c.root/('b'*32);private.directory(folder)
    sensitive='SYNTHETIC_DO_NOT_EMIT'
    bad=folder/(sensitive+'.entry.json');bad.write_text('{}');private.permissions(bad,harden=True)
    assert sensitive not in json.dumps(c.scan())
    assert c.scan()[0]['state']=='corrupt'

def test_missing_behavioral_evidence_cannot_be_returned_as_allow(tmp_path):
    c=cache(tmp_path);q=complete(c);reply=ack(q,report())
    reply.update(state='completed',gate_decision='ALLOW',cleanup_status='verified')
    with pytest.raises(CacheError):c.acknowledge(q,report(),reply)
    assert c.scan()[0]['state']=='pending_upload'

def test_disk_full_new_report_does_not_damage_prior_acknowledged_evidence(tmp_path,monkeypatch):
    from agentguard.management import cache as module
    c=cache(tmp_path);q=complete(c);c.acknowledge(q,report(),ack(q,report()))
    prior=c.root/q['project']/(q['id']+'.entry.json');before=prior.read_bytes()
    second=dict(q,id='c'*32);c.begin(second)
    def full(fd,data):os.write(fd,data[:8]);raise OSError(28,'synthetic disk full')
    monkeypatch.setattr(module.secure,'_write_secret',full)
    with pytest.raises(CacheError):c.complete(second,report())
    assert prior.read_bytes()==before
    assert sorted(s['state'] for s in c.scan())==['acknowledged','incomplete']

def test_stage_name_collision_never_deletes_existing_orphan(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from agentguard.management import cache as module
    from agentguard.management import cache_security as private
    c=cache(tmp_path);q=claim();c.begin(q)
    stage=c.root/q['project']/('.stage-'+'d'*32);private.directory(stage)
    orphan=stage/'record';orphan.write_bytes(b'SYNTHETIC_PRIVATE_ORPHAN');private.permissions(orphan,harden=True)
    monkeypatch.setattr(module.uuid,'uuid4',lambda:SimpleNamespace(hex='d'*32))
    with pytest.raises(CacheError):c.complete(q,report())
    assert orphan.exists() and orphan.read_bytes()==b'SYNTHETIC_PRIVATE_ORPHAN'

@pytest.mark.parametrize('point',['before_ack_publish','after_ack_publish'])
def test_real_ack_crash_keeps_pending_or_complete_receipt(tmp_path,point):
    c=cache(tmp_path);q=complete(c)
    script="""
import os,sys
from agentguard.management.cache import EvidenceCache
from agentguard.management import cache as m
from agentguard.core.manifest import digest
c=EvidenceCache(sys.argv[1],server=sys.argv[2],credential=sys.argv[3])
q=dict(id='a'*32,project='b'*32,lease='synthetic-private-lease',spec={'retention_days':1})
r={'failure':'RUNNER_EXECUTION_ERROR'}
reply=dict(id=q['id'],project=q['project'],state='error',gate_decision='BLOCK',cleanup_status='unverified',authorizes_release=False,delivery_accepted=True,report_digest=digest(r))
original=m.private.replace
def crash(*a,**k):
 if sys.argv[4]=='after_ack_publish':original(*a,**k)
 os._exit(72)
m.private.replace=crash
c.acknowledge(q,r,reply)
"""
    p=subprocess.run([sys.executable,'-B','-c',script,str(c.root),SERVER,TOKEN,point],capture_output=True,timeout=30)
    assert p.returncode==72 and not p.stdout and not p.stderr
    states={s['state'] for s in cache(tmp_path).scan()}
    assert ('acknowledged' if point=='after_ack_publish' else 'pending_upload') in states
    assert states<={'acknowledged','pending_upload','incomplete'}
