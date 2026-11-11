"""Attempt-owned environments. External state is inspected independently of worker/tool claims."""
from copy import deepcopy
from dataclasses import dataclass,field,replace
import hashlib
import json
import re
from pathlib import Path
import shutil
import stat
import tempfile
import time
import uuid
from agentguard.core.manifest import canonical_json
from agentguard.integrations.http import Endpoint,request_json
from agentguard.integrations.tools import ToolContract

@dataclass
class MemoryEnvironment:
    external: bool=False
    kind: str='memory'
    namespace: str=''
    _state: dict=field(default_factory=dict)
    def provision(self,fixture): self.namespace=uuid.uuid4().hex;self._state=deepcopy(fixture)
    def snapshot(self): return deepcopy(self._state)
    def reset(self,fixture): self._state=deepcopy(fixture)
    def cleanup(self): self._state={};return 'verified'
    def identity(self): return dict(kind=self.kind,version='1.0.0')


def safe_relative(name):
    if not isinstance(name,str) or not name or len(name)>1024 or name.startswith('/') or any(c in name for c in '\\:%\x00'):
        raise PermissionError('Unsafe workspace resource')
    parts=name.split('/')
    if any(not part or part in {'.','..'} or part.startswith('.') for part in parts): raise PermissionError('Unsafe workspace path')
    # Portable resources must never resolve to a Win32 device or a normalized alias,
    # including when a Linux-created snapshot is later materialized on Windows.
    for part in parts:
        stem=part.split('.')[0].rstrip(' ').upper()
        if part.endswith((' ','.')) or any(ord(c)<32 or c in '<>"|?*' for c in part) or stem in {'CON','PRN','AUX','NUL','CONIN$','CONOUT$'} or re.fullmatch(r'(?:COM|LPT)[1-9\u00b9\u00b2\u00b3]',stem):
            raise PermissionError('Non-portable/device workspace resource')
    return Path(*parts)

@dataclass
class FilesystemEnvironment:
    external: bool=True
    kind: str='filesystem'
    namespace: str=''
    root: str=''

    def _path(self,name):
        root=Path(self.root)
        if not self.root or root.is_symlink(): raise PermissionError('Unprovisioned or replaced workspace')
        relative=safe_relative(name)
        target=root/relative
        for parent in (target,*target.parents):
            if parent==root: break
            if parent.is_symlink(): raise PermissionError('Symlink workspace resource')
        if not target.resolve().is_relative_to(root.resolve()): raise PermissionError('Workspace escape')
        if target.exists() and (not target.is_file() or target.stat().st_nlink!=1): raise PermissionError('Non-regular/hardlinked workspace resource')
        return target

    def provision(self,fixture):
        if self.root: raise ValueError('Environment already provisioned')
        self.namespace=uuid.uuid4().hex
        self.root=tempfile.mkdtemp(prefix='agentguard-'+self.namespace+'-')
        self.reset(fixture)

    def snapshot(self):
        result={};total=0
        root=Path(self.root)
        if not self.root or root.is_symlink(): raise PermissionError('Invalid workspace')
        for path in sorted(root.rglob('*')):
            if path.is_symlink(): raise PermissionError('Symlink in workspace snapshot')
            if path.is_dir(): continue
            checked=self._path(path.relative_to(root).as_posix())
            if not stat.S_ISREG(checked.stat().st_mode): raise PermissionError('Non-regular workspace resource')
            with checked.open('rb') as source: data=source.read(1024*1024+1)
            total+=len(data)
            if total>1024*1024 or len(result)>=128: raise ValueError('Workspace snapshot limit exceeded')
            result[path.relative_to(root).as_posix()]=data.decode('utf-8')
        return {'files':result}

    def reset(self,fixture):
        if set(fixture)!={'files'} or not isinstance(fixture['files'],dict): raise ValueError('Filesystem fixture requires files mapping')
        if len(canonical_json(fixture).encode())>1024*1024: raise ValueError('Fixture too large')
        for name in fixture['files']: safe_relative(name)
        paths=[(Path(self.root)/safe_relative(name),content) for name,content in fixture['files'].items()]
        if any(not isinstance(content,str) for _,content in paths): raise ValueError('Filesystem contents must be UTF-8 strings')
        # Snapshot validates the entire existing namespace before removing only owned files.
        existing=self.snapshot()['files']
        for name in existing: self._path(name).unlink()
        for directory in sorted((p for p in Path(self.root).rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True): directory.rmdir()
        for target,content in paths:
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(content,encoding='utf-8',newline='')

    def invoke(self,name,arguments):
        if name=='file.read':
            with self._path(arguments['filename']).open('rb') as source: data=source.read(1024*1024+1)
            if len(data)>1024*1024: raise ValueError('File read limit exceeded')
            return data.decode('utf-8')
        if name=='file.write':
            target=self._path(arguments['filename'])
            content=arguments['content']
            if not isinstance(content,str) or len(content.encode())>1024*1024: raise ValueError('Invalid file contents')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content,encoding='utf-8',newline='')
            self.snapshot();return None
        if name=='file.delete': self._path(arguments['filename']).unlink();return None
        raise PermissionError('Unknown filesystem operation')

    def contracts(self):
        result=[]
        for name in ('file.read','file.write','file.delete'):
            properties={'filename':{'type':'string','minLength':1}}
            if name=='file.write': properties['content']={'type':'string'}
            result.append(ToolContract(name=name,version='1.0.0',adapter='filesystem@1.0.0',effect_type='read' if name=='file.read' else 'write',
                input_schema=dict(type='object',properties=properties,required=list(properties),additionalProperties=False),
                output_schema={'type':'string'} if name=='file.read' else {'type':'null'}))
        return result

    def cleanup(self):
        if not self.root: return 'verified'
        root=Path(self.root)
        expected=Path(tempfile.gettempdir()).resolve()
        if root.is_symlink() or root.resolve().parent!=expected or not root.name.startswith('agentguard-'+self.namespace+'-'):
            return 'unverified'
        try:
            if root.exists(): shutil.rmtree(root)
            return 'verified' if not root.exists() else 'unverified'
        except OSError: return 'unverified'

    def identity(self): return dict(kind=self.kind,version='1.0.0')

