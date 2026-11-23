"""Transactional local review history and immutable suite snapshots. No authentication claims."""
from copy import deepcopy
from pathlib import Path
import json
import sqlite3
import re
from contextlib import contextmanager
from agentguard.core.manifest import digest
from agentguard.generation.models import Model,Candidate,Source,definitions
from agentguard.generation.validation import validate_candidate
from pydantic import Field,model_validator

class Snapshot(Model):
    schema_version: int=1
    id: str=Field(min_length=1,max_length=200)
    cases: list[dict]=Field(min_length=1,max_length=200)
    requirements: list[dict]=Field(default_factory=list)
    fingerprint: str|None=None
    @model_validator(mode='after')
    def seal(self):
        if type(self.schema_version) is not int or self.schema_version!=1:raise ValueError('Unsupported suite version')
        for row in self.cases:
            if set(row)!={'id','revision','fingerprint','review_fingerprint'} or type(row['revision']) is not int or row['revision']<1 or not isinstance(row['id'],str):raise ValueError('Invalid suite pin')
        if len({row['id'] for row in self.cases})!=len(self.cases):raise ValueError('Duplicate suite member')
        from agentguard.generation.coverage import coverage
        coverage([],self.requirements)
        expected=digest(self.model_dump(exclude={'fingerprint'}))
        if self.fingerprint is not None and self.fingerprint!=expected:raise ValueError('Suite fingerprint mismatch')
        self.fingerprint=expected;return self

class ReviewConflictError(ValueError):
    """An optimistic browser review no longer matches the stored record."""


