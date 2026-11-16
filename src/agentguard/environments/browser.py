"""Closed test-app Chromium context, owned by an attempt-local service process.

Only declared fixture pages/fields exist. Arbitrary URLs, HTML, scripts, browser
profiles, downloads and external requests are not accepted.
"""
from dataclasses import dataclass,field,replace
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from agentguard.environments.adapters import FilesystemEnvironment
from agentguard.environments.services import identity,contract,TEXT
from agentguard.integrations.http import Endpoint,request_json
from agentguard.core.manifest import canonical_json

def fixture_check(fixture):
    if set(fixture)!={'browser'} or not isinstance(fixture['browser'],dict) or set(fixture['browser'])!={'page','pages'}:raise ValueError('Closed browser fixture required')
    state=fixture['browser'];pages=state['pages']
    if not isinstance(pages,dict) or not 1<=len(pages)<=10 or state['page'] not in pages or len(canonical_json(fixture).encode())>65536:raise ValueError('Browser fixture limits')
    for key,page in pages.items():
        identity(key)
        if not isinstance(page,dict) or set(page)!={'fields','text','submitted'} or not isinstance(page['fields'],dict) or len(page['fields'])>20 or type(page['submitted']) is not bool or not isinstance(page['text'],str):raise ValueError('Invalid fixture page')
        for name,value in page['fields'].items():
            identity(name)
            if name in {'fixture-text','fixture-submit'}:raise ValueError('Reserved fixture DOM identity')
            if not isinstance(value,str) or len(value)>4096:raise ValueError('Invalid fixture field')

@dataclass
class BrowserEnvironment(FilesystemEnvironment):
    kind: str='browser'
    _process: object=field(default=None,repr=False,compare=False)
    validate_fixture=staticmethod(fixture_check)

    def __getstate__(self):return {**self.__dict__,'_process':None}
    def clone(self):return replace(self,_process=None)
    def provision(self,fixture):
        fixture_check(fixture)
        # Filesystem lifecycle allocates only this attempt's namespace.
        super().provision(fixture)
    def reset(self,fixture):
        fixture_check(fixture)
        if self._process is None:
            config=self._path('config.json')
            descriptor=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(descriptor,'w',encoding='utf-8') as stream:json.dump(dict(key=secrets.token_urlsafe(32),fixture=fixture,owner_pid=os.getpid(),namespace=self.namespace),stream)
            self._process=subprocess.Popen([sys.executable,'-m','agentguard.environments.browser_service',self.root],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            deadline=time.monotonic()+15
            while not self._path('ready.json').exists():
                if self._process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Browser fixture service unavailable; install the browser extra and Chromium')
                time.sleep(.05)
        else:self._request('reset',fixture)
    def _request(self,action,value):
        config=json.loads(self._path('config.json').read_text(encoding='utf-8'))
        port=json.loads(self._path('ready.json').read_text(encoding='utf-8'))['port']
        endpoint=Endpoint('http://127.0.0.1:'+str(port),allow_http=True,private_hosts=frozenset({'127.0.0.1'}),timeout_seconds=4,max_bytes=131072)
        result,_,_=request_json(endpoint,'POST','/',dict(action=action,value=value),headers={'X-AgentGuard-Browser-Key':config['key']})
        if result.get('error'):raise ValueError('Browser fixture operation rejected')
        return result
    def snapshot(self):return self._request('snapshot',{})
    def invoke(self,name,arguments):
        if name not in {'browser.open','browser.fill','browser.submit','browser.text'}:raise PermissionError('Unsupported browser operation')
        if name=='browser.open':identity(arguments.get('page'))
        if name=='browser.fill':identity(arguments.get('field'))
        return self._request(name,arguments)
    def cleanup(self):
        process=self._process
        if process is not None:
            try:
                if process.poll() is None:self._request('close',{})
                process.wait(timeout=10)
                if process.returncode!=0:return 'unverified'
            except Exception:
                if process.poll() is None:process.kill();process.wait(timeout=3)
                return 'unverified'
        return super().cleanup()
    def contracts(self):
        return [contract('browser.open',dict(page=TEXT),['page']),
            contract('browser.fill',dict(field=TEXT,value={'type':'string','maxLength':4096}),['field','value']),
            contract('browser.submit',{},[]),contract('browser.text',{},[],'read')]
