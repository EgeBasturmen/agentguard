"""Local immutable baseline lifecycle. Audit hashes detect edits, not operator impersonation."""
import json
import re
import sqlite3
from datetime import datetime,timezone,timedelta
from contextlib import contextmanager
from pathlib import Path
from pydantic import Field, model_validator,model_serializer
from agentguard.core.manifest import canonical_json, digest
from agentguard.generation.models import Model, RuntimeTemplate
from agentguard.generation.execution import SuiteExecution
from agentguard.generation.repository import Snapshot
from agentguard.integrations.http import _json


def checked_execution(value):
    raw=value.model_dump(mode='json') if isinstance(value,SuiteExecution) else json.loads(canonical_json(value))
    passed=raw.pop('passed',None);gate=raw.pop('gate_decision',None)
    execution=SuiteExecution.model_validate_json(canonical_json(raw))
    if type(passed) is not bool or passed!=execution.passed or gate!=execution.gate_decision:
        raise ValueError('Suite verdict contradicts its evidence')
    return execution


class FrozenBaseline(Model):
    schema_version: int=1
    name: str=Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    snapshot: Snapshot
    artifacts: dict[str,str]
    runtimes: dict[str,dict]
    execution: dict
    repeat_evidence: dict|None=None
    fingerprint: str|None=None

    @model_serializer(mode='wrap')
    def compatible(self,handler):
        data=handler(self)
        if self.repeat_evidence is None:data.pop('repeat_evidence',None)
        return data

    @model_validator(mode='after')
    def seal(self):
        execution=checked_execution(self.execution)
        ids={row['id'] for row in self.snapshot.cases}
        if not execution.passed or execution.snapshot!=self.snapshot or set(self.artifacts)!=ids or set(self.runtimes)!=ids:
            raise ValueError('Baseline requires a complete passing frozen suite and exact artifact/runtime pins')
        for identity in ids:
            if not re.fullmatch(r'[0-9a-f]{64}',self.artifacts[identity]):raise ValueError('SHA256 artifact identity required')
            RuntimeTemplate.model_validate(self.runtimes[identity])
            result=next(row.result for row in execution.cases if row.id==identity)
            if result.run_manifest:
                manifest=result.run_manifest
                observed=manifest.source_digest if manifest.artifact_kind=='python_source' else manifest.image_id.removeprefix('sha256:')
                if not re.fullmatch(r'[0-9a-f]{64}',observed) or observed!=self.artifacts[identity]:
                    raise ValueError('Artifact identity contradicts controlled source/image evidence')
        if self.repeat_evidence is not None:
            from agentguard.regression.repeats import RepeatBatch,compare_repeats
            batch=RepeatBatch.model_validate(self.repeat_evidence)
            if batch.snapshot!=self.snapshot or batch.attempts[0]['execution']!=self.execution or not compare_repeats(batch,batch).passed:
                raise ValueError('Baseline repeat evidence or policy is unverified')
            for attempt in batch.attempts:
                for member in checked_execution(attempt['execution']).cases:
                    manifest=member.result.run_manifest
                    if manifest:
                        observed=manifest.source_digest if manifest.artifact_kind=='python_source' else manifest.image_id.removeprefix('sha256:')
                        if observed!=self.artifacts[member.id]:raise ValueError('Repeated artifact identity changed')
        expected=digest(self.model_dump(mode='json',exclude={'fingerprint'}))
        if self.fingerprint is not None and self.fingerprint!=expected:raise ValueError('Frozen baseline changed')
        if len(canonical_json(self.model_dump(mode='json')).encode())>8*1024*1024:raise ValueError('Baseline exceeds 8 MiB')
        self.fingerprint=expected
        return self


def audit_identity(actor,reason):
    if not isinstance(actor,str) or not actor.strip() or len(actor)>200:raise ValueError('Named actor required')
    if not isinstance(reason,str) or not reason.strip() or len(reason)>2048:raise ValueError('Bounded rationale required')