class ScenarioRepository:
    def __init__(self,path,*,connection=None,schema='main'):
        if not re.fullmatch(r'main|scenario_[0-9a-f]{32}',schema) or connection is None and schema!='main':
            raise ValueError('Invalid scenario database schema')
        self.path=Path(path)
        self._connection=connection
        self._schema=schema
        self.path.parent.mkdir(parents=True,exist_ok=True)
        if self.path.is_symlink():raise ValueError('Repository cannot be a symlink')
        with self._connect() as db:
            tables={row[0] for row in db.execute(f"SELECT name FROM {self._table('sqlite_master')} WHERE type='table'")}
            if tables and 'agentguard_meta' not in tables:raise ValueError('Not an AgentGuard scenario database')
            if tables and [tuple(row) for row in db.execute(f"SELECT version FROM {self._table('agentguard_meta')}")]!=[(1,)]:raise ValueError('Unsupported repository schema')
            # executescript would implicitly commit the caller's control transaction.
            for name,columns in (
                ('agentguard_meta','version INTEGER NOT NULL'),
                ('sources','id TEXT PRIMARY KEY,payload TEXT NOT NULL,digest TEXT NOT NULL'),
                ('cases','id TEXT NOT NULL,revision INTEGER NOT NULL,digest TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,review TEXT,PRIMARY KEY(id,revision)'),
                ('suites','digest TEXT PRIMARY KEY,payload TEXT NOT NULL')):
                db.execute(f'CREATE TABLE IF NOT EXISTS {self._table(name)}({columns})')
            rows=[tuple(row) for row in db.execute(f"SELECT version FROM {self._table('agentguard_meta')}")]
            if not rows:db.execute(f"INSERT INTO {self._table('agentguard_meta')} VALUES(1)")
            elif rows!=[(1,)]:raise ValueError('Unsupported repository schema')
    def _table(self,name):
        if name not in {'agentguard_meta','sources','cases','suites','sqlite_master'}:raise ValueError('Unknown scenario table')
        return f'"{self._schema}"."{name}"'
    def _begin(self,db):
        if self._connection is None:db.execute('BEGIN IMMEDIATE')
    @contextmanager
    def _connect(self):
        if self._connection is not None:
            connection=self._connection
            if not connection.in_transaction:raise ValueError('Scenario repository requires its active control transaction')
            connection.execute('SAVEPOINT scenario_operation')
            try:
                yield connection
            except BaseException:
                connection.execute('ROLLBACK TO scenario_operation')
                connection.execute('RELEASE scenario_operation')
                raise
            else:connection.execute('RELEASE scenario_operation')
            return
        connection=sqlite3.connect(self.path,timeout=10)
        try:
            with connection:yield connection
        finally:connection.close()
    def _load(self,db,identity,revision):
        row=db.execute(f"SELECT payload,digest,status,review FROM {self._table('cases')} WHERE id=? AND revision=?",(identity,revision)).fetchone()
        if row is None:raise KeyError('Unknown scenario revision')
        case=Candidate.model_validate_json(row[0])
        if case.id!=identity or case.revision!=revision or case.fingerprint!=row[1]:raise ValueError('Stored case integrity mismatch')
        return case,row[2],json.loads(row[3]) if row[3] else None
    def get(self,identity,revision):
        with self._connect() as db:return self._load(db,identity,revision)
    def sources(self):
        with self._connect() as db:return [Source.model_validate_json(row[0]) for row in db.execute(f"SELECT payload FROM {self._table('sources')} ORDER BY id")]
    def _sync(self,db,sources):
        for source in sources:
            source=Source.model_validate(source.model_dump())
            previous=db.execute(f"SELECT digest FROM {self._table('sources')} WHERE id=?",(source.id,)).fetchone()
            if previous is not None and previous[0]!=source.fingerprint:
                # Conservative invalidation: no stale rule/contract may silently retain release approval.
                for identity,revision,payload in db.execute(f"SELECT id,revision,payload FROM {self._table('cases')} WHERE status IN ('approved','reviewed')").fetchall():
                    case=Candidate.model_validate_json(payload)
                    if any(s.id==source.id and s.fingerprint!=source.fingerprint for s in case.sources):
                        db.execute(f"UPDATE {self._table('cases')} SET status='stale' WHERE id=? AND revision=?",(identity,revision))
            db.execute(f"INSERT OR REPLACE INTO {self._table('sources')} VALUES(?,?,?)",(source.id,source.model_dump_json(),source.fingerprint))
    def sync_sources(self,sources):
        with self._connect() as db:
            self._begin(db);self._sync(db,sources)
    def add(self,case):
        case=case.checked()
        with self._connect() as db:
            self._begin(db)
            old=db.execute(f"SELECT digest FROM {self._table('cases')} WHERE id=? AND revision=?",(case.id,case.revision)).fetchone()
            if old:
                if old[0]!=case.fingerprint:raise ValueError('Immutable revision already exists; create a new version')
                return case
            self._sync(db,case.sources)
            db.execute(f"UPDATE {self._table('cases')} SET status='stale' WHERE id=? AND status IN ('approved','reviewed')",(case.id,))
            db.execute(f"INSERT INTO {self._table('cases')} VALUES(?,?,?,?,?,NULL)",(case.id,case.revision,case.fingerprint,case.model_dump_json(),'draft'))
        return case
    def _current(self,db,case):
        latest=db.execute(f"SELECT MAX(revision) FROM {self._table('cases')} WHERE id=?",(case.id,)).fetchone()[0]
        if latest!=case.revision:raise ValueError('Revision is superseded/stale')
        for source in case.sources:
            row=db.execute(f"SELECT digest FROM {self._table('sources')} WHERE id=?",(source.id,)).fetchone()
            if row is None or row[0]!=source.fingerprint:raise ValueError('Source binding is stale')
    def review(self,identity,revision,*,reviewer,decisions,fixture_confirmed=False,classification_confirmed=False,notes='',expected_review_fingerprint=None):
        if not isinstance(reviewer,str) or not reviewer.strip() or len(reviewer)>200:raise ValueError('Named reviewer required')
        if not isinstance(notes,str) or len(notes)>8192:raise ValueError('Bounded review notes required')
        if type(fixture_confirmed) is not bool or type(classification_confirmed) is not bool:raise ValueError('Fixture confirmation must be boolean')
        if not isinstance(decisions,dict) or any(v not in {'confirmed','rejected','unknown'} for v in decisions.values()):raise ValueError('Invalid oracle review decisions')
        with self._connect() as db:
            self._begin(db);case,status,previous=self._load(db,identity,revision);self._current(db,case)
            if expected_review_fingerprint is not None:
                if not isinstance(expected_review_fingerprint,str) or expected_review_fingerprint!=(previous['fingerprint'] if previous else ''):
                    raise ReviewConflictError('Stored review changed; reload before saving')
            if status not in {'draft','reviewed'}:raise ValueError('Revision is not reviewable; create a new version')
            validation=validate_candidate(case)
            if not validation.ready:raise ValueError('Validation failed: '+'; '.join(validation.issues))
            if set(decisions)!=set(validation.oracle_ids):raise ValueError('Review must address every current oracle exactly')
            record=dict(case_digest=case.fingerprint,reviewer=reviewer,decisions=decisions,fixture_confirmed=fixture_confirmed,classification_confirmed=classification_confirmed,notes=notes)
            record['fingerprint']=digest(record)
            db.execute(f"UPDATE {self._table('cases')} SET status='reviewed',review=? WHERE id=? AND revision=?",(json.dumps(record),identity,revision))
        return record
    def approve(self,identity,revision,*,reviewer,expected_digest):
        if not isinstance(reviewer,str) or not reviewer.strip() or len(reviewer)>200:raise ValueError('Named approver required')
        with self._connect() as db:
            self._begin(db);case,status,record=self._load(db,identity,revision);self._current(db,case)
            if status!='reviewed' or case.fingerprint!=expected_digest or not record:raise ValueError('Matching reviewed revision required')
            self._verify_review(case,record)
            record['approver']=reviewer;record['fingerprint']=digest({k:v for k,v in record.items() if k!='fingerprint'})
            db.execute(f"UPDATE {self._table('cases')} SET status='approved',review=? WHERE id=? AND revision=?",(json.dumps(record),identity,revision))
        return case
    def _verify_review(self,case,record):
        body={k:v for k,v in record.items() if k!='fingerprint'}
        if digest(body)!=record.get('fingerprint') or record.get('case_digest')!=case.fingerprint:raise ValueError('Review integrity mismatch')
        validation=validate_candidate(case)
        if not validation.ready or set(record['decisions'])!=set(validation.oracle_ids) or not record['fixture_confirmed'] or not record.get('classification_confirmed') or any(value!='confirmed' for value in record['decisions'].values()):
            raise ValueError('Fixture and every oracle require independent explicit confirmation')
    def retire(self,identity,revision):
        with self._connect() as db:
            self._begin(db);self._load(db,identity,revision)
            db.execute(f"UPDATE {self._table('cases')} SET status='retired' WHERE id=? AND revision=?",(identity,revision))
    def revise(self,identity,revision,*,scenario,reason,source_id=None):
        if not isinstance(reason,str) or not reason.strip():raise ValueError('Revision rationale required')
        with self._connect() as db:
            self._begin(db);old,_,_=self._load(db,identity,revision)
            latest=db.execute(f"SELECT MAX(revision) FROM {self._table('cases')} WHERE id=?",(identity,)).fetchone()[0]
            if latest!=revision:raise ValueError('Revise only the latest revision')
            checks=definitions(scenario)
            source=Source(id=source_id or 'manual:'+identity,version=str(latest+1),kind='manual',content={'checks':checks,'reason':reason})
            data=old.model_dump(exclude={'fingerprint'})
            data.update(revision=latest+1,scenario=deepcopy(scenario),unresolved=[],oracle_sources={key:[source.id] for key in checks},sources=[s.model_dump() for s in old.sources if s.id!=source.id]+[source.model_dump()],
                provenance=old.provenance+[dict(method='manual-resolution@1.0.0',reason=reason,sources=[source.id])])
            case=Candidate.model_validate(data);self._sync(db,case.sources)
            db.execute(f"UPDATE {self._table('cases')} SET status='stale' WHERE id=? AND status IN ('approved','reviewed')",(identity,))
            db.execute(f"INSERT INTO {self._table('cases')} VALUES(?,?,?,?,?,NULL)",(identity,case.revision,case.fingerprint,case.model_dump_json(),'draft'))
        return case
    def freeze(self,identity,members,*,requirements=None):
        if not members:raise ValueError('Empty release suite is not allowed')
        with self._connect() as db:
            self._begin(db);pins=[]
            for case_id,revision in members:
                case,status,record=self._load(db,case_id,revision);self._current(db,case)
                if status!='approved':raise ValueError('Only current approved scenarios may enter a release suite')
                self._verify_review(case,record)
                pins.append(dict(id=case_id,revision=revision,fingerprint=case.fingerprint,review_fingerprint=record['fingerprint']))
            snapshot=Snapshot(id=identity,cases=sorted(pins,key=lambda row:row['id']),requirements=requirements or [])
            db.execute(f"INSERT OR IGNORE INTO {self._table('suites')} VALUES(?,?)",(snapshot.fingerprint,snapshot.model_dump_json()))
        return snapshot
    def admission(self,snapshot,pin):
        snapshot=Snapshot.model_validate(snapshot.model_dump())
        with self._connect() as db:
            stored=db.execute(f"SELECT payload FROM {self._table('suites')} WHERE digest=?",(snapshot.fingerprint,)).fetchone()
            if stored is None or Snapshot.model_validate_json(stored[0])!=snapshot:raise ValueError('Unknown or changed frozen suite')
            if pin not in snapshot.cases:raise ValueError('Unknown suite member')
            case,status,record=self._load(db,pin['id'],pin['revision']);self._current(db,case)
            if status!='approved' or not record or not record.get('approver') or case.fingerprint!=pin['fingerprint'] or record['fingerprint']!=pin['review_fingerprint']:raise ValueError('Release admission is stale/unapproved')
            self._verify_review(case,record)
            return case,record

    def import_cases(self,cases):
        imported=[]
        for incoming in cases:
            incoming=incoming.checked()
            with self._connect() as db:
                self._begin(db)
                latest=db.execute(f"SELECT MAX(revision) FROM {self._table('cases')} WHERE id=?",(incoming.id,)).fetchone()[0] or 0
                data=incoming.model_dump(exclude={'fingerprint'});data['revision']=latest+1
                data['provenance'].append(dict(method='dataset-import@1.0.0',reason='Imported content requires local review',sources=[s.id for s in incoming.sources]))
                case=Candidate.model_validate(data);self._sync(db,case.sources)
                db.execute(f"UPDATE {self._table('cases')} SET status='stale' WHERE id=? AND status IN ('approved','reviewed')",(case.id,))
                db.execute(f"INSERT INTO {self._table('cases')} VALUES(?,?,?,?,?,NULL)",(case.id,case.revision,case.fingerprint,case.model_dump_json(),'draft'))
                imported.append(case)
        return imported

    def is_approved(self,case):
        try:
            with self._connect() as db:
                stored,status,review=self._load(db,case.id,case.revision);self._current(db,stored)
                self._verify_review(stored,review)
                return status=='approved' and stored.fingerprint==case.fingerprint and bool(review.get('approver'))
        except Exception:return False

    def check_snapshot(self,snapshot):
        snapshot=Snapshot.model_validate(snapshot.model_dump())
        with self._connect() as db:
            self._begin(db)
            stored=db.execute(f"SELECT payload FROM {self._table('suites')} WHERE digest=?",(snapshot.fingerprint,)).fetchone()
            if stored is None or Snapshot.model_validate_json(stored[0])!=snapshot:raise ValueError('Unknown or altered frozen suite')
            for pin in snapshot.cases:
                case,status,review=self._load(db,pin['id'],pin['revision']);self._current(db,case)
                if status!='approved' or not review or not review.get('approver') or case.fingerprint!=pin['fingerprint'] or review['fingerprint']!=pin['review_fingerprint']:
                    raise ValueError('Frozen suite changed during execution')
                self._verify_review(case,review)
