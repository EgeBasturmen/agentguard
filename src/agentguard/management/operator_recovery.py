"""Job-bound customer-runner evidence, never independent execution attestation.

Use existing v2 receipt storage: no schema or normal finish wire-format change.
All callers must hold the control DB transaction/coordination lock.
"""
import hashlib
import hmac
import json
import math
import os
import re
from agentguard.core.manifest import digest
from agentguard.management.store import AccessError,identity,encode,token_hash,check_id,label

INTENT='execution_intent_v1'
EVIDENCE='execution_evidence_v1'
CLEANUP='cache_cleanup'

def read_private_evidence(path):
    from agentguard.management.credentials import CredentialError
    try:return _read_private_evidence(path)
    except AccessError:raise
    except (CredentialError,OSError,ValueError,TypeError):
        raise AccessError('RECOVERY_PRIVATE_FILE_UNVERIFIED',409) from None

def _read_private_evidence(path):
    from agentguard.management import credentials as secure
    from agentguard.management import cache_security as private
    from agentguard.integrations.http import _json
    path=secure._path(path);before=secure._regular(path);private.permissions(path)
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(fd,'rb') as stream:
        current=os.fstat(stream.fileno())
        if (before.st_dev,before.st_ino)!=(current.st_dev,current.st_ino):fail()
        raw=stream.read(2*1024*1024+64*1024+1)
    if len(raw)>2*1024*1024+64*1024:fail()
    return _json(raw)

def fail(code='RECOVERY_EVIDENCE_UNVERIFIED'):
    raise AccessError(code,409)

def signed(service,value,domain):
    return dict(value=value,mac=hmac.new(service.signing_key,(domain+'\n'+encode(value)).encode(),hashlib.sha256).hexdigest())

def verify(service,value,domain):
    if not isinstance(value,dict) or set(value)!={'value','mac'} or not isinstance(value['mac'],str) or not re.fullmatch('[0-9a-f]{64}',value['mac']):fail()
    if not hmac.compare_digest(value['mac'],signed(service,value['value'],domain)['mac']):fail('RECOVERY_BINDING_INTEGRITY_ERROR')
    return value['value']

def record(db,job,kind,project):
    rows=db.execute('SELECT reference,project FROM recovery_receipts WHERE job=? AND kind=?',(job,kind)).fetchall()
    if len(rows)!=1 or rows[0][1]!=project:fail('RECOVERY_BINDING_MISSING_OR_CONFLICTING')
    try:return json.loads(rows[0][0])
    except (ValueError,TypeError):fail()

def insert(service,db,job,kind,value):
    from agentguard.management.limits import logical_storage,PLANS
    reference=value if isinstance(value,str) else encode(value)
    plan=db.execute('SELECT plan FROM projects WHERE id=?',(job['project'],)).fetchone()
    if plan is None or logical_storage(db,job['project'])+len(reference.encode())>PLANS[plan[0]]['storage']:fail('RECOVERY_STORAGE_QUOTA')
    db.execute('INSERT INTO recovery_receipts VALUES(?,?,?,?,?,?)',
        (identity(),job['project'],job['id'],kind,reference,service.clock()))

def issue(service,db,job,runner,lease):
    spec=json.loads(job['spec'])
    value=dict(version=1,job=job['id'],project=job['project'],runner=runner['id'],
        execution=identity(),lease_hash=token_hash(lease),spec_hash=digest(spec),
        cases=[dict(id=p['id'],revision=p['revision'],fingerprint=p['fingerprint'],run_id=identity()) for p in spec['snapshot']['cases']])
    ticket=signed(service,value,INTENT);insert(service,db,job,INTENT,ticket)
    return ticket

