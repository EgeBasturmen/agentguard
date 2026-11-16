"""Core verdicts, redacted presentation artifacts, current authorization and retention."""
import base64
import hmac
import hashlib
import json
import math
from agentguard.management.store import AccessError,identity,check_id,encode,token_hash
from agentguard.management.redaction import redact
from agentguard.regression.baseline import checked_execution
from agentguard.regression.gate import GateAPI,GateDecision
from agentguard.core.manifest import digest
from agentguard.core.scenario import Scenario

class Results:
    def compare_reports(self,token,project,*,baseline_job,candidate_job,baseline_report,candidate_report):
        from agentguard.regression.repeats import RepeatBatch,compare_repeats
        with self.lock,self.db() as db:
            actor=self._auth(db,token,project,'run:write');self._auth(db,token,project,'artifact:read');batches=[]
            for job_id,report in [(baseline_job,baseline_report),(candidate_job,candidate_report)]:
                job=db.execute("SELECT * FROM jobs WHERE id=? AND project=? AND state='completed'",(check_id(job_id),project)).fetchone()
                if job is None or job['result_hash']!=digest(report) or not job['seal'] or not hmac.compare_digest(job['seal'],self._seal(job)):raise AccessError('PRIVATE_EVIDENCE_DOES_NOT_MATCH_RUN',409)
                artifact=json.loads(job['view'])['id']
                if db.execute('SELECT 1 FROM artifacts WHERE id=? AND project=? AND expires>?',(artifact,project,self.clock())).fetchone() is None:raise AccessError('EVIDENCE_RETENTION_EXPIRED',409)
                spec=json.loads(job['spec']);self._evaluate_report(spec,report)
                if set(report)!={'execution'}:raise AccessError('COMPARISON_REQUIRES_EXECUTION',400)
                execution=checked_execution(report['execution'])
                batches.append(RepeatBatch(snapshot=execution.snapshot,expected_attempts=1,attempts=[dict(index=1,execution=report['execution'])]))
            comparison=compare_repeats(*batches);decision=GateAPI.evaluate(comparison)
            findings=[dict(case_id=row['case_id'],attempt=row['attempt'],comparison_status=row['comparison']['comparison_status'],findings=row['comparison']['findings']) for row in comparison.comparisons]
            fields=json.loads(db.execute('SELECT redact FROM projects WHERE id=?',(project,)).fetchone()[0])
            presentation=redact(dict(view=dict(decision=decision.model_dump(mode='json'),findings=findings,
                baseline=baseline_job,candidate=candidate_job,recomputable=False,assurance='customer-runner-reported comparison; not release approval'),source=[baseline_report,candidate_report]),fields)['view']
            result=self._put(db,project,'comparison',presentation)
            self.event(db,actor['workspace'],project,actor['user'],'run.compare',result['id']);return result

    def _seal(self,job):
        value={key:job[key] for key in ('id','project','request_hash','result_hash','decision','view','cleanup','state')}
        return hmac.new(self.signing_key,encode(value).encode(),hashlib.sha256).hexdigest()

    def _job_view(self,job):
        if job['state']=='completed' and (not job['seal'] or not hmac.compare_digest(job['seal'],self._seal(job))):
            return dict(id=job['id'],project=job['project'],state='error',gate_decision='BLOCK',authorizes_release=False,
                cleanup_status='unverified',decision=GateDecision(decision_status='unverified',reasons=['STORED_RESULT_INTEGRITY_ERROR']).model_dump(mode='json'))
        result=super()._job_view(job)
        if job['state']=='completed':
            info=json.loads(job['view']);retained=not info.get('removed') and info.get('expires',0)>self.clock()
            result['evidence_retained']=retained
            if not retained:
                result['historical_decision']=result.get('decision')
                decision=GateDecision(decision_status='unverified',reasons=['EVIDENCE_REMOVED_OR_RETENTION_EXPIRED']).model_dump(mode='json')
                result.update(gate_decision='BLOCK',decision=decision)
        return result

    def _artifact_metadata(self,db,job_id,info):
        old=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        # A legitimate retention/redaction operation must never sign already-corrupted evidence.
        valid=old is not None and old['state']=='completed' and old['seal'] and hmac.compare_digest(old['seal'],self._seal(old))
        if not valid:return
        db.execute('UPDATE jobs SET view=? WHERE id=?',(encode(info),job_id))
        updated=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
        db.execute('UPDATE jobs SET seal=? WHERE id=?',(self._seal(updated),job_id))

    def _evaluate_report(self,spec,report):
        if spec['kind']=='preflight':
            if set(report)!={'preflight'} or not isinstance(report['preflight'],dict):raise AccessError('INVALID_PREFLIGHT_REPORT',400)
            value=report['preflight']
            if set(value)!={'schema_version','checks','agent_invoked','verification_level'} or type(value['schema_version']) is not int or value['schema_version']!=1 or value['agent_invoked'] is not False:raise AccessError('INVALID_PREFLIGHT_REPORT',400)
            if not isinstance(value['checks'],list) or len(value['checks'])!=len(spec['cases']):raise AccessError('MISSING_PREFLIGHT_CASE',400)
            if {r.get('case_id') for r in value['checks']}!={r['case']['id'] for r in spec['cases']}:raise AccessError('PREFLIGHT_SCOPE_CHANGED',400)
            for row in value['checks']:
                if set(row)!={'case_id','supported','issues','coverage_gaps'} or type(row['supported']) is not bool or not isinstance(row['issues'],list) or not isinstance(row['coverage_gaps'],list) or row['supported']==bool(row['issues']):raise AccessError('INVALID_PREFLIGHT_CHECK',400)
            return GateDecision(decision_status='unverified',reasons=['PREFLIGHT_IS_NOT_BEHAVIORAL_EVIDENCE']),[], 'not_required'
        if set(report)!={'execution'}:raise AccessError('INVALID_REPORT',400)
        execution=checked_execution(report['execution'])
        from agentguard.generation.repository import Snapshot
        if execution.snapshot!=Snapshot.model_validate(spec['snapshot']):raise AccessError('REPORTED_SUITE_CHANGED',400)
        expected={row['case']['id']:row['case'] for row in spec['cases']};runs=[]
        for member in execution.cases:
            run=member.result
            if run is None:continue
            case=expected.get(member.id)
            if case is None or run.comparison_manifest is None and run.status!='error' or run.comparison_manifest is not None and run.comparison_manifest.scenario.model_dump(mode='json',exclude={'generation'})!=Scenario.model_validate(case['scenario']).model_dump(mode='json',exclude={'generation'}):raise AccessError('REPORTED_ORACLE_CHANGED',400)
            transport=spec['connection']['runtime']['agent']['transport']
            if transport!='http' and (run.run_manifest is None and run.status!='error' or run.run_manifest is not None and run.run_manifest.source_digest and run.run_manifest.source_digest!=spec['connection']['artifact_digest']):raise AccessError('REPORTED_ARTIFACT_CHANGED',400)
            model=case['runtime'].get('model')
            if model is not None:
                if run.model_evidence is None and run.status!='error':raise AccessError('MISSING_GATEWAY_USAGE_EVIDENCE',400)
                if run.model_evidence is not None:
                    from agentguard.model_gateway.gateway import GatewayConfig,GatewayEvidence
                    ledger=GatewayEvidence.model_validate(run.model_evidence)
                    if ledger.config!=GatewayConfig.model_validate(model):raise AccessError('MODEL_CONFIG_OR_PRICING_CHANGED',400)
            runs.append(run)
        cleanup='verified' if len(runs)==len(expected) and all(r.cleanup_status=='verified' for r in runs) else 'unverified'
        return GateAPI.evaluate(execution),runs,cleanup

    def finish(self,runner_token,job_id,lease,report):
        if not isinstance(report,dict) or len(encode(report).encode())>2*1024*1024:raise AccessError('REPORT_SIZE_OR_TYPE',413)
        report_hash=digest(report)
        with self.lock,self.db() as db:
            runner=self._runner(db,runner_token)
            existing=db.execute('SELECT * FROM jobs WHERE id=? AND project=? AND runner=?',(check_id(job_id),runner['project'],runner['id'])).fetchone()
            if existing and existing['state']=='completed':
                if not isinstance(lease,str) or not hmac.compare_digest(existing['lease'] or '',token_hash(lease)) or existing['result_hash']!=report_hash:raise AccessError('RESULT_CONFLICT',409)
                return self._job_view(existing)
            _,job=self._leased(db,runner_token,job_id,lease);spec=json.loads(job['spec'])
            if set(report)=={'failure'}:
                if report['failure'] not in {'RUNNER_EXECUTION_ERROR','SOURCE_VERSION_MISMATCH','RUNNER_EXTERNAL_GRANT_REQUIRED'}:raise AccessError('INVALID_RUNNER_FAILURE',400)
                self._fail_job(db,job,report['failure']);return self._job_view(db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone())
            decision,runs,cleanup=self._evaluate_report(spec,report)
            # Fence against review withdrawal and cancellation while customer execution was running.
            try:self._suite_spec(db,job['project'],spec['suite_id'])
            except Exception:decision=GateDecision(decision_status='unverified',reasons=['SUITE_APPROVAL_WITHDRAWN'])
            if job['cancel']:decision=GateDecision(decision_status='unverified',reasons=['RUN_CANCELLED'])
            if self.clock()>job['started']+job['reserved_seconds']:
                self._fail_job(db,job,'JOB_DEADLINE_CLEANUP_UNVERIFIED');return self._job_view(db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone())
            decision.authorizes_release=False
            project=db.execute('SELECT * FROM projects WHERE id=?',(job['project'],)).fetchone()
            view=redact(dict(schema_version=1,kind='agentguard.private-runner-presentation',job=job_id,
                decision=decision.model_dump(mode='json'),evidence=report,recomputable=False,
                original_evidence_digest=report_hash,assurance='Customer runner report; server validated structure/oracle/pins, not remote execution attestation.'),json.loads(project['redact']))
            payload=encode(view)
            if len(payload.encode())>job['reserved_bytes']:raise AccessError('ARTIFACT_RESERVATION_EXCEEDED',413)
            artifact=identity();artifact_hash=digest(view)
            expiry=self.clock()+project['retention']*86400
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?,?)',(artifact,job['project'],job_id,payload,artifact_hash,expiry))
            db.execute("UPDATE jobs SET state='completed',result_hash=?,decision=?,view=?,seal=NULL,cleanup=?,updated=?,reserved_seconds=0,reserved_bytes=0 WHERE id=?",
                (report_hash,encode(decision.model_dump(mode='json')),encode(dict(id=artifact,hash=artifact_hash,expires=expiry)),cleanup,self.clock(),job_id))
            updated=db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            db.execute('UPDATE jobs SET seal=? WHERE id=?',(self._seal(updated),job_id))
            seconds=max(1,math.ceil(self.clock()-job['started']))
            db.execute('INSERT INTO usage VALUES(?,?,?,?,?,?,?)',(identity(),job['project'],job_id,'lease_wall_seconds',seconds,'server-lease-wall-not-cpu-invoice',self.clock()))
            calls=[call for run in runs if run.model_evidence for call in run.model_evidence.get('calls',[])]
            if calls and all(c.get('status')=='completed' and c.get('usage_source')=='provider-reported' for c in calls):
                tokens=sum(c['input_tokens']+c['output_tokens'] for c in calls)
                estimate=sum(c['estimated_cost_micro_usd'] for c in calls)
                for kind,amount in [('model_tokens',tokens),('estimated_cost_micro_usd',estimate)]:db.execute('INSERT INTO usage VALUES(?,?,?,?,?,?,?)',(identity(),job['project'],job_id,kind,amount,'customer-runner/provider-reported-configured-estimate',self.clock()))
            configured=[m for m in spec['cases'] if (m['case']['runtime'].get('model') or {}).get('mode')=='live']
            model_settled=not configured or len(runs)==len(spec['cases']) and all(run.model_evidence is not None and run.model_evidence.get('status')=='passed' for run in runs)
            if model_settled:db.execute('UPDATE jobs SET reserved_model_tokens=0,reserved_model_cost=0 WHERE id=?',(job_id,))
            self._job_event(db,job_id,job['project'],'completed')
            return self._job_view(db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone())

    def set_data_policy(self,token,project,*,retention_days,redact_fields):
        if type(retention_days) is not int or not 1<=retention_days<=30 or not isinstance(redact_fields,list) or len(redact_fields)>50 or any(not isinstance(f,str) or not 1<=len(f)<=100 for f in redact_fields):raise AccessError('INVALID_DATA_POLICY',400)
        with self.db() as db:
            actor=self._auth(db,token,project,'project:admin')
            db.execute('UPDATE projects SET retention=?,redact=? WHERE id=?',(retention_days,encode(redact_fields),project))
            for artifact in db.execute('SELECT * FROM artifacts WHERE project=?',(project,)).fetchall():
                view=redact(json.loads(artifact['payload']),redact_fields);hashed=digest(view)
                expiry=min(artifact['expires'],self.clock()+retention_days*86400)
                db.execute('UPDATE artifacts SET payload=?,hash=?,expires=? WHERE id=?',(encode(view),hashed,expiry,artifact['id']))
                self._artifact_metadata(db,artifact['job'],dict(id=artifact['id'],hash=hashed,expires=expiry))
            self.event(db,actor['workspace'],project,actor['user'],'data.policy',project)
        return dict(retention_days=retention_days,redact_fields=redact_fields)

    def artifact_grant(self,token,project,artifact_id):
        with self.db() as db:
            actor=self._auth(db,token,project,'artifact:read');row=db.execute('SELECT * FROM artifacts WHERE id=? AND project=? AND expires>?',(check_id(artifact_id),project,self.clock())).fetchone()
            if row is None:raise AccessError('NOT_FOUND',404)
            value=encode(dict(key=actor['id'],project=project,artifact=artifact_id,expires=int(self.clock())+60))
            signature=hmac.new(self.signing_key,value.encode(),hashlib.sha256).hexdigest()
            grant=base64.urlsafe_b64encode(value.encode()).decode().rstrip('=')+'.'+signature
            return dict(grant=grant,expires_in_seconds=60,authentication_required=True)

    def artifact(self,token,project,artifact_id,grant):
        with self.db() as db:
            actor=self._auth(db,token,project,'artifact:read')
            try:
                if not isinstance(grant,str) or len(grant)>2048:raise ValueError()
                body,signature=grant.split('.');raw=base64.urlsafe_b64decode(body+'='*(-len(body)%4));value=json.loads(raw)
                if not hmac.compare_digest(signature,hmac.new(self.signing_key,raw,hashlib.sha256).hexdigest()) or set(value)!={'key','project','artifact','expires'} or value['key']!=actor['id'] or value['project']!=project or value['artifact']!=artifact_id or type(value['expires']) is not int or value['expires']<=self.clock():raise ValueError()
            except Exception:raise AccessError('INVALID_ARTIFACT_GRANT',403) from None
            row=db.execute('SELECT * FROM artifacts WHERE id=? AND project=? AND expires>?',(check_id(artifact_id),project,self.clock())).fetchone()
            if row is None:raise AccessError('NOT_FOUND',404)
            view=json.loads(row['payload']);job=db.execute('SELECT * FROM jobs WHERE id=? AND project=?',(row['job'],project)).fetchone()
            if digest(view)!=row['hash'] or job is None or not job['seal'] or not hmac.compare_digest(job['seal'],self._seal(job)) or json.loads(job['view'])!=dict(id=artifact_id,hash=row['hash'],expires=row['expires']):raise AccessError('ARTIFACT_INTEGRITY_ERROR',409)
            return view

    def delete_artifact(self,token,project,artifact_id):
        with self.db() as db:
            actor=self._auth(db,token,project,'project:admin')
            artifact=db.execute('SELECT * FROM artifacts WHERE id=? AND project=?',(check_id(artifact_id),project)).fetchone()
            if artifact is None:raise AccessError('NOT_FOUND',404)
            if db.execute('DELETE FROM artifacts WHERE id=? AND project=?',(check_id(artifact_id),project)).rowcount!=1:raise AccessError('NOT_FOUND',404)
            self._artifact_metadata(db,artifact['job'],dict(id=artifact_id,hash=artifact['hash'],expires=artifact['expires'],removed=True))
            self.event(db,actor['workspace'],project,actor['user'],'artifact.delete',artifact_id)
        return dict(deleted=True,scope='primary artifact payload; metadata/audit retained; backups are operator responsibility')

    def purge_expired(self):
        with self.db() as db:
            for artifact in db.execute('SELECT * FROM artifacts WHERE expires<=?',(self.clock(),)).fetchall():
                self._artifact_metadata(db,artifact['job'],dict(id=artifact['id'],hash=artifact['hash'],expires=artifact['expires'],removed=True))
            count=db.execute('DELETE FROM artifacts WHERE expires<=?',(self.clock(),)).rowcount
            db.execute('DELETE FROM enrollments WHERE expires<=?',(self.clock(),))
        return dict(artifacts_deleted=count)
