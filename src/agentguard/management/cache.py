"""Private, single-owner evidence journal. No execution or transport retries.

One checksummed envelope is the authority. Canonical report JSON is only an export.
Local hashes detect accidental corruption; they do not authenticate a hostile owner.
"""
from contextlib import contextmanager
from dataclasses import dataclass,field
import hashlib
import math
import os
from pathlib import Path
import re
import shutil
import threading
import time
import uuid
from agentguard.management import credentials as secure
from agentguard.management import cache_security as private
from agentguard.management.store import AccessError,check_id,encode
from agentguard.integrations.http import _json
from agentguard.core.manifest import digest

class CacheError(AccessError):
    def __init__(self,code='CACHE_UNVERIFIED'):super().__init__(code,409)

@dataclass(frozen=True)
class PendingDelivery:
    job_id: str
    project: str
    execution: str
    lease: str=field(repr=False)
    report: dict=field(repr=False)
    def __getitem__(self,key):return getattr(self,key)
    def request_body(self):
        """Sensitive transport input; never emit this body as diagnostics."""
        return dict(job_id=self.job_id,lease=self.lease,report=self.report)

class EvidenceCache:
    MAX_REPORT=2*1024*1024
    MAX_RECORD=MAX_REPORT+64*1024
    def __init__(self,root,*,server,credential,clock=None,max_bytes=64*1024*1024,max_records=1000):
        self.clock=clock or time.time;self.max_bytes=max_bytes;self.max_records=max_records
        self._local=threading.local();self.legacy=False
        self.owner=digest(dict(server=server,credential_hash=hashlib.sha256(credential.encode()).hexdigest()))
        try:
            self.root=secure._path(root)
            if not self.root.exists():secure._parent(self.root)
            private.permissions(self.root);secure._permissions(self.root.parent,'parent')
            with self.session():
                marker=self.root/'.agentguard-runner-cache'
                if marker.exists():
                    raw=self._read(marker,1024)
                    if raw==b'private-runner-cache-v1':self.legacy=True
                    elif _json(raw)!=dict(version=2,owner=self.owner):raise CacheError('CACHE_OWNER_MISMATCH')
                else:
                    if any(p.name!=self._guard_name() for p in self.root.iterdir()):raise CacheError('CACHE_UNRECOGNIZED_CONTENT')
                    self._atomic(marker,dict(version=2,owner=self.owner))
        except CacheError:raise
        except Exception:raise CacheError('CACHE_PREPARATION_UNVERIFIED') from None

    def _guard_name(self):
        return '.agentguard-credential-'+hashlib.sha256('cache'.encode()).hexdigest()[:24]+'.lock'

    @contextmanager
    def session(self):
        if getattr(self._local,'active',False):
            yield;return
        try:
            secure._path(self.root);private.permissions(self.root)
            with private.guard(self.root/self._guard_name()):
                self._local.active=True
                try:yield
                finally:self._local.active=False
        except AccessError:raise
        except secure.CredentialError as error:
            raise CacheError('CACHE_BUSY' if error.code=='CREDENTIAL_PUBLICATION_BUSY' else 'CACHE_PERMISSIONS_UNVERIFIED') from None
        except Exception:raise CacheError('CACHE_IO_UNVERIFIED') from None

    def _read(self,path,limit=None):
        secure._path(path);st=secure._regular(path);private.permissions(path)
        if st.st_size>(limit or self.MAX_RECORD):raise CacheError('CACHE_RECORD_TOO_LARGE')
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
        with os.fdopen(fd,'rb') as stream:
            current=os.fstat(stream.fileno())
            if (st.st_dev,st.st_ino)!=(current.st_dev,current.st_ino):raise CacheError('CACHE_PATH_CHANGED')
            raw=stream.read((limit or self.MAX_RECORD)+1)
        if len(raw)>(limit or self.MAX_RECORD):raise CacheError('CACHE_RECORD_TOO_LARGE')
        return raw

    def _atomic(self,target,value,*,replace=False):
        data=encode(value).encode('utf-8')
        if len(data)>self.MAX_RECORD:raise CacheError('CACHE_RECORD_TOO_LARGE')
        stage=target.parent/('.stage-'+uuid.uuid4().hex);temporary=stage/'record';fd=None;owns_stage=False;owns_file=False
        try:
            stage.mkdir(mode=0o700);owns_stage=True
            private.permissions(stage,harden=True)
            fd=os.open(temporary,os.O_RDWR|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0),0o600)
            owns_file=True
            private.permissions(temporary,harden=True)
            private.permissions(temporary);secure._regular(temporary)
            secure._write_secret(fd,data)
            os.lseek(fd,0,0)
            if os.read(fd,len(data)+1)!=data:raise CacheError('CACHE_WRITE_UNVERIFIED')
            os.close(fd);fd=None
            if replace:
                secure._path(target);secure._regular(target);private.permissions(target)
                private.replace(temporary,target)
            else:secure._publish_noreplace(temporary,target)
            secure._sync_parent(target.parent)
            if self._read(target)!=data:raise CacheError('CACHE_WRITE_UNVERIFIED')
        finally:
            if fd is not None:
                try:os.close(fd)
                except OSError:pass
            try:
                if owns_file and temporary.exists():temporary.unlink()
                if owns_stage:stage.rmdir()
            except OSError:pass

    def _path(self,project,job):
        folder=self.root/check_id(project)
        secure._path(folder)
        if folder.exists():private.permissions(folder)
        return folder/(check_id(job)+'.entry.json')

    def _envelope(self,payload):return dict(payload=payload,checksum=digest(payload))

    def _load(self,project,job):
        try:
            entry=_json(self._read(self._path(project,job)))
            if set(entry)!={'payload','checksum'} or digest(entry['payload'])!=entry['checksum']:raise ValueError()
            p=entry['payload']
            fields={'version','owner','project','job','execution','lease','lease_hash','spec_hash','created','expires','state','report','report_hash','receipt'}
            if set(p)!=fields or p['version']!=2 or p['owner']!=self.owner or p['project']!=project or p['job']!=job:raise ValueError()
            check_id(p['execution'])
            if not isinstance(p['lease'],str) or not 1<=len(p['lease'])<=512 or hashlib.sha256(p['lease'].encode()).hexdigest()!=p['lease_hash']:raise ValueError()
            if not re.fullmatch('[0-9a-f]{64}',p['spec_hash']):raise ValueError()
            if any(type(p[k]) not in (int,float) or not math.isfinite(p[k]) for k in ('created','expires')) or p['expires']<=p['created']:raise ValueError()
            if p['state']=='incomplete':
                if any(p[k] is not None for k in ('report','report_hash','receipt')):raise ValueError()
            elif p['state'] in {'pending_upload','acknowledged'}:
                if not isinstance(p['report'],dict) or len(encode(p['report']).encode())>self.MAX_REPORT or digest(p['report'])!=p['report_hash']:raise ValueError()
                if p['state']=='pending_upload' and p['receipt'] is not None:raise ValueError()
                if p['state']=='acknowledged':self._receipt(p,p['receipt'])
            else:raise ValueError()
            return p
        except CacheError:raise
        except Exception:raise CacheError('CACHE_RECORD_CORRUPT') from None

    def _bound(self,p,claim):
        if p['job']!=claim['id'] or p['project']!=claim['project'] or p['lease']!=claim['lease'] or p['spec_hash']!=digest(claim['spec']):raise CacheError('CACHE_BINDING_MISMATCH')

    def begin(self,claim):
        with self.session():
            self.ensure_ready()
            path=self._path(claim['project'],claim['id'])
            if path.exists():raise CacheError('CACHE_RESULT_EXISTS')
            private.directory(path.parent)
            lease=claim['lease'];days=claim['spec'].get('retention_days',7)
            if not isinstance(lease,str) or not 1<=len(lease)<=512 or type(days) is not int or not 1<=days<=30:raise CacheError('CACHE_CLAIM_INVALID')
            now=self.clock()
            payload=dict(version=2,owner=self.owner,project=claim['project'],job=claim['id'],execution=uuid.uuid4().hex,lease=lease,
                lease_hash=hashlib.sha256(lease.encode()).hexdigest(),spec_hash=digest(claim['spec']),created=now,expires=now+days*86400,
                state='incomplete',report=None,report_hash=None,receipt=None)
            self._atomic(path,self._envelope(payload))

    def complete(self,claim,report):
        with self.session():
            p=self._load(claim['project'],claim['id']);self._bound(p,claim)
            if p['state']!='incomplete':raise CacheError('CACHE_RESULT_EXISTS')
            if not isinstance(report,dict) or not report or len(encode(report).encode())>self.MAX_REPORT:raise CacheError('CACHE_REPORT_INVALID')
            p.update(state='pending_upload',report=report,report_hash=digest(report))
            self._atomic(self._path(p['project'],p['job']),self._envelope(p),replace=True)
            # Compatibility export is derived, never used to recover delivery state.
            self._atomic(self.root/p['project']/(p['job']+'.json'),report)

    def _receipt(self,p,reply):
        if not isinstance(reply,dict) or reply.get('delivery_accepted') is not True or reply.get('report_digest')!=p['report_hash'] or reply.get('id')!=p['job'] or reply.get('project')!=p['project'] or reply.get('state') not in {'completed','error'} or reply.get('gate_decision') not in {'ALLOW','BLOCK'} or reply.get('authorizes_release') is not False or reply.get('cleanup_status') not in {'verified','unverified','not_required'}:
            raise CacheError('CACHE_ACKNOWLEDGEMENT_UNVERIFIED')
        if reply['state']=='error' and reply['gate_decision']!='BLOCK':raise CacheError('CACHE_ACKNOWLEDGEMENT_UNVERIFIED')
        if reply['gate_decision']=='ALLOW':
            if reply['state']!='completed' or reply['cleanup_status']!='verified' or 'execution' not in p['report']:raise CacheError('CACHE_ACKNOWLEDGEMENT_UNVERIFIED')

    def acknowledge(self,claim,report,reply):
        with self.session():
            p=self._load(claim['project'],claim['id']);self._bound(p,claim)
            if p['state']!='pending_upload' or p['report_hash']!=digest(report):raise CacheError('CACHE_BINDING_MISMATCH')
            self._persist_ack(p,reply)

    def _persist_ack(self,p,reply):
        self._receipt(p,reply)
        # Persist only operational receipt fields, never arbitrary server response text.
        p.update(state='acknowledged',receipt={k:reply[k] for k in ('id','project','state','gate_decision','cleanup_status','authorizes_release','delivery_accepted','report_digest')})
        self._atomic(self._path(p['project'],p['job']),self._envelope(p),replace=True)

    def acknowledge_delivery(self,delivery,reply):
        """Record an explicitly obtained acceptance; no network or retry is performed."""
        with self.session():
            if not isinstance(delivery,PendingDelivery):raise CacheError('CACHE_BINDING_MISMATCH')
            p=self._load(delivery.project,delivery.job_id)
            if p['state']!='pending_upload' or p['execution']!=delivery.execution or p['lease']!=delivery.lease or p['report_hash']!=digest(delivery.report):raise CacheError('CACHE_BINDING_MISMATCH')
            self._persist_ack(p,reply)

    def delivery(self,project,job):
        """Private S2-04 input; contains the lease and must never be printed/logged."""
        with self.session():
            if self.legacy:raise CacheError('CACHE_LEGACY_UNVERIFIED')
            p=self._load(project,job)
            if p['state']!='pending_upload':raise CacheError('CACHE_NOT_PENDING')
            return PendingDelivery(p['job'],p['project'],p['execution'],p['lease'],p['report'])

    def scan(self):
        """Secret-free local status. Never a PASS/ALLOW or execution receipt."""
        with self.session():
            if self.legacy:return [dict(state='legacy_unverified')]
            results=[]
            for folder in self.root.iterdir():
                if folder.name.startswith('.stage-'):results.append(dict(state='incomplete'));continue
                if folder.name in {'.agentguard-runner-cache',self._guard_name()}:continue
                if not re.fullmatch('[0-9a-f]{32}',folder.name):
                    results.append(dict(state='corrupt'));continue
                secure._path(folder);private.permissions(folder)
                for path in folder.iterdir():
                    if path.name.startswith('.stage-'):results.append(dict(state='incomplete'));continue
                    if not path.name.endswith('.entry.json'):
                        if re.fullmatch('[0-9a-f]{32}\\.json',path.name) and (folder/(path.stem+'.entry.json')).exists():continue
                        results.append(dict(project=folder.name,state='corrupt'));continue
                    job=path.name[:-11]
                    if not re.fullmatch('[0-9a-f]{32}',job):
                        results.append(dict(project=folder.name,state='corrupt'));continue
                    try:p=self._load(folder.name,job);results.append(dict(project=folder.name,job=job,execution=p['execution'],state=p['state']))
                    except (CacheError,AccessError):results.append(dict(project=folder.name,job=job,state='corrupt'))
            return results

    def ensure_ready(self):
        with self.session():
            if any(r['state']!='acknowledged' for r in self.scan()):raise CacheError('CACHE_RECOVERY_REQUIRED')
            files=list(self.root.rglob('*'))
            if sum(p.lstat().st_size for p in files if p.is_file())+4*self.MAX_REPORT>self.max_bytes or sum(r['state']=='acknowledged' for r in self.scan())>=self.max_records:raise CacheError('CACHE_QUOTA_EXCEEDED')
            if shutil.disk_usage(self.root).free<4*self.MAX_REPORT:raise CacheError('CACHE_DISK_SPACE_UNVERIFIED')

    def purge(self):
        with self.session():
            if self.legacy:return 0
            removed=0
            for status in self.scan():
                if status['state']!='acknowledged':continue
                p=self._load(status['project'],status['job'])
                if p['expires']>self.clock():continue
                path=self._path(p['project'],p['job']);export=path.parent/(p['job']+'.json')
                if export.exists():
                    if digest(_json(self._read(export)))!=p['report_hash']:raise CacheError('CACHE_EXPORT_CORRUPT')
                    export.unlink();secure._sync_parent(path.parent)
                path.unlink();secure._sync_parent(path.parent);removed+=1
            return removed
