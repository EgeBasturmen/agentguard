"""Durable single-dispatch leases. Unknown execution is terminal, never silently retried."""
import hmac
import json
import secrets
from agentguard.management.store import AccessError,identity,token_hash,label,check_id,encode
from agentguard.management.limits import PLANS,PERIOD,logical_storage
from agentguard.core.manifest import digest
from agentguard.management.redaction import reject_credentials
from agentguard.regression.gate import GateDecision

class Queue:
    def enroll_runner(self,token,project,name):
        with self.db() as db:
            actor=self._auth(db,token,project,'project:admin')
            db.execute('DELETE FROM enrollments WHERE expires<=?',(self.clock(),))
            if db.execute('SELECT count(*) FROM enrollments WHERE project=? AND used=0 AND expires>?',(project,self.clock())).fetchone()[0]>=20:raise AccessError('ENROLLMENT_LIMIT',409)
            if db.execute('SELECT count(*) FROM runners WHERE project=? AND revoked=0',(project,)).fetchone()[0]>=10:raise AccessError('RUNNER_LIMIT',409)
            value='ag7e_'+secrets.token_urlsafe(32)
            db.execute('INSERT INTO enrollments VALUES(?,?,?,?,0,?)',(token_hash(value),project,label(name),self.clock()+600,actor['id']))
            self.event(db,actor['workspace'],project,actor['user'],'runner.invite',project)
            return dict(enrollment_token=value,expires_in_seconds=600)

    def exchange_enrollment(self,enrollment_token):
        if not isinstance(enrollment_token,str) or len(enrollment_token)>256:raise AccessError('UNAUTHENTICATED',401)
        with self.db() as db:
            row=db.execute('SELECT * FROM enrollments WHERE hash=?',(token_hash(enrollment_token),)).fetchone()
            if row is None or row['used'] or row['expires']<=self.clock() or not self._issuer_admin(db,row['issuer'],row['project']):raise AccessError('UNAUTHENTICATED',401)
            db.execute('UPDATE enrollments SET used=1 WHERE hash=?',(row['hash'],));runner=identity();value='ag7r_'+secrets.token_urlsafe(32)
            db.execute('INSERT INTO runners VALUES(?,?,?,?,0,?,?)',(runner,row['project'],row['name'],token_hash(value),self.clock(),row['issuer']))
            return dict(id=runner,project=row['project'],runner_token=value)

    def _runner(self,db,token):
        if not isinstance(token,str) or not 10<=len(token)<=256:raise AccessError('UNAUTHENTICATED',401)
        hashed=token_hash(token);runner=db.execute('SELECT * FROM runners WHERE hash=?',(hashed,)).fetchone()
        if runner is None or runner['revoked'] or not hmac.compare_digest(runner['hash'],hashed) or not self._issuer_admin(db,runner['issuer'],runner['project']):raise AccessError('UNAUTHENTICATED',401)
        db.execute('UPDATE runners SET last_seen=? WHERE id=?',(self.clock(),runner['id']))
        return dict(runner)

    def _revoke_issuer_runners(self,db,project,issuers):
        if not issuers:return
        placeholders=','.join('?' for _ in issuers)
        rows=db.execute(f'SELECT id FROM runners WHERE project=? AND issuer IN ({placeholders})',(project,*issuers)).fetchall()
        for runner in rows:
            db.execute('UPDATE runners SET revoked=1 WHERE id=?',(runner['id'],))
            for job in db.execute("SELECT * FROM jobs WHERE runner=? AND state IN ('leased','running')",(runner['id'],)).fetchall():self._fail_job(db,job,'ISSUER_AUTHORITY_REVOKED_CLEANUP_UNVERIFIED')

    def runners(self,token,project):
        with self.db() as db:
            self._auth(db,token,project,'project:admin')
            return [dict(row) for row in db.execute('SELECT id,name,issuer,last_seen,revoked FROM runners WHERE project=? ORDER BY id LIMIT 100',(project,))]

    def _job_event(self,db,job,project,kind):db.execute('INSERT INTO events(job,project,kind,created) VALUES(?,?,?,?)',(job,project,kind,self.clock()))

    def _fail_job(self,db,job,reason):
        decision=GateDecision(decision_status='unverified',reasons=[reason]).model_dump(mode='json')
        db.execute("UPDATE jobs SET state='error',cancel=1,lease=NULL,cleanup='unverified',decision=?,updated=? WHERE id=?",(encode(decision),self.clock(),job['id']))
        self._job_event(db,job['id'],job['project'],reason)

    def reconcile(self):
        with self.db() as db:return self._reconcile(db)

    def _reconcile(self,db):
        rows=db.execute("SELECT * FROM jobs WHERE state IN ('leased','running') AND lease_until<=?",(self.clock(),)).fetchall()
        for job in rows:self._fail_job(db,job,'LEASE_LOST_EXECUTION_AND_CLEANUP_UNVERIFIED')
        return dict(expired=len(rows),redispatched=0)

    def revoke_runner(self,token,project,runner_id):
        with self.db() as db:
            actor=self._auth(db,token,project,'project:admin')
            if db.execute('SELECT 1 FROM runners WHERE id=? AND project=?',(check_id(runner_id),project)).fetchone() is None:raise AccessError('NOT_FOUND',404)
            db.execute('UPDATE runners SET revoked=1 WHERE id=?',(runner_id,))
            for job in db.execute("SELECT * FROM jobs WHERE runner=? AND state IN ('leased','running')",(runner_id,)).fetchall():self._fail_job(db,job,'RUNNER_REVOKED_CLEANUP_UNVERIFIED')
            self.event(db,actor['workspace'],project,actor['user'],'runner.revoke',runner_id)
        return dict(revoked=True)

    def enqueue(self,token,project,suite,idempotency_key,*,kind='suite',artifact_bytes=256*1024):
        if kind not in {'suite','preflight'} or not isinstance(idempotency_key,str) or not 1<=len(idempotency_key)<=100:raise AccessError('INVALID_JOB',400)
        reject_credentials(idempotency_key)
        if type(artifact_bytes) is not int or not 4096<=artifact_bytes<=1024*1024:raise AccessError('INVALID_ARTIFACT_RESERVATION',400)
        request_hash=digest(dict(suite=suite,kind=kind,artifact_bytes=artifact_bytes))
        with self.lock,self.db() as db:
            actor=self._auth(db,token,project,'run:write');self._reconcile(db)
            from agentguard.management.operations import quota_issues
            if quota_issues(db,project):raise AccessError('QUOTA_LEDGER_UNVERIFIED',409)
            existing=db.execute('SELECT * FROM jobs WHERE project=? AND idempotency=?',(project,idempotency_key)).fetchone()
            if existing:
                if existing['request_hash']!=request_hash:raise AccessError('IDEMPOTENCY_CONFLICT',409)
                return self._job_view(existing)
            payload=self._suite_spec(db,project,suite);payload.update(kind=kind,suite_id=suite,
                retention_days=db.execute('SELECT retention FROM projects WHERE id=?',(project,)).fetchone()[0])
            seconds=sum(int(row['case']['runtime']['policy'].get('timeout_seconds',30))+120 for row in payload['cases'])
            if not 1<=seconds<=3600:raise AccessError('JOB_DEADLINE_LIMIT',400)
            plan=PLANS[db.execute('SELECT plan FROM projects WHERE id=?',(project,)).fetchone()[0]]
            period=int(self.clock()//PERIOD)*PERIOD
            model_configs=[row['case']['runtime'].get('model') for row in payload['cases'] if (row['case']['runtime'].get('model') or {}).get('mode')=='live'] if kind=='suite' else []
            model_tokens=sum(config['max_total_tokens'] for config in model_configs);model_cost=sum(config['max_cost_micro_usd'] for config in model_configs)
            granted=db.execute('SELECT model_tokens,model_cost,model_pricing FROM projects WHERE id=?',(project,)).fetchone()
            from agentguard.management.objects import check_model_pricing
            for config in model_configs:check_model_pricing(config,json.loads(granted['model_pricing']))
            held=db.execute('SELECT COALESCE(SUM(reserved_model_tokens),0),COALESCE(SUM(reserved_model_cost),0) FROM jobs WHERE project=?',(project,)).fetchone()
            charged={row['kind']:row['amount'] for row in db.execute("SELECT kind,SUM(amount) AS amount FROM usage WHERE project=? AND created>=? AND kind IN ('model_tokens','estimated_cost_micro_usd') GROUP BY kind",(project,period))}
            if model_configs and (held[0]+charged.get('model_tokens',0)+model_tokens>min(plan['model_tokens'],granted['model_tokens']) or held[1]+charged.get('estimated_cost_micro_usd',0)+model_cost>min(plan['model_cost'],granted['model_cost'])):raise AccessError('PROJECT_MODEL_BUDGET',409)
            admitted=db.execute("SELECT COALESCE(SUM(amount),0) FROM usage WHERE project=? AND kind='admitted_run' AND created>=?",(project,period)).fetchone()[0]
            active=db.execute("SELECT count(*) FROM jobs WHERE project=? AND state IN ('queued','leased','running')",(project,)).fetchone()[0]
            user_active=db.execute("SELECT count(*) FROM jobs WHERE project=? AND creator=? AND state IN ('queued','leased','running')",(project,actor['user'])).fetchone()[0]
            reserved=db.execute('SELECT COALESCE(SUM(reserved_seconds),0) FROM jobs WHERE project=?',(project,)).fetchone()[0]
            spent=db.execute("SELECT COALESCE(SUM(amount),0) FROM usage WHERE project=? AND kind='lease_wall_seconds' AND created>=?",(project,period)).fetchone()[0]
            spec=encode(payload)
            if admitted>=plan['runs'] or active>=plan['concurrency'] or user_active>=plan['user_concurrency'] or reserved+spent+seconds>plan['seconds']:raise AccessError('RUN_OR_COMPUTE_QUOTA',409)
            if logical_storage(db,project)+len(spec.encode())+artifact_bytes>plan['storage']:raise AccessError('STORAGE_QUOTA',409)
            job=identity();now=self.clock()
            db.execute('INSERT INTO jobs(id,project,creator,idempotency,request_hash,spec,state,created,updated,cleanup,reserved_seconds,reserved_bytes,reserved_model_tokens,reserved_model_cost) VALUES(?,?,?,?,?,?,\'queued\',?,?,\'not_started\',?,?,?,?)',
                (job,project,actor['user'],idempotency_key,request_hash,spec,now,now,seconds,artifact_bytes,model_tokens,model_cost))
            db.execute('INSERT INTO usage VALUES(?,?,?,?,?,?,?)',(identity(),project,job,'admitted_run',1,'server-admission',now))
            self._job_event(db,job,project,'queued');self.event(db,actor['workspace'],project,actor['user'],'run.enqueue',job)
            return self._job_view(db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone())

    def claim(self,runner_token):
        with self.db() as db:
            runner=self._runner(db,runner_token);self._reconcile(db)
            if db.execute("SELECT 1 FROM jobs WHERE runner=? AND state IN ('leased','running')",(runner['id'],)).fetchone():return None
            job=db.execute("SELECT * FROM jobs WHERE project=? AND state='queued' ORDER BY created,id LIMIT 1",(runner['project'],)).fetchone()
            if job is None:return None
            value=secrets.token_urlsafe(32);now=self.clock()
            db.execute("UPDATE jobs SET state='leased',runner=?,lease=?,lease_until=?,started=?,updated=?,cleanup='unverified' WHERE id=? AND state='queued'",(runner['id'],token_hash(value),now+30,now,now,job['id']))
            self._job_event(db,job['id'],job['project'],'leased')
            return dict(id=job['id'],project=job['project'],lease=value,lease_seconds=30,max_seconds=job['reserved_seconds'],spec=json.loads(job['spec']))

    def _leased(self,db,runner_token,job_id,lease):
        runner=self._runner(db,runner_token);self._reconcile(db)
        job=db.execute('SELECT * FROM jobs WHERE id=? AND project=? AND runner=?',(check_id(job_id),runner['project'],runner['id'])).fetchone()
        if job is None:raise AccessError('NOT_FOUND',404)
        if job['state'] not in {'leased','running'} or not isinstance(lease,str) or job['lease'] is None or not hmac.compare_digest(job['lease'],token_hash(lease)):raise AccessError('STALE_LEASE',409)
        return runner,job

    def heartbeat(self,runner_token,job_id,lease):
        with self.db() as db:
            _,job=self._leased(db,runner_token,job_id,lease)
            deadline=job['started']+job['reserved_seconds']
            if deadline<=self.clock():
                self._fail_job(db,job,'JOB_DEADLINE_CLEANUP_UNVERIFIED');return dict(cancel=True,lease_seconds=0)
            db.execute("UPDATE jobs SET state='running',lease_until=?,updated=? WHERE id=?",(min(self.clock()+30,deadline),self.clock(),job_id))
            return dict(cancel=bool(job['cancel']),lease_seconds=30)

    def _job_view(self,job):
        result={key:job[key] for key in ('id','project','state','created','updated','cleanup')}
        result['cleanup_status']=result.pop('cleanup');result['gate_decision']='BLOCK'
        result.update(assurance='customer-private-runner-reported; not independent attestation',authorizes_release=False)
        if job['decision']:
            decision=json.loads(job['decision'])
            if job['state']!='completed' and (decision.get('passed') or decision.get('gate_decision')=='ALLOW'):
                decision=GateDecision(decision_status='unverified',reasons=['NON_COMPLETED_RUN_CANNOT_PASS']).model_dump(mode='json')
            result.update(decision=decision,gate_decision=decision['gate_decision'])
        if job['view']:result['artifact']=json.loads(job['view'])
        return result

    def job(self,token,project,job_id):
        with self.db() as db:
            self._auth(db,token,project);self._reconcile(db)
            job=db.execute('SELECT * FROM jobs WHERE id=? AND project=?',(check_id(job_id),project)).fetchone()
            if job is None:raise AccessError('NOT_FOUND',404)
            return self._job_view(job)

    def jobs(self,token,project,*,after=0,limit=50):
        if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=100:raise AccessError('INVALID_PAGE',400)
        with self.db() as db:
            self._auth(db,token,project);self._reconcile(db)
            return [self._job_view(row) for row in db.execute('SELECT * FROM jobs WHERE project=? ORDER BY created DESC,id LIMIT ? OFFSET ?',(project,limit,after))]

    def events(self,token,project,job_id,*,after=0,limit=100):
        if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=100:raise AccessError('INVALID_PAGE',400)
        with self.db() as db:
            self._auth(db,token,project)
            if db.execute('SELECT 1 FROM jobs WHERE id=? AND project=?',(check_id(job_id),project)).fetchone() is None:raise AccessError('NOT_FOUND',404)
            return [dict(row) for row in db.execute('SELECT id,kind,created FROM events WHERE project=? AND job=? AND id>? ORDER BY id LIMIT ?',(project,job_id,after,limit))]

    def cancel(self,token,project,job_id):
        with self.db() as db:
            actor=self._auth(db,token,project,'run:write');job=db.execute('SELECT * FROM jobs WHERE id=? AND project=?',(check_id(job_id),project)).fetchone()
            if job is None:raise AccessError('NOT_FOUND',404)
            if job['state']=='queued':
                db.execute("UPDATE jobs SET state='cancelled',cancel=1,cleanup='not_required',reserved_seconds=0,reserved_bytes=0,reserved_model_tokens=0,reserved_model_cost=0,updated=? WHERE id=?",(self.clock(),job_id))
            elif job['state'] in {'leased','running'}:db.execute('UPDATE jobs SET cancel=1 WHERE id=?',(job_id,))
            self._job_event(db,job_id,project,'cancel_requested');self.event(db,actor['workspace'],project,actor['user'],'run.cancel',job_id)
        return dict(cancel_requested=True)

    def usage(self,token,project):
        with self.db() as db:
            self._auth(db,token,project);self._reconcile(db);period=int(self.clock()//PERIOD)*PERIOD
            plan=db.execute('SELECT plan FROM projects WHERE id=?',(project,)).fetchone()[0]
            rows={r['kind']:r['total'] for r in db.execute('SELECT kind,SUM(amount) AS total FROM usage WHERE project=? AND created>=? GROUP BY kind',(project,period))}
            held=db.execute('SELECT COALESCE(SUM(reserved_model_tokens),0),COALESCE(SUM(reserved_model_cost),0) FROM jobs WHERE project=?',(project,)).fetchone()
            return dict(plan=plan,limits=PLANS[plan],period_start=period,period_seconds=PERIOD,
                admitted_runs=rows.get('admitted_run',0),lease_wall_seconds=rows.get('lease_wall_seconds',0),
                reserved_seconds=db.execute('SELECT COALESCE(SUM(reserved_seconds),0) FROM jobs WHERE project=?',(project,)).fetchone()[0],
                logical_storage_bytes=logical_storage(db,project),model_usage_source='runner-reported or unknown; no invoice attestation',
                model_tokens=rows.get('model_tokens'),cost_micro_usd=rows.get('estimated_cost_micro_usd'),
                reserved_model_tokens=held[0],reserved_model_cost_micro_usd=held[1],gateway_totals_complete=held[0]==0 and held[1]==0)