class BaselineRepository:
    def __init__(self,path):
        self.path=Path(path)
        if self.path.is_symlink():raise ValueError('Baseline database cannot be a symlink')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as db:
            names={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if names and 'baseline_meta' not in names:raise ValueError('Not a baseline database')
            db.executescript('''CREATE TABLE IF NOT EXISTS baseline_meta(version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS baselines(digest TEXT PRIMARY KEY,payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS baseline_events(baseline TEXT NOT NULL,seq INTEGER NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(baseline,seq));
                CREATE TABLE IF NOT EXISTS baseline_active(name TEXT PRIMARY KEY,digest TEXT NOT NULL);''')
            rows=db.execute('SELECT version FROM baseline_meta').fetchall()
            if not rows:db.execute('INSERT INTO baseline_meta VALUES(1)')
            elif rows!=[(1,)]:raise ValueError('Unsupported baseline database version')

    @contextmanager
    def _connect(self):
        connection=sqlite3.connect(self.path,timeout=10)
        try:
            with connection:yield connection
        finally:connection.close()

    def _history(self,db,key):
        stored=db.execute('SELECT seq,payload FROM baseline_events WHERE baseline=? ORDER BY seq',(key,)).fetchmany(1001)
        if len(stored)>1000:raise ValueError('Baseline audit exceeds bound')
        rows=[_json(row[1]) for row in stored]
        previous=None;state=None
        transitions={None:{'create':'draft'},'draft':{'approve':'approved','retire':'retired'},
            'approved':{'promote':'active','retire':'retired'},'active':{'supersede':'approved','retire':'retired'},'retired':{}}
        for index,row in enumerate(rows,1):
            if set(row)!={'baseline','seq','previous','action','actor','reason','timestamp','fingerprint'}:raise ValueError('Invalid baseline audit')
            audit_identity(row['actor'],row['reason'])
            if not isinstance(row['timestamp'],str) or len(row['timestamp'])>40 or datetime.fromisoformat(row['timestamp']).utcoffset()!=timedelta(0):raise ValueError('UTC audit timestamp required')
            if type(row['seq']) is not int:raise ValueError('Integer audit sequence required')
            if stored[index-1][0]!=index or row['baseline']!=key or row['seq']!=index or row['previous']!=previous or row['fingerprint']!=digest({k:v for k,v in row.items() if k!='fingerprint'}):
                raise ValueError('Baseline audit integrity mismatch')
            state=transitions[state].get(row['action'])
            if state is None:raise ValueError('Invalid baseline lifecycle transition')
            previous=row['fingerprint']
        if not rows:raise ValueError('Missing baseline audit')
        return rows,state

    def _load(self,db,key):
        row=db.execute('SELECT payload FROM baselines WHERE digest=?',(key,)).fetchone()
        if row is None:raise ValueError('Unknown baseline')
        if len(row[0].encode())>8*1024*1024:raise ValueError('Stored baseline exceeds bound')
        raw=_json(row[0])
        if raw.get('fingerprint')!=key:raise ValueError('Missing/mismatched baseline fingerprint')
        record=FrozenBaseline.model_validate(raw)
        _,state=self._history(db,key)
        return record,state

    def _event(self,db,key,action,actor,reason):
        last=db.execute('SELECT seq,payload FROM baseline_events WHERE baseline=? ORDER BY seq DESC LIMIT 1',(key,)).fetchone()
        if last and last[0]>=1000:raise ValueError('Baseline audit exceeds bound')
        row=dict(baseline=key,seq=last[0]+1 if last else 1,previous=json.loads(last[1])['fingerprint'] if last else None,
            action=action,actor=actor,reason=reason,timestamp=datetime.now(timezone.utc).isoformat())
        row['fingerprint']=digest(row)
        db.execute('INSERT INTO baseline_events VALUES(?,?,?)',(key,row['seq'],canonical_json(row)))

    def create(self,name,scenarios,execution,*,artifacts,actor,reason,repeat_evidence=None):
        audit_identity(actor,reason);execution=checked_execution(execution)
        if not execution.passed:raise ValueError('Unverified suite cannot become a baseline')
        scenarios.check_snapshot(execution.snapshot)
        runtimes={pin['id']:scenarios.admission(execution.snapshot,pin)[0].runtime.model_dump(mode='json') for pin in execution.snapshot.cases}
        record=FrozenBaseline(name=name,snapshot=execution.snapshot,artifacts=artifacts,runtimes=runtimes,execution=execution.model_dump(mode='json'),repeat_evidence=repeat_evidence)
        scenarios.check_snapshot(execution.snapshot)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM baselines WHERE digest=?',(record.fingerprint,)).fetchone():raise ValueError('Immutable baseline already exists')
            db.execute('INSERT INTO baselines VALUES(?,?)',(record.fingerprint,record.model_dump_json()))
            self._event(db,record.fingerprint,'create',actor,reason)
        return self.get(record.fingerprint)

    def get(self,key):
        with self._connect() as db:return self._load(db,key)[0]

    def history(self,key):
        with self._connect() as db:
            self._load(db,key)
            return self._history(db,key)[0]

    def active(self,name):
        with self._connect() as db:
            row=db.execute('SELECT digest FROM baseline_active WHERE name=?',(name,)).fetchone()
            if row is None:raise ValueError('No active baseline')
            record,state=self._load(db,row[0])
            if record.name!=name or state!='active':raise ValueError('Active pointer contradicts lifecycle')
            return record

    def approve(self,key,*,scenarios,actor,reason):
        audit_identity(actor,reason)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE');record,state=self._load(db,key)
            scenarios.check_snapshot(record.snapshot)
            if state!='draft':raise ValueError('Only a draft baseline can be approved')
            self._event(db,key,'approve',actor,reason)

    def promote(self,key,*,scenarios,actor,reason,expected_active):
        audit_identity(actor,reason)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE');record,state=self._load(db,key)
            scenarios.check_snapshot(record.snapshot)
            current=db.execute('SELECT digest FROM baseline_active WHERE name=?',(record.name,)).fetchone()
            current=current[0] if current else None
            if state!='approved' or current!=expected_active:raise ValueError('Approval and current active fingerprint required')
            if current:
                old_record,old_state=self._load(db,current)
                if old_state!='active' or old_record.name!=record.name:raise ValueError('Invalid old active baseline')
                self._event(db,current,'supersede',actor,reason)
            self._event(db,key,'promote',actor,reason)
            db.execute('INSERT OR REPLACE INTO baseline_active VALUES(?,?)',(record.name,key))

    def retire(self,key,*,actor,reason):
        audit_identity(actor,reason)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE');_,state=self._load(db,key)
            if state=='retired':raise ValueError('Baseline is already retired')
            self._event(db,key,'retire',actor,reason)
            db.execute('DELETE FROM baseline_active WHERE digest=?',(key,))
