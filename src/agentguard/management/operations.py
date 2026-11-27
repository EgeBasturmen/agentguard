"""Private single-controller operations; signed backup, deletion intents and fail-closed recovery."""
from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile
from agentguard.management.store import AccessError,check_id,encode,identity,label,token_hash
from agentguard.management.locking import state_lock

def signature(key,value):return hmac.new(key,encode(value).encode(),hashlib.sha256).hexdigest()

def private_write(path,data):
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())

def checked_journal(path,key):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>1024*1024:raise AccessError('DELETION_JOURNAL_UNVERIFIED',409)
    value=json.loads(path.read_text(encoding='utf-8'))
    expected={'version','projects','mac'} if value.get('version')==1 else {'version','projects','workspaces','mac'}
    if set(value)!=expected or type(value['version']) is not int or value['version'] not in {1,2} or not isinstance(value['projects'],list) or len(value['projects'])>10000:raise AccessError('DELETION_JOURNAL_UNVERIFIED',409)
    for project in value['projects']:check_id(project)
    if value['projects']!=sorted(set(value['projects'])) or not hmac.compare_digest(value['mac'],signature(key,{k:v for k,v in value.items() if k!='mac'})):raise AccessError('DELETION_JOURNAL_UNVERIFIED',409)
    workspaces=value.get('workspaces',[])
    if not isinstance(workspaces,list) or len(workspaces)>10000 or workspaces!=sorted(set(workspaces)):raise AccessError('DELETION_JOURNAL_UNVERIFIED',409)
    for workspace in workspaces:check_id(workspace)
    return value

def journal(service,initialize=False):
    path=service.root/'deletions.json'
    with state_lock(service.root):
        if not path.exists() and initialize:
            value=dict(version=2,projects=[],workspaces=[]);value['mac']=signature(service.signing_key,value);private_write(path,encode(value).encode())
        return checked_journal(path,service.signing_key)

def record_deletion(service,project=None,workspace=None,projects=()):
    value=journal(service);value['version']=2;value.setdefault('workspaces',[])
    value['projects']=sorted(set(value['projects'])|{check_id(p) for p in projects}|({check_id(project)} if project else set()))
    if workspace:value['workspaces']=sorted(set(value['workspaces'])|{check_id(workspace)})
    if len(value['projects'])>10000 or len(value['workspaces'])>10000:raise AccessError('DELETION_JOURNAL_CAPACITY',409)
    value['mac']=signature(service.signing_key,{k:v for k,v in value.items() if k!='mac'})
    temporary=service.root/(identity()+'.journal-tmp')
    private_write(temporary,encode(value).encode());temporary.replace(service.root/'deletions.json')

def apply_deletions(service,db):
    intents=journal(service);projects=intents['projects'];workspaces=intents.get('workspaces',[])
    if not projects and not workspaces:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='deletion_applied'").fetchone() and db.execute('SELECT 1 FROM deletion_applied').fetchone():raise AccessError('DELETION_JOURNAL_REGRESSION',409)
        return
    version=db.execute('SELECT version FROM control_meta').fetchall()
    if len(version)!=1 or version[0][0] not in {1,2}:raise AccessError('UNSUPPORTED_DATABASE_VERSION',500)
    if version[0][0]==2:
        applied={row[0] for row in db.execute('SELECT project FROM deletion_applied')}
        if not applied<=set(projects)|{'w:'+w for w in workspaces}:raise AccessError('DELETION_JOURNAL_REGRESSION',409)
        projects=sorted(set(projects)-applied)
        workspaces=[w for w in workspaces if 'w:'+w not in applied]
    extra={row[0] for workspace in workspaces for row in db.execute('SELECT id FROM projects WHERE workspace=?',(workspace,))}
    projects=sorted(set(projects)|extra)
    for project in projects:
        for table in ('members','keys','objects','enrollments','runners','events','artifacts','usage','commercial','audit','jobs'):
            db.execute('DELETE FROM '+table+' WHERE project=?',(project,))
        if version[0][0]==2:
            for table in ('billing_reconciliation','quota_state','recovery_receipts'):db.execute('DELETE FROM '+table+' WHERE project=?',(project,))
        db.execute('DELETE FROM projects WHERE id=?',(project,))
        for suffix in ('','-wal','-shm','-journal'):
            path=service.root/(check_id(project)+'.scenarios.sqlite'+suffix)
            if path.is_symlink():raise AccessError('UNSAFE_STORAGE',400)
            if path.exists():path.unlink()
        if version[0][0]==2 and project in intents['projects']:db.execute('INSERT OR IGNORE INTO deletion_applied VALUES(?)',(project,))
    for workspace in workspaces:
        for table in ('workspace_members','keys','audit'):db.execute('DELETE FROM '+table+' WHERE workspace=?',(workspace,))
        db.execute('DELETE FROM workspaces WHERE id=?',(workspace,))
        if version[0][0]==2:db.execute('INSERT OR IGNORE INTO deletion_applied VALUES(?)',('w:'+workspace,))