def envelope(value):
    try:
        if not isinstance(value,dict) or set(value)!={'payload','checksum'}:fail()
        p=value['payload']
        required={'version','owner','project','job','execution','lease','lease_hash','spec_hash','created','expires','state','report','report_hash','receipt','recovery_binding'}
        if not isinstance(p,dict) or set(p)!=required or type(p['version']) is not int or p['version']!=2 or digest(p)!=value['checksum']:fail()
        for key in ('job','project','execution'):check_id(p[key])
        for key in ('owner','lease_hash','spec_hash','report_hash'):
            if not isinstance(p[key],str) or not re.fullmatch('[0-9a-f]{64}',p[key]):fail()
        if not isinstance(p['lease'],str) or not 1<=len(p['lease'])<=512 or token_hash(p['lease'])!=p['lease_hash']:fail()
        if any(type(p[k]) not in (int,float) or not math.isfinite(p[k]) for k in ('created','expires')) or p['expires']<=p['created']:fail()
        if p['state'] not in {'pending_upload','acknowledged'} or not isinstance(p['report'],dict) or len(encode(p['report']).encode())>2*1024*1024 or digest(p['report'])!=p['report_hash']:fail()
        if p['state']=='pending_upload' and p['receipt'] is not None:fail()
        if p['state']=='acknowledged':
            from agentguard.management.cache import EvidenceCache
            EvidenceCache._receipt(None,p,p['receipt'])
        return p
    except AccessError:raise
    except Exception:fail()

def bound(service,db,job,evidence,lease=None):
    p=envelope(evidence);ticket=record(db,job['id'],INTENT,job['project']);b=verify(service,ticket,INTENT)
    if p['recovery_binding']!=ticket:fail('RECOVERY_JOB_BINDING_MISMATCH')
    expected=dict(job=job['id'],project=job['project'],runner=job['runner'],spec_hash=digest(json.loads(job['spec'])))
    if any(b.get(k)!=v for k,v in expected.items()):fail('RECOVERY_JOB_BINDING_MISMATCH')
    if any(p[k]!=b[k] for k in ('job','project','execution','lease_hash','spec_hash')):fail('RECOVERY_JOB_BINDING_MISMATCH')
    if lease is not None and (not isinstance(lease,str) or not hmac.compare_digest(token_hash(lease),b['lease_hash'])):fail('RECOVERY_LEASE_MISMATCH')
    return p,b

def runtime(service,job,p,b):
    spec=json.loads(job['spec'])
    if spec['kind']!='suite' or spec['connection']['runtime']['agent']['transport'] not in {'python','container'}:fail('RECOVERY_RUNTIME_NOT_OBSERVED')
    _,runs,cleanup=service._evaluate_report(spec,p['report'])
    if cleanup!='verified' or len(runs)!=len(b['cases']):fail('RECOVERY_CLEANUP_NOT_PROVEN')
    expected={c['id']:c for c in b['cases']};observed=[]
    members=p['report']['execution']['cases']
    if len(members)!=len(expected) or {c['id'] for c in members}!=set(expected):fail('RECOVERY_CLEANUP_MEMBERS_MISMATCH')
    for case in members:
        pin=expected.get(case['id']);run=case.get('result') or {};m=run.get('run_manifest') or {}
        if pin is None or case['revision']!=pin['revision'] or m.get('run_id')!=pin['run_id'] or m.get('started') is not True:fail('RECOVERY_RUNTIME_IDENTITY_MISMATCH')
        for key in ('container_id','worker_digest','source_digest'):
            if not isinstance(m.get(key),str) or not re.fullmatch('[0-9a-f]{64}',m[key]):fail('RECOVERY_RUNTIME_IDENTITY_MISMATCH')
        if not isinstance(m.get('image_id'),str) or not re.fullmatch('sha256:[0-9a-f]{64}',m['image_id']):fail('RECOVERY_RUNTIME_IDENTITY_MISMATCH')
        observed.append(dict(case=case['id'],revision=case['revision'],runtime=m))
    if len({r['runtime']['container_id'] for r in observed})!=len(observed):fail('RECOVERY_RUNTIME_IDENTITY_MISMATCH')
    return observed

def seal_check(service,job):
    if (job['result_hash'] is not None or job['seal'] is not None) and (not job['seal'] or not hmac.compare_digest(job['seal'],service._seal(job))):fail('STORED_RESULT_INTEGRITY_ERROR')

