"""Manual pilot commercial/support workflows. No processor charge or invented external pilot proof."""
import json
from agentguard.management.store import AccessError,identity,check_id,label,encode,token_hash
from agentguard.management.redaction import reject_credentials,redact
from agentguard.management.limits import PLANS,logical_storage

class Pilot:
    def operator_model_budget(self,project,*,max_tokens,max_cost_micro_usd,operator,reason,input_rate=1000000,output_rate=10000000,pricing_version='operator-conservative-v1'):
        operator=label(operator);reason=label(reason);pricing_version=label(pricing_version)
        if any(type(n) is not int or n<0 for n in (max_tokens,max_cost_micro_usd,input_rate,output_rate)) or input_rate==0 or output_rate==0:raise AccessError('INVALID_OPERATOR_MODEL_BUDGET',400)
        with self.db() as db:
            record=db.execute('SELECT * FROM projects WHERE id=?',(check_id(project),)).fetchone()
            if record is None:raise AccessError('NOT_FOUND',404)
            plan=PLANS[record['plan']]
            if max_tokens>plan['model_tokens'] or max_cost_micro_usd>plan['model_cost']:raise AccessError('MODEL_BUDGET_EXCEEDS_PLAN',400)
            db.execute('UPDATE projects SET model_tokens=?,model_cost=?,model_pricing=? WHERE id=?',
                (max_tokens,max_cost_micro_usd,encode(dict(input_rate=input_rate,output_rate=output_rate,version=pricing_version)),project))
            if max_tokens==0 or max_cost_micro_usd==0:db.execute("UPDATE jobs SET cancel=1 WHERE project=? AND reserved_model_tokens>0 AND state IN ('leased','running')",(project,))
            self.event(db,record['workspace'],project,operator,'operator.model_budget',token_hash(reason))
        return dict(configured=True,invoice_upper_bound_guaranteed=False,runner_opt_in_required=True)

    def _commercial(self,db,project,kind,value,status):
        reject_credentials(value);payload=encode(value)
        plan=PLANS[db.execute('SELECT plan FROM projects WHERE id=?',(project,)).fetchone()[0]]
        if len(payload.encode())>16384 or logical_storage(db,project)+len(payload.encode())>plan['storage']:raise AccessError('STORAGE_QUOTA',409)
        if db.execute('SELECT count(*) FROM commercial WHERE project=?',(project,)).fetchone()[0]>=500:raise AccessError('SUPPORT_RECORD_LIMIT',409)
        record=identity();db.execute('INSERT INTO commercial VALUES(?,?,?,?,?,?)',(record,project,kind,payload,status,self.clock()))
        return dict(id=record,status=status,**value)

    def request_plan(self,token,project,plan):
        if plan!='pilot':raise AccessError('UNSUPPORTED_PILOT_PLAN',400)
        with self.db() as db:
            actor=self._auth(db,token,project,'billing:write')
            result=self._commercial(db,project,'plan',dict(plan=plan,payment_processed_by_agentguard=False,
                flow='Manual invoice/payment outside AgentGuard; operator confirmation required. No API charge.'),'pending_manual_payment')
            self.event(db,actor['workspace'],project,actor['user'],'plan.request',result['id']);return result

    def operator_activate_plan(self,request_id,*,operator,receipt_reference):
        # Deliberately no HTTP route. Operator must have local private storage access.
        operator=label(operator);reference=label(receipt_reference);reject_credentials(reference)
        with self.db() as db:
            row=db.execute("SELECT * FROM commercial WHERE id=? AND kind='plan' AND status='pending_manual_payment'",(check_id(request_id),)).fetchone()
            if row is None:raise AccessError('INVALID_PLAN_REQUEST',409)
            payload=json.loads(row['payload']);plan=payload['plan']
            if plan not in PLANS:raise AccessError('UNKNOWN_PLAN',409)
            payload.update(manual_receipt_reference_digest=token_hash(reference),operator=operator,
                payment_assurance='operator-recorded external payment; not independently verified by AgentGuard')
            db.execute('UPDATE commercial SET status=?,payload=? WHERE id=?',('operator_confirmed_manual',encode(payload),request_id))
            db.execute('UPDATE projects SET plan=? WHERE id=?',(plan,row['project']))
            workspace=db.execute('SELECT workspace FROM projects WHERE id=?',(row['project'],)).fetchone()[0]
            self.event(db,workspace,row['project'],operator,'operator.plan.activate',request_id)
        return dict(activated=plan,payment_processed_by_agentguard=False)

    def request_cancellation(self,token,project):
        with self.db() as db:
            actor=self._auth(db,token,project,'billing:write')
            result=self._commercial(db,project,'cancel',dict(no_automatic_charge=True,flow='Operator cancels external billing and confirms downgrade.'),'pending_operator_cancellation')
            self.event(db,actor['workspace'],project,actor['user'],'plan.cancel.request',result['id']);return result

    def operator_cancel_plan(self,request_id,*,operator,reason):
        operator=label(operator);reason=label(reason);reject_credentials(reason)
        with self.db() as db:
            row=db.execute("SELECT * FROM commercial WHERE id=? AND kind='cancel' AND status='pending_operator_cancellation'",(check_id(request_id),)).fetchone()
            if row is None:raise AccessError('INVALID_CANCELLATION_REQUEST',409)
            if db.execute("SELECT 1 FROM jobs WHERE project=? AND state IN ('queued','leased','running')",(row['project'],)).fetchone():raise AccessError('ACTIVE_RUNS_PREVENT_IMMEDIATE_DOWNGRADE',409)
            db.execute("UPDATE projects SET plan='developer' WHERE id=?",(row['project'],))
            payload=json.loads(row['payload']);payload.update(operator=operator,reason=reason)
            db.execute("UPDATE commercial SET status='operator_cancelled',payload=? WHERE id=?",(encode(payload),request_id))
            workspace=db.execute('SELECT workspace FROM projects WHERE id=?',(row['project'],)).fetchone()[0]
            self.event(db,workspace,row['project'],operator,'operator.plan.cancel',request_id)
        return dict(cancelled=True,plan='developer',external_refund_not_automated=True)

    def feedback(self,token,project,*,category,title,body,job_id=None):
        if category not in {'setup','bug','billing','feedback'} or not isinstance(body,str) or not body.strip() or len(body)>8192:raise AccessError('INVALID_FEEDBACK',400)
        with self.db() as db:
            actor=self._auth(db,token,project,'support:write')
            if job_id and db.execute('SELECT 1 FROM jobs WHERE id=? AND project=?',(check_id(job_id),project)).fetchone() is None:raise AccessError('NOT_FOUND',404)
            fields=json.loads(db.execute('SELECT redact FROM projects WHERE id=?',(project,)).fetchone()[0])
            result=self._commercial(db,project,'feedback',redact(dict(category=category,title=label(title),body=body,
                job_id=job_id,external_validation=False,history=[]),fields),'open')
            self.event(db,actor['workspace'],project,actor['user'],'feedback.create',result['id']);return result

    def feedback_item(self,token,project,ticket):
        with self.db() as db:
            self._auth(db,token,project);row=db.execute("SELECT * FROM commercial WHERE id=? AND project=? AND kind='feedback'",(check_id(ticket),project)).fetchone()
            if row is None:raise AccessError('NOT_FOUND',404)
            return dict(id=ticket,status=row['status'],**json.loads(row['payload']))

    def transition_feedback(self,token,project,ticket,status,*,note,baseline_job=None,candidate_job=None):
        note=label(note);reject_credentials(note)
        with self.db() as db:
            actor=self._auth(db,token,project,'support:write');row=db.execute("SELECT * FROM commercial WHERE id=? AND project=? AND kind='feedback'",(check_id(ticket),project)).fetchone()
            if row is None:raise AccessError('NOT_FOUND',404)
            value=json.loads(row['payload']);transitions={'open':{'investigating'},'investigating':{'fix_ready' if value['category']=='bug' else 'resolved'},'fix_ready':{'retested'},'retested':{'closed'},'resolved':{'closed'}}
            if status not in transitions.get(row['status'],set()):raise AccessError('INVALID_FEEDBACK_TRANSITION',409)
            if status=='retested':
                reports=[]
                for job in (baseline_job,candidate_job):
                    result=db.execute("SELECT * FROM jobs WHERE id=? AND project=? AND state='completed'",(check_id(job),project)).fetchone()
                    if result is None:raise AccessError('RETEST_REQUIRES_COMPLETED_RUNS',409)
                    reports.append(result)
                old,new=reports
                if self._job_view(old)['gate_decision']!='BLOCK' or self._job_view(new)['gate_decision']!='ALLOW' or json.loads(old['spec'])['snapshot']!=json.loads(new['spec'])['snapshot'] or json.loads(old['spec'])['connection']['artifact_digest']==json.loads(new['spec'])['connection']['artifact_digest']:raise AccessError('RETEST_REQUIRES_SAME_ORACLE_AND_CHANGED_AGENT',409)
            if len(value['history'])>=50:raise AccessError('FEEDBACK_HISTORY_LIMIT',409)
            value['history'].append(dict(status=status,note=note,actor=actor['user'],time=self.clock(),baseline=baseline_job,candidate=candidate_job))
            db.execute('UPDATE commercial SET status=?,payload=? WHERE id=?',(status,encode(value),ticket))
            self.event(db,actor['workspace'],project,actor['user'],'feedback.transition',ticket)
            return dict(id=ticket,status=status,external_validation=False)

    def pilot_readiness(self,token,project):
        with self.db() as db:
            self._auth(db,token,project)
            return dict(ag075_status='PENDING_EXTERNAL_VALIDATION',external_pilot_verified=False,
                independent_external_teams=None,self_service_success_verified=False,
                completed_runs=db.execute("SELECT COUNT(*) FROM jobs WHERE project=? AND state='completed'",(project,)).fetchone()[0],
                feedback_records=db.execute("SELECT COUNT(*) FROM commercial WHERE project=? AND kind='feedback'",(project,)).fetchone()[0],
                explanation='Technical fixtures and declared feedback are not independent 3–5-team onboarding/retention/bug evidence.')