def backup(service,output):
    output=Path(output).absolute()
    if output.exists() or output.is_symlink():raise FileExistsError('Backup output exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    with state_lock(service.root):
        with service.db() as db:
            if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','leased','running')").fetchone():raise AccessError('QUIESCENT_BACKUP_REQUIRED',409)
            projects=[row[0] for row in db.execute('SELECT id FROM projects')]
            schema=db.execute('SELECT version FROM control_meta').fetchone()[0]
        checkpoint=journal(service)
        with tempfile.TemporaryDirectory(prefix='agentguard-backup-') as directory:
            directory=Path(directory);files={}
            for path in [service.db_path,*[service.scenario_repository_path(p) for p in projects if service.scenario_repository_path(p).exists()]]:
                if path.is_symlink() or path.stat().st_nlink!=1:raise AccessError('UNSAFE_STORAGE',400)
                target=directory/path.name
                with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source,closing(sqlite3.connect(target)) as destination:source.backup(destination)
                if target.stat().st_size>64*1024*1024:raise AccessError('BACKUP_PROFILE_SIZE_LIMIT',409)
                files[path.name]=dict(size=target.stat().st_size,sha256=hashlib.sha256(target.read_bytes()).hexdigest())
            if sum(row['size'] for row in files.values())>256*1024*1024:raise AccessError('BACKUP_PROFILE_SIZE_LIMIT',409)
            manifest=dict(version=1,schema=schema,created=service.clock(),files=files,deletion_checkpoint=checkpoint['mac'])
            manifest['mac']=signature(service.signing_key,manifest)
            descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            try:
                with os.fdopen(descriptor,'wb') as stream,zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('manifest.json',encode(manifest))
                    for name in files:archive.write(directory/name,name)
            except Exception:
                output.unlink();raise
    return dict(backup_verified=True,files=len(files),signing_key_in_archive=False,encryption_provided=False,quiescent=True)

def restore(archive,target,*,signing_key,deletions):
    from agentguard.management.service import ControlPlane
    target=Path(target).absolute()
    if target.exists() or target.is_symlink():raise FileExistsError('Restore requires a new target')
    key_path=Path(signing_key)
    if key_path.is_symlink() or key_path.stat().st_size!=32:raise AccessError('INVALID_STORAGE_KEY',400)
    key=key_path.read_bytes();latest=checked_journal(deletions,key)
    target.parent.mkdir(parents=True,exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as source:
            entries=source.infolist()
            if len(entries)>256 or len({r.filename for r in entries})!=len(entries) or any(r.file_size>64*1024*1024 for r in entries) or sum(r.file_size for r in entries)>256*1024*1024:raise ValueError()
            if source.getinfo('manifest.json').file_size>1024*1024:raise ValueError()
            manifest=json.loads(source.read('manifest.json'))
            if set(manifest)!={'version','schema','created','files','deletion_checkpoint','mac'} or manifest['version']!=1 or manifest['schema'] not in {1,2} or not hmac.compare_digest(manifest['mac'],signature(key,{k:v for k,v in manifest.items() if k!='mac'})):raise ValueError()
            names=set(manifest['files'])
            if 'control.sqlite' not in names or set(r.filename for r in entries)!=names|{'manifest.json'}:raise ValueError()
            for name in names:
                if name!='control.sqlite':
                    if not name.endswith('.scenarios.sqlite'):raise ValueError()
                    check_id(name[:-17])
            with tempfile.TemporaryDirectory(prefix='agentguard-restore-',dir=target.parent) as directory:
                staging=Path(directory)/'state';staging.mkdir(mode=0o700)
                for name,expected in manifest['files'].items():
                    if set(expected)!={'size','sha256'} or expected['size']!=source.getinfo(name).file_size:raise ValueError()
                    hashed=hashlib.sha256()
                    descriptor=os.open(staging/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(descriptor,'wb') as output,source.open(name) as data:
                        while True:
                            block=data.read(65536)
                            if not block:break
                            hashed.update(block);output.write(block)
                    if hashed.hexdigest()!=expected['sha256']:raise ValueError()
                    with closing(sqlite3.connect(staging/name)) as db:
                        if db.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise ValueError()
                        if name=='control.sqlite' and db.execute('SELECT version FROM control_meta').fetchall()!=[(manifest['schema'],)]:raise ValueError()
                        if name!='control.sqlite' and db.execute('SELECT version FROM agentguard_meta').fetchall()!=[(1,)]:raise ValueError()
                private_write(staging/'signing.key',key);private_write(staging/'deletions.json',encode(latest).encode())
                recovered=ControlPlane(staging)
                with recovered.db() as db:
                    db.execute('UPDATE keys SET revoked=1');db.execute('UPDATE runners SET revoked=1');db.execute('UPDATE enrollments SET used=1')
                    for job in db.execute("SELECT * FROM jobs WHERE state IN ('queued','leased','running')").fetchall():recovered._fail_job(db,job,'RESTORE_REQUIRES_OPERATOR_RECOVERY')
                    recovered.event(db,None,None,'offline-operator','restore.quarantined',identity())
                recovered.purge_expired();staging.replace(target)
    except (zipfile.BadZipFile,KeyError,TypeError,ValueError,sqlite3.DatabaseError):raise AccessError('BACKUP_OR_RESTORE_UNVERIFIED',409) from None
    return dict(restored=True,credentials_revoked=True,automatic_redispatch=False,deletion_journal_required=True)

def quota_issues(db,project):
    issues=[]
    for job in db.execute('SELECT * FROM jobs WHERE project=?',(project,)):
        rows=db.execute("SELECT amount,source FROM usage WHERE project=? AND job=? AND kind='admitted_run'",(project,job['id'])).fetchall()
        if len(rows)!=1 or rows[0]['amount']!=1 or rows[0]['source']!='server-admission':issues.append('ADMISSION_LEDGER_MISMATCH')
        if any(type(job[field]) is not int or job[field]<0 for field in ('reserved_seconds','reserved_bytes','reserved_model_tokens','reserved_model_cost')):issues.append('INVALID_RESERVATION')
    for row in db.execute('SELECT kind,amount FROM usage WHERE project=?',(project,)):
        if row['kind'] not in {'admitted_run','lease_wall_seconds','model_tokens','estimated_cost_micro_usd'} or type(row['amount']) is not int or row['amount']<0:issues.append('INVALID_USAGE_AMOUNT_OR_KIND')
    if db.execute('SELECT 1 FROM usage u LEFT JOIN jobs j ON j.id=u.job AND j.project=u.project WHERE u.project=? AND j.id IS NULL',(project,)).fetchone():issues.append('ORPHAN_USAGE')
    return sorted(set(issues))

class Operations:
    def account_info(self,token):
        with self.db() as db:
            actor=self._auth(db,token,scope=None)
            row=db.execute('SELECT id,name FROM workspaces WHERE id=?',(actor['workspace'],)).fetchone()
            if row is None:raise AccessError('NOT_FOUND',404)
            return dict(workspace_id=row['id'],workspace_name=row['name'],role=actor['role'],workspace_owner_actions=actor['project'] is None and actor['role']=='owner',operator_assisted=True)

    def delete_workspace(self,token,*,confirmation):
        with self.db() as db:
            actor=self._auth(db,token,scope='project:admin')
            if actor['project'] is not None or actor['role']!='owner':raise AccessError('WORKSPACE_OWNER_REQUIRED',403)
            row=db.execute('SELECT name FROM workspaces WHERE id=?',(actor['workspace'],)).fetchone()
            if row is None or confirmation!=row['name']:raise AccessError('CONFIRM_WORKSPACE_NAME',409)
            projects=[r[0] for r in db.execute('SELECT id FROM projects WHERE workspace=?',(actor['workspace'],))]
            for project in projects:
                if db.execute('SELECT plan FROM projects WHERE id=?',(project,)).fetchone()[0]!='developer':raise AccessError('PLAN_CANCELLATION_REQUIRED',409)
                if db.execute("SELECT 1 FROM jobs WHERE project=? AND (state IN ('queued','leased','running') OR cleanup='unverified')",(project,)).fetchone():raise AccessError('VERIFIED_QUIESCENCE_REQUIRED',409)
            record_deletion(self,workspace=actor['workspace'],projects=projects);apply_deletions(self,db)
        return dict(deleted=True,primary_workspace_data_removed=True,customer_cache_and_backup_retention_still_required=True)

    def onboarding_funnel(self,token,project):
        with self.db() as db:
            self._auth(db,token,project)
            configured=db.execute("SELECT count(*) FROM objects WHERE project=? AND kind='connection'",(project,)).fetchone()[0]
            approved=0
            for row in db.execute("SELECT payload FROM objects WHERE project=? AND kind='case'",(project,)):
                case=json.loads(row[0])['case']
                if self.scenario_repository(project).get(case['id'],case['revision'])[1]=='approved':approved+=1
            verified=sum(self._job_view(row)['gate_decision']=='ALLOW' for row in db.execute("SELECT * FROM jobs WHERE project=? AND state='completed'",(project,)))
            return dict(configured_connections=configured,approved_cases=approved,verified_current_runs=verified,
                manual_plan_requests=db.execute("SELECT count(*) FROM commercial WHERE project=? AND kind='plan'",(project,)).fetchone()[0],
                self_service_success_verified=False,external_pilot_verified=False,assistance_level='operator-assisted private pilot; external onboarding not validated')

    def recover_job(self,job_id,report,*,operator,reason):
        operator=label(operator);reason=label(reason)
        from agentguard.core.manifest import digest
        with self.db() as db:
            row=db.execute("SELECT * FROM jobs WHERE id=? AND state='error'",(check_id(job_id),)).fetchone()
            if row is None:raise AccessError('RECOVERY_REQUIRES_FAILED_JOB',409)
            signed_receipt=row['result_hash'] is not None
            if signed_receipt and (not row['seal'] or not hmac.compare_digest(row['seal'],self._seal(row))):raise AccessError('STORED_RESULT_INTEGRITY_ERROR',409)
            _,runs,cleanup=self._evaluate_report(json.loads(row['spec']),report)
            if cleanup!='verified' or not runs or any(run.run_manifest is None or not run.run_manifest.started or not run.run_manifest.container_id for run in runs):raise AccessError('RECOVERY_CLEANUP_NOT_PROVEN',409)
            reference=digest(report)
            existing=db.execute("SELECT reference FROM recovery_receipts WHERE job=? AND kind='cache_cleanup'",(job_id,)).fetchone()
            if existing and existing[0]!=reference:raise AccessError('RECOVERY_EVIDENCE_CONFLICT',409)
            if not existing:db.execute('INSERT INTO recovery_receipts VALUES(?,?,?,?,?,?)',(identity(),row['project'],job_id,'cache_cleanup',reference,self.clock()))
            db.execute("UPDATE jobs SET cleanup='verified' WHERE id=?",(job_id,))
            if signed_receipt:
                updated=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
                db.execute('UPDATE jobs SET seal=? WHERE id=?',(self._seal(updated),job_id))
            self.event(db,None,row['project'],operator,'recovery.cleanup',token_hash(reason))
        return dict(cleanup_status='verified',gate_decision='BLOCK',authorizes_release=False,unknown_usage_reservations_retained=True)

    def delete_project(self,token,project,*,confirmation):
        with self.db() as db:
            self._auth(db,token,project,'project:admin')
            name=db.execute('SELECT name FROM projects WHERE id=?',(project,)).fetchone()[0]
            if confirmation!=name:raise AccessError('CONFIRM_PROJECT_NAME',409)
            if db.execute('SELECT plan FROM projects WHERE id=?',(project,)).fetchone()[0]!='developer':raise AccessError('PLAN_CANCELLATION_REQUIRED',409)
            if db.execute("SELECT 1 FROM jobs WHERE project=? AND (state IN ('queued','leased','running') OR cleanup='unverified')",(project,)).fetchone():raise AccessError('VERIFIED_QUIESCENCE_REQUIRED',409)
            record_deletion(self,project);apply_deletions(self,db)
        return dict(deleted=True,primary_project_data_removed=True,customer_runner_cache_removal_required=True,backup_journal_must_be_preserved=True)

    def reconcile_billing(self,project,*,operator,reference,paid):
        label(operator);reference=token_hash(label(reference))
        if type(paid) is not bool:raise AccessError('STRICT_PAYMENT_STATUS_REQUIRED',400)
        with self.db() as db:
            check_id(project)
            existing=db.execute('SELECT * FROM billing_reconciliation WHERE reference=?',(reference,)).fetchone()
            if existing:
                if existing['project']!=project or existing['status']!=str(paid):raise AccessError('CONTRADICTORY_PAYMENT_EVIDENCE',409)
            else:
                records=db.execute("SELECT payload FROM commercial WHERE project=? AND kind='plan' AND status='operator_confirmed_manual'",(project,)).fetchall()
                if not paid or not any(json.loads(row[0]).get('manual_receipt_reference_digest')==reference for row in records):raise AccessError('PAYMENT_REFERENCE_NOT_MATCHED',409)
                db.execute('INSERT INTO billing_reconciliation VALUES(?,?,?,?)',(reference,project,str(paid),self.clock()))
                self.event(db,None,project,operator,'billing.reconcile',reference)
        return dict(status='matched',payment_independently_verified=False)

    def operations_health(self,token,project):
        with self.db() as db:
            self._auth(db,token,project,'project:admin');issues=quota_issues(db,project)
            db.execute('INSERT OR REPLACE INTO quota_state VALUES(?,?,?,?)',(project,bool(issues),encode(issues),self.clock()))
            unknown=db.execute("SELECT count(*) FROM jobs WHERE project=? AND state='error' AND (cleanup='unverified' OR reserved_seconds>0 OR reserved_model_tokens>0)",(project,)).fetchone()[0]
            return dict(status='blocked' if issues else 'needs_operator_review' if unknown else 'ready',quota_issues=issues,unverified_jobs=unknown,billing=dict(plan=db.execute('SELECT plan FROM projects WHERE id=?',(project,)).fetchone()[0],payment_independently_verified=False),external_pilot_verified=False)
