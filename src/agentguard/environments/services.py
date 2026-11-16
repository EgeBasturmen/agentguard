"""Attempt-owned SQLite record fixtures. No arbitrary SQL or production service credentials."""
from dataclasses import dataclass, replace
from contextlib import contextmanager
import json
import re
import sqlite3
from agentguard.environments.adapters import DocumentEnvironment
from agentguard.core.manifest import canonical_json
from agentguard.integrations.tools import ToolContract

def identity(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',value):raise ValueError('Bounded fixture identity required')
    return value

def contract(name,properties,required,effect='write'):
    return ToolContract(name=name,version='1.0.0',adapter='fixture-services@1.0.0',effect_type=effect,
        input_schema=dict(type='object',properties=properties,required=required,additionalProperties=False),output_schema={'type':'object'})

TEXT={'type':'string','minLength':1,'maxLength':80}

@dataclass
class RecordEnvironment(DocumentEnvironment):
    kind: str='database'

    @property
    def collection(self):
        if self.kind not in {'database','inbox','calendar'}:raise ValueError('Unsupported record fixture')
        return {'database':'tables','inbox':'messages','calendar':'events'}[self.kind]

    def clone(self):return replace(self)

    @contextmanager
    def _db(self):
        path=self._path('records.sqlite')
        db=sqlite3.connect(path,timeout=2)
        try:
            with db:yield db
        finally:db.close()

    def validate_fixture(self,fixture):
        if set(fixture)!={self.collection} or not isinstance(fixture[self.collection],dict):raise ValueError('Declared fixture collection required')
        if len(canonical_json(fixture).encode())>1024*1024:raise ValueError('Fixture size limit')
        groups=fixture[self.collection] if self.kind=='database' else {'default':fixture[self.collection]}
        rows=[]
        for table,records in groups.items():
            identity(table)
            if not isinstance(records,dict):raise ValueError('Fixture records mapping required')
            for key,value in records.items():
                identity(key);self._value(value);rows.append((table,key,canonical_json(value)))
        if len(rows)>1000 or len(groups)>100:raise ValueError('Fixture count limit')
        return groups,rows

    def reset(self,fixture):
        groups,rows=self.validate_fixture(fixture)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records(bucket TEXT,id TEXT,payload TEXT,PRIMARY KEY(bucket,id))')
            db.execute('CREATE TABLE IF NOT EXISTS buckets(name TEXT PRIMARY KEY)')
            db.execute('DELETE FROM records');db.execute('DELETE FROM buckets')
            db.executemany('INSERT INTO buckets VALUES(?)',[(table,) for table in groups])
            db.executemany('INSERT INTO records VALUES(?,?,?)',rows)

    def _value(self,value):
        if not isinstance(value,dict) or len(canonical_json(value).encode())>65536:raise ValueError('Bounded JSON record required')
        if self.kind=='calendar' and ('start' in value or 'end' in value):
            if type(value.get('start')) is not int or type(value.get('end')) is not int or not 0<=value['start']<value['end']<=2**53:raise ValueError('Calendar interval invalid')

    def snapshot(self):
        with self._db() as db:
            groups={r[0]:{} for r in db.execute('SELECT name FROM buckets ORDER BY name')}
            for table,key,value in db.execute('SELECT bucket,id,payload FROM records ORDER BY bucket,id'):groups[table][key]=json.loads(value)
        return {self.collection:groups if self.kind=='database' else groups.get('default',{})}

    def invoke(self,name,arguments):
        if name not in {self.kind+'.'+op for op in ('get','put','delete','list')}:raise PermissionError('Undeclared operation; SQL and external delivery are forbidden')
        op=name.rsplit('.',1)[1];expected=({'table'} if self.kind=='database' else set())|({'id'} if op!='list' else set())|({'value'} if op=='put' else set())
        if set(arguments)!=expected:raise ValueError('Unexpected operation fields')
        table=identity(arguments['table']) if self.kind=='database' else 'default'
        key=identity(arguments['id']) if op!='list' else None
        if op=='put':self._value(arguments['value'])
        with self._db() as db:
            if db.execute('SELECT 1 FROM buckets WHERE name=?',(table,)).fetchone() is None:raise ValueError('Unknown fixture table')
            if op=='put':
                rows=db.execute('SELECT id,payload FROM records').fetchall()
                old=db.execute('SELECT payload FROM records WHERE bucket=? AND id=?',(table,key)).fetchone()
                size=sum(len(r[1].encode()) for r in rows)-(len(old[0].encode()) if old else 0)+len(canonical_json(arguments['value']).encode())
                if (not old and len(rows)>=1000) or size>1024*1024:raise ValueError('Record capacity exceeded')
                db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?)',(table,key,canonical_json(arguments['value'])))
                return {'id':key,'stored':True}
            if op=='delete':
                removed=db.execute('DELETE FROM records WHERE bucket=? AND id=?',(table,key)).rowcount
                return {'id':key,'removed':bool(removed)}
            if op=='get':
                row=db.execute('SELECT payload FROM records WHERE bucket=? AND id=?',(table,key)).fetchone()
                if row is None:raise KeyError('Unknown record')
                return json.loads(row[0])
            return {'records':{k:json.loads(v) for k,v in db.execute('SELECT id,payload FROM records WHERE bucket=? ORDER BY id',(table,))}}

    def contracts(self):
        result=[]
        for op in ('get','put','delete','list'):
            fields={'table':TEXT} if self.kind=='database' else {}
            if op!='list':fields['id']=TEXT
            if op=='put':fields['value']={'type':'object'}
            result.append(contract(self.kind+'.'+op,fields,list(fields),'read' if op in {'get','list'} else 'write'))
        return result