@dataclass
class DocumentEnvironment(FilesystemEnvironment):
    kind: str='dataset'

    def reset(self,fixture):
        if set(fixture)!={'documents'} or not isinstance(fixture['documents'],dict): raise ValueError('Dataset fixture requires documents')
        for name,document in fixture['documents'].items():
            safe_relative(name)
            if '/' in name or not isinstance(document,dict) or not isinstance(document.get('content'),str): raise ValueError('Invalid document fixture')
        data=canonical_json(fixture)
        if len(data.encode())>1024*1024: raise ValueError('Dataset too large')
        self._path('dataset.json').write_text(data,encoding='utf-8')

    def snapshot(self):
        with self._path('dataset.json').open('rb') as source: data=source.read(1024*1024+1)
        if len(data)>1024*1024: raise ValueError('Dataset too large')
        result=json.loads(data);canonical_json(result)
        return result

    def invoke(self,name,arguments):
        documents=self.snapshot()['documents']
        if name=='document.get': return deepcopy(documents[arguments['document_id']])
        if name=='document.search':
            query=arguments['query'].casefold()
            return [dict(**{k:v for k,v in deepcopy(value).items() if k!="id"},id=identity) for identity,value in sorted(documents.items()) if query in value['content'].casefold()][:100]
        raise PermissionError('Dataset is immutable; unsupported operation')

    def contracts(self):
        result=[]
        for name,argument,output in [('document.get','document_id','object'),('document.search','query','array')]:
            result.append(ToolContract(name=name,version='1.0.0',adapter='dataset@1.0.0',effect_type='read',
                input_schema=dict(type='object',properties={argument:{'type':'string','minLength':1}},required=[argument],additionalProperties=False),output_schema={'type':output}))
        return result

@dataclass
class HTTPEnvironment:
    endpoint: Endpoint
    allow_staging_writes: bool=False
    service_version: str='1.0.0'
    external: bool=True
    kind: str='http'
    namespace: str=''
    deadline: float|None=None

    def __post_init__(self):
        if type(self.allow_staging_writes) is not bool: raise ValueError('Staging writes require a strict boolean grant')

    def _request(self,method,path='/',body=None,**kwargs):
        endpoint=self.endpoint
        if self.deadline is not None:
            remaining=self.deadline-time.monotonic()
            if remaining<=0:
                from agentguard.execution.errors import ExecutionTimeout
                raise ExecutionTimeout('Environment lifecycle deadline exceeded')
            endpoint=replace(endpoint,timeout_seconds=min(endpoint.timeout_seconds,remaining))
        return request_json(endpoint,method,path,body,**kwargs)

    def verify_contract(self):
        value,_,_=self._request('GET','/contract')
        expected=dict(version=self.service_version,namespace_isolation=True,synchronous_effects=True,snapshot=True,reset=True,cleanup=True)
        if value!=expected: raise ValueError('Staging lifecycle capabilities/version are not verified')
        if not self.allow_staging_writes: raise PermissionError('Staging fixture writes require an explicit grant')

    def provision(self,fixture):
        self.verify_contract()
        self.namespace=uuid.uuid4().hex
        self._request('PUT','/spaces/'+self.namespace,fixture)
        if canonical_json(self.snapshot())!=canonical_json(fixture): raise ValueError('Seeded staging fixture does not match')

    def snapshot(self):
        if not self.namespace: raise ValueError('HTTP environment is not provisioned')
        value,_,_=self._request('GET','/spaces/'+self.namespace)
        if not isinstance(value,dict): raise ValueError('Staging snapshot must be an object')
        return value

    def reset(self,fixture):
        self._request('PUT','/spaces/'+self.namespace,fixture)
        if canonical_json(self.snapshot())!=canonical_json(fixture): raise ValueError('Staging reset was not verified')

    def bound_endpoint(self,suffix=''):
        if not self.namespace: raise ValueError('HTTP environment is not provisioned')
        return replace(self.endpoint,base_url=self.endpoint.base_url.rstrip('/')+'/spaces/'+self.namespace+suffix)

    def cleanup(self):
        self.deadline=None
        if not self.namespace: return 'verified'
        try:
            self._request('DELETE','/spaces/'+self.namespace,accepted=(200,204,404))
            _,_,status=self._request('GET','/spaces/'+self.namespace,accepted=(404,))
            return 'verified' if status==404 else 'unverified'
        except Exception: return 'unverified'

    def identity(self): return dict(kind=self.kind,version='1.0.0',service_version=self.service_version,endpoint=self.endpoint.base_url)
