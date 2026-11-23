"""Single-controller pilot metadata and durable worker leases. No agent imports."""
from contextlib import contextmanager
from pathlib import Path
import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from agentguard.management.locking import state_lock

class AccessError(Exception):
    def __init__(self,code='FORBIDDEN',status=403):self.code=code;self.status=status;super().__init__(code)

def identity():return uuid.uuid4().hex
def token_hash(value):return hashlib.sha256(value.encode()).hexdigest()
def encode(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def check_id(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{32}',value):raise AccessError('NOT_FOUND',404)
    return value
def label(value):
    if not isinstance(value,str) or not value.strip() or len(value)>200 or any(ord(c)<32 for c in value):raise AccessError('INVALID_NAME',400)
    from agentguard.management.redaction import reject_credentials
    reject_credentials(value)
    return value.strip()

SCHEMA='''
CREATE TABLE IF NOT EXISTS control_meta(version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_members(workspace TEXT,user TEXT,role TEXT,PRIMARY KEY(workspace,user));
CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,workspace TEXT NOT NULL,name TEXT NOT NULL,plan TEXT NOT NULL,retention INTEGER NOT NULL,redact TEXT NOT NULL,model_tokens INTEGER NOT NULL DEFAULT 0,model_cost INTEGER NOT NULL DEFAULT 0,model_pricing TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS members(project TEXT,user TEXT,role TEXT,PRIMARY KEY(project,user));
CREATE TABLE IF NOT EXISTS keys(id TEXT PRIMARY KEY,hash TEXT UNIQUE NOT NULL,workspace TEXT NOT NULL,project TEXT,user TEXT NOT NULL,scopes TEXT NOT NULL,expires REAL NOT NULL,revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS rate_limits(key TEXT,window INTEGER,count INTEGER,PRIMARY KEY(key,window));
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,workspace TEXT,project TEXT,actor TEXT,action TEXT,object TEXT,created REAL);
CREATE TABLE IF NOT EXISTS objects(id TEXT PRIMARY KEY,project TEXT,kind TEXT,payload TEXT,created REAL);
CREATE TABLE IF NOT EXISTS enrollments(hash TEXT PRIMARY KEY,project TEXT,name TEXT,expires REAL,used INTEGER DEFAULT 0,issuer TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runners(id TEXT PRIMARY KEY,project TEXT,name TEXT,hash TEXT UNIQUE,revoked INTEGER DEFAULT 0,last_seen REAL,issuer TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,project TEXT,creator TEXT,idempotency TEXT,request_hash TEXT,spec TEXT,state TEXT,runner TEXT,lease TEXT,lease_until REAL,created REAL,updated REAL,started REAL,cancel INTEGER DEFAULT 0,result_hash TEXT,decision TEXT,view TEXT,seal TEXT,cleanup TEXT,reserved_seconds INTEGER,reserved_bytes INTEGER,reserved_model_tokens INTEGER NOT NULL DEFAULT 0,reserved_model_cost INTEGER NOT NULL DEFAULT 0,UNIQUE(project,idempotency));
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,job TEXT,project TEXT,kind TEXT,created REAL);
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,project TEXT,job TEXT,payload TEXT,hash TEXT,expires REAL);
CREATE TABLE IF NOT EXISTS usage(id TEXT PRIMARY KEY,project TEXT,job TEXT,kind TEXT,amount INTEGER,source TEXT,created REAL,UNIQUE(job,kind));
CREATE TABLE IF NOT EXISTS commercial(id TEXT PRIMARY KEY,project TEXT,kind TEXT,payload TEXT,status TEXT,created REAL);
'''

class Store:
    def __init__(self,root,*,rate_limit=120,clock=None,migrate_schema=True):
        self.root=Path(root).absolute()
        if self.root.is_symlink():raise AccessError('UNSAFE_STORAGE',400)
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.db_path=self.root/'control.sqlite'
        if self.db_path.is_symlink():raise AccessError('UNSAFE_STORAGE',400)
        self.clock=clock or time.time;self.rate_limit=rate_limit;self.lock=threading.RLock()
        self._db_local=threading.local()
        self.migrate_schema=migrate_schema
        with state_lock(self.root):self._initialize()

    def _initialize(self):
        self.key_path=self.root/'signing.key'
        if self.key_path.is_symlink():raise AccessError('UNSAFE_STORAGE',400)
        if not self.key_path.exists():
            try:
                descriptor=os.open(self.key_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                with os.fdopen(descriptor,'wb') as stream:stream.write(secrets.token_bytes(32))
            except FileExistsError:pass
        self.signing_key=self.key_path.read_bytes()
        if len(self.signing_key)!=32:raise AccessError('INVALID_STORAGE_KEY',500)
        with self.db() as db:
            names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if names and 'control_meta' not in names:raise AccessError('WRONG_DATABASE',500)
            if names:
                if [r[0] for r in db.execute('SELECT version FROM control_meta')] not in ([1],[2]):raise AccessError('UNSUPPORTED_DATABASE_VERSION',500)
                required={'projects':{'workspace','model_tokens','model_cost','model_pricing'},'jobs':{'started','reserved_model_tokens','reserved_model_cost','seal'},
                    'runners':{'issuer','hash','project'},'enrollments':{'issuer','expires','used'}}
                for table,columns in required.items():
                    if table not in names or not columns<={r[1] for r in db.execute('PRAGMA table_info('+table+')')}:raise AccessError('UNSUPPORTED_DATABASE_SHAPE_NO_IMPLICIT_MIGRATION',500)
            if not names:
                for statement in SCHEMA.split(';'):
                    if statement.strip():db.execute(statement)
            versions=db.execute('SELECT version FROM control_meta').fetchall()
            initialize_journal=not names or [r[0] for r in versions]==[1]
            if not versions:db.execute('INSERT INTO control_meta VALUES(1)')
            from agentguard.management.migrations import migrate
            if not versions or [r[0] for r in versions]==[1]:
                if not versions or self.migrate_schema:migrate(db)
            else:
                for table in ('schema_history','billing_reconciliation','quota_state','recovery_receipts','deletion_applied'):
                    if table not in names:raise AccessError('UNSUPPORTED_DATABASE_SHAPE_NO_IMPLICIT_MIGRATION',500)
            from agentguard.management.operations import journal
            journal(self,initialize=initialize_journal)
        if os.name!='nt':os.chmod(self.db_path,0o600)

    @contextmanager
    def db(self):
        with state_lock(self.root):
            connection=sqlite3.connect(self.db_path,timeout=15);connection.row_factory=sqlite3.Row
            previous=getattr(self._db_local,'connection',None)
            try:
                connection.execute('PRAGMA secure_delete=ON')
                connection.execute('PRAGMA synchronous=FULL')
                with connection:
                    connection.execute('BEGIN IMMEDIATE')
                    if (self.root/'deletions.json').exists():
                        from agentguard.management.operations import apply_deletions
                        apply_deletions(self,connection)
                    self._db_local.connection=connection
                    yield connection
            finally:
                self._db_local.connection=previous
                connection.close()

    def consume_rate(self,key):
        path=self.root/'abuse.sqlite'
        if path.is_symlink():raise AccessError('UNSAFE_STORAGE',400)
        connection=sqlite3.connect(path,timeout=5)
        try:
            with connection:
                connection.execute('CREATE TABLE IF NOT EXISTS attempts(key TEXT,window INTEGER,count INTEGER,PRIMARY KEY(key,window))')
                window=int(self.clock()//60);connection.execute('DELETE FROM attempts WHERE window<?',(window-1,))
                connection.execute('INSERT INTO attempts VALUES(?,?,1) ON CONFLICT(key,window) DO UPDATE SET count=count+1',(key,window))
                count=connection.execute('SELECT count FROM attempts WHERE key=? AND window=?',(key,window)).fetchone()[0]
            if count>self.rate_limit:raise AccessError('RATE_LIMITED',429)
        finally:connection.close()

    def scenario_repository_path(self,project):return self.root/(check_id(project)+'.scenarios.sqlite')

    def event(self,db,workspace,project,actor,action,obj):
        db.execute('INSERT INTO audit(workspace,project,actor,action,object,created) VALUES(?,?,?,?,?,?)',
            (workspace,project,actor,action,obj,self.clock()))

    def scenario_repository(self,project):
        from agentguard.generation.repository import ScenarioRepository
        path=self.scenario_repository_path(project)
        connection=getattr(self._db_local,'connection',None)
        if connection is None:return ScenarioRepository(path)
        # SQLite's multi-file atomic commit requires disk-backed rollback journals,
        # not WAL/OFF/MEMORY. Never silently convert an operator's existing database.
        databases={row[1]:row[2] for row in connection.execute('PRAGMA database_list')}
        if not databases.get('main') or connection.execute('PRAGMA main.journal_mode').fetchone()[0] not in {'delete','truncate','persist'}:
            raise AccessError('ATOMIC_SCENARIO_STORAGE_REQUIRED',409)
        if path.is_symlink() or path.exists() and path.stat().st_nlink!=1:raise AccessError('UNSAFE_STORAGE',400)
        schema='scenario_'+check_id(project)
        if schema not in databases:
            connection.execute(f'ATTACH DATABASE ? AS "{schema}"',(str(path),))
        elif Path(databases[schema]).resolve()!=path.resolve():raise AccessError('UNSAFE_STORAGE',400)
        if connection.execute(f'PRAGMA "{schema}".journal_mode').fetchone()[0] not in {'delete','truncate','persist'}:
            raise AccessError('ATOMIC_SCENARIO_STORAGE_REQUIRED',409)
        if connection.execute(f'PRAGMA "{schema}".synchronous').fetchone()[0]<2:
            raise AccessError('ATOMIC_SCENARIO_STORAGE_REQUIRED',409)
        return ScenarioRepository(path,connection=connection,schema=schema)
