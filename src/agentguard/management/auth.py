"""Opaque high-entropy keys, current memberships and explicit scopes; no password database."""
import hmac
import json
import secrets
from agentguard.management.store import AccessError,identity,token_hash,label,check_id,encode

SCOPES=frozenset({'project:read','project:admin','agent:write','suite:write','suite:review','run:write','artifact:read','billing:write','support:write'})
ROLES={'owner':SCOPES,'admin':SCOPES,
    'reviewer':frozenset({'project:read','suite:review','run:write','artifact:read','support:write'}),
    'developer':frozenset({'project:read','agent:write','suite:write','run:write','artifact:read','support:write'}),
    'viewer':frozenset({'project:read','artifact:read'})}

class Auth:
    def _key(self,db,workspace,project,user,scopes,ttl,replacing=None):
        if type(ttl) is not int or not 1<=ttl<=90*86400:raise AccessError('INVALID_KEY_TTL',400)
        active=db.execute('SELECT project FROM keys WHERE workspace=? AND revoked=0 AND expires>? AND id!=?',(workspace,self.clock(),replacing or '')).fetchall()
        if len(active)>=128 or project is not None and sum(row['project']==project for row in active)>=32:raise AccessError('KEY_LIMIT',409)
        value='ag7_'+secrets.token_urlsafe(32);key=identity()
        db.execute('INSERT INTO keys(id,hash,workspace,project,user,scopes,expires) VALUES(?,?,?,?,?,?,?)',
            (key,token_hash(value),workspace,project,user,encode(sorted(scopes)),self.clock()+ttl))
        return dict(token=value,key_id=key,workspace=workspace,project=project,user=user)

    def _current_role(self,db,key,project=None):
        if project is not None:
            check_id(project);record=db.execute('SELECT * FROM projects WHERE id=? AND workspace=?',(project,key['workspace'])).fetchone()
            if record is None or key['project'] not in (None,project):raise AccessError('NOT_FOUND',404)
            member=db.execute('SELECT role FROM members WHERE project=? AND user=?',(project,key['user'])).fetchone()
            workspace=db.execute('SELECT role FROM workspace_members WHERE workspace=? AND user=?',(key['workspace'],key['user'])).fetchone()
            role=workspace['role'] if workspace and workspace['role'] in {'owner','admin'} else member['role'] if member else None
        else:
            member=db.execute('SELECT role FROM workspace_members WHERE workspace=? AND user=?',(key['workspace'],key['user'])).fetchone()
            role=member['role'] if member else None
            if key['project'] is not None:
                project=key['project'];member=db.execute('SELECT role FROM members WHERE project=? AND user=?',(project,key['user'])).fetchone()
                role=member['role'] if member else None
        return role

    def _issuer_admin(self,db,issuer,project):
        key=db.execute('SELECT * FROM keys WHERE id=?',(issuer,)).fetchone()
        if key is None or key['revoked'] or key['expires']<=self.clock() or 'project:admin' not in json.loads(key['scopes']):return False
        try:return 'project:admin' in ROLES.get(self._current_role(db,key,project),())
        except AccessError:return False

    def _auth(self,db,token,project=None,scope='project:read'):
        if not isinstance(token,str) or not 10<=len(token)<=256:raise AccessError('UNAUTHENTICATED',401)
        hashed=token_hash(token);key=db.execute('SELECT * FROM keys WHERE hash=?',(hashed,)).fetchone()
        if key is None or not hmac.compare_digest(key['hash'],hashed) or key['revoked'] or key['expires']<=self.clock():raise AccessError('UNAUTHENTICATED',401)
        self.consume_rate(key['id'])
        self.consume_rate('principal:'+token_hash(key['workspace']+'\x00'+key['user']))
        if scope is not None and scope not in json.loads(key['scopes']):raise AccessError()
        role=self._current_role(db,key,project)
        if role not in ROLES or scope is not None and scope not in ROLES[role]:raise AccessError()
        return dict(key,role=role)

    def bootstrap(self,user):
        with self.db() as db:
            if db.execute('SELECT 1 FROM workspaces').fetchone():raise AccessError('ALREADY_INITIALIZED',409)
            return self._workspace(db,label(user),'Initial workspace')

    def _workspace(self,db,user,name):
        workspace=identity();db.execute('INSERT INTO workspaces VALUES(?,?)',(workspace,label(name)))
        db.execute('INSERT INTO workspace_members VALUES(?,?,?)',(workspace,label(user),'owner'))
        result=self._key(db,workspace,None,user,SCOPES,30*86400)
        self.event(db,workspace,None,user,'workspace.create',workspace);return result

    def create_workspace(self,token,user,name):
        with self.db() as db:
            actor=self._auth(db,token,scope='project:admin')
            if actor['project'] is not None:raise AccessError()
            return self._workspace(db,label(user),name)

    def create_project(self,token,name):
        with self.db() as db:
            actor=self._auth(db,token,scope='project:admin')
            if actor['project'] is not None:raise AccessError()
            if db.execute('SELECT COUNT(*) FROM projects WHERE workspace=?',(actor['workspace'],)).fetchone()[0]>=20:raise AccessError('PROJECT_LIMIT',409)
            project=identity();db.execute('INSERT INTO projects(id,workspace,name,plan,retention,redact) VALUES(?,?,?,?,?,?)',(project,actor['workspace'],label(name),'developer',7,'[]'))
            db.execute('INSERT INTO members VALUES(?,?,?)',(project,actor['user'],'owner'))
            self.event(db,actor['workspace'],project,actor['user'],'project.create',project)
            return dict(id=project,name=name,plan='developer')

    def project(self,token,project):
        with self.db() as db:
            self._auth(db,token,project)
            return dict(db.execute('SELECT * FROM projects WHERE id=?',(project,)).fetchone())

    def projects(self,token):
        with self.db() as db:
            actor=self._auth(db,token);result=[]
            for row in db.execute('SELECT * FROM projects WHERE workspace=? ORDER BY id',(actor['workspace'],)):
                if actor['project'] is not None and row['id']!=actor['project']:continue
                member=db.execute('SELECT 1 FROM members WHERE project=? AND user=?',(row['id'],actor['user'])).fetchone()
                owner=db.execute("SELECT 1 FROM workspace_members WHERE workspace=? AND user=? AND role IN ('owner','admin')",(actor['workspace'],actor['user'])).fetchone()
                if member or owner:result.append(dict(row))
            return result

    def add_member(self,token,project,user,role):
        if role not in ROLES or role=='owner':raise AccessError('INVALID_ROLE',400)
        with self.db() as db:
            actor=self._auth(db,token,project,'project:admin');user=label(user)
            old=db.execute('SELECT role FROM members WHERE project=? AND user=?',(project,user)).fetchone()
            if old and old['role']=='owner':raise AccessError()
            db.execute('INSERT INTO members VALUES(?,?,?) ON CONFLICT(project,user) DO UPDATE SET role=excluded.role',(project,user,role))
            if old and old['role']=='admin' and role!='admin':
                ids=[r[0] for r in db.execute('SELECT id FROM keys WHERE workspace=? AND user=?',(actor['workspace'],user))]
                self._revoke_issuer_runners(db,project,ids)
            self.event(db,actor['workspace'],project,actor['user'],'membership.set',user)
        return dict(user=user,role=role)

    def remove_member(self,token,project,user):
        with self.db() as db:
            actor=self._auth(db,token,project,'project:admin')
            row=db.execute('SELECT role FROM members WHERE project=? AND user=?',(project,user)).fetchone()
            if row is None or row['role']=='owner':raise AccessError()
            db.execute('DELETE FROM members WHERE project=? AND user=?',(project,user))
            self._revoke_issuer_runners(db,project,[r[0] for r in db.execute('SELECT id FROM keys WHERE workspace=? AND user=?',(actor['workspace'],user))])
            self.event(db,actor['workspace'],project,actor['user'],'membership.remove',user)
        return dict(removed=True)

    def issue_key(self,token,project,user,scopes,ttl_seconds=30*86400):
        with self.db() as db:
            actor=self._auth(db,token,project,'project:admin')
            role=db.execute('SELECT role FROM members WHERE project=? AND user=?',(project,user)).fetchone()
            if role is None or not isinstance(scopes,list) or not scopes or len(scopes)!=len(set(scopes)) or not set(scopes)<=ROLES[role['role']] or not set(scopes)<=set(json.loads(actor['scopes'])):raise AccessError()
            result=self._key(db,actor['workspace'],project,user,scopes,ttl_seconds)
            self.event(db,actor['workspace'],project,actor['user'],'key.issue',result['key_id']);return result

    def revoke_key(self,token,project,key):
        with self.db() as db:
            actor=self._auth(db,token,project,'project:admin')
            row=db.execute('SELECT id FROM keys WHERE id=? AND project=?',(check_id(key),project)).fetchone()
            if row is None:raise AccessError('NOT_FOUND',404)
            db.execute('UPDATE keys SET revoked=1 WHERE id=?',(key,))
            self._revoke_issuer_runners(db,project,[key])
            self.event(db,actor['workspace'],project,actor['user'],'key.revoke',key)
        return dict(revoked=True)

    def rotate_key(self,token):
        with self.db() as db:
            actor=self._auth(db,token,scope=None)
            scopes=set(json.loads(actor['scopes']))&ROLES[actor['role']]
            if not scopes:raise AccessError()
            result=self._key(db,actor['workspace'],actor['project'],actor['user'],scopes,int(min(30*86400,actor['expires']-self.clock())),replacing=actor['id'])
            db.execute('UPDATE keys SET revoked=1 WHERE id=?',(actor['id'],))
            db.execute('UPDATE runners SET issuer=? WHERE issuer=?',(result['key_id'],actor['id']))
            self.event(db,actor['workspace'],actor['project'],actor['user'],'key.rotate',actor['id']);return result

    def operator_recover_owner(self,workspace,user,*,operator,reason):
        operator=label(operator);reason=label(reason)
        with self.db() as db:
            row=db.execute("SELECT 1 FROM workspace_members WHERE workspace=? AND user=? AND role='owner'",(check_id(workspace),label(user))).fetchone()
            if row is None:raise AccessError('OWNER_NOT_FOUND',404)
            db.execute('UPDATE keys SET revoked=1 WHERE workspace=? AND user=?',(workspace,user))
            issuers=[r[0] for r in db.execute('SELECT id FROM keys WHERE workspace=? AND user=?',(workspace,user))]
            for project in db.execute('SELECT id FROM projects WHERE workspace=?',(workspace,)).fetchall():self._revoke_issuer_runners(db,project[0],issuers)
            result=self._key(db,workspace,None,user,SCOPES,30*86400)
            self.event(db,workspace,None,operator,'operator.owner.recover',token_hash(reason));return result

    def audit_log(self,token,project,*,after=0,limit=100):
        if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=100:raise AccessError('INVALID_PAGE',400)
        with self.db() as db:
            self._auth(db,token,project,'project:admin')
            return [dict(row) for row in db.execute('SELECT id,actor,action,object,created FROM audit WHERE project=? AND id>? ORDER BY id LIMIT ?',(project,after,limit))]