def register(service,runner_token,job_id,lease,evidence):
    with service.db() as db:
        runner=service._runner(db,runner_token)
        job=db.execute('SELECT * FROM jobs WHERE id=? AND project=? AND runner=?',(check_id(job_id),runner['project'],runner['id'])).fetchone()
        if job is None:raise AccessError('NOT_FOUND',404)
        seal_check(service,job)
        p,b=bound(service,db,job,evidence,lease)
        observed=runtime(service,job,p,b)
        value=dict(binding=b,owner=p['owner'],report_hash=p['report_hash'],runtime=observed)
        registration=signed(service,value,EVIDENCE)
        rows=db.execute('SELECT reference,project FROM recovery_receipts WHERE job=? AND kind=?',(job_id,EVIDENCE)).fetchall()
        if rows:
            if len(rows)!=1 or rows[0][1]!=job['project'] or json.loads(rows[0][0])!=registration:fail('RECOVERY_EVIDENCE_CONFLICT')
        else:
            # A runtime may belong to one job only, even within the same suite.
            for row in db.execute('SELECT reference,project,job FROM recovery_receipts WHERE kind=?',(EVIDENCE,)):
                previous=verify(service,json.loads(row[0]),EVIDENCE)
                if previous['binding']['project']!=row[1] or previous['binding']['job']!=row[2]:fail('RECOVERY_BINDING_INTEGRITY_ERROR')
                if {r['runtime']['container_id'] for r in previous['runtime']} & {r['runtime']['container_id'] for r in observed}:fail('RECOVERY_RUNTIME_REPLAY')
            insert(service,db,job,EVIDENCE,registration)
        service._runner(db,runner_token)
        return dict(recovery_evidence_registered=True,job=job_id,execution=b['execution'],report_digest=p['report_hash'],independent_attestation=False)

def recover(service,job_id,evidence,*,operator,reason,token=None,project=None):
    operator=label(operator);reason=label(reason)
    if token is None or project is None:raise AccessError('RECOVERY_AUTHORIZATION_REQUIRED',403)
    with service.db() as db:
        actor=service._auth(db,token,project,'project:admin')
        job=db.execute("SELECT * FROM jobs WHERE id=? AND project=? AND state='error'",(check_id(job_id),project)).fetchone()
        if job is None:raise AccessError('RECOVERY_REQUIRES_FAILED_JOB',409)
        seal_check(service,job)
        p,b=bound(service,db,job,evidence);observed=runtime(service,job,p,b)
        expected=dict(binding=b,owner=p['owner'],report_hash=p['report_hash'],runtime=observed)
        registration=verify(service,record(db,job_id,EVIDENCE,project),EVIDENCE)
        if registration!=expected:fail('RECOVERY_EVIDENCE_CONFLICT')
        reference=digest(dict(binding=b,report_hash=p['report_hash'],runtime=observed))
        existing=db.execute('SELECT reference,project FROM recovery_receipts WHERE job=? AND kind=?',(job_id,CLEANUP)).fetchall()
        if existing:
            if len(existing)!=1 or existing[0][1]!=project or existing[0][0]!=reference:fail('RECOVERY_EVIDENCE_CONFLICT')
            if job['cleanup']!='verified' or not job['seal'] or not hmac.compare_digest(job['seal'],service._seal(job)):fail('RECOVERY_RECEIPT_STATE_UNVERIFIED')
        else:
            if job['cleanup']=='verified':fail('RECOVERY_RECEIPT_STATE_UNVERIFIED')
            insert(service,db,job,CLEANUP,reference)
            db.execute("UPDATE jobs SET cleanup='verified' WHERE id=?",(job_id,))
            updated=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            db.execute('UPDATE jobs SET seal=? WHERE id=?',(service._seal(updated),job_id))
            service.event(db,actor['workspace'],project,actor['user'],'recovery.cleanup',token_hash(reason))
        service._auth(db,token,project,'project:admin')
        return dict(cleanup_status='verified',gate_decision='BLOCK',authorizes_release=False,unknown_usage_reservations_retained=True,independent_attestation=False)
