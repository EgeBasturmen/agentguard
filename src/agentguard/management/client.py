"""Explicit management endpoint only, verified TLS, bounded JSON; no redirects or automatic retries."""
import re
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from agentguard.integrations.http import _json
from agentguard.management.store import AccessError,encode

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class Client:
    def __init__(self,url,token=''):
        parsed=urlsplit(url)
        if parsed.scheme not in {'https','http'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:raise ValueError('Explicit management origin required')
        if parsed.scheme=='http' and parsed.hostname not in {'127.0.0.1','localhost','::1'}:raise ValueError('Remote runner connections require verified HTTPS')
        if not isinstance(token,str) or token and not re.fullmatch(r'ag7r?_[A-Za-z0-9_-]{43}',token):raise ValueError('Explicit AgentGuard credential required; provider keys are not accepted')
        self.url=url.rstrip('/');self.token=token
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    def request(self,path,body=None):
        if not isinstance(path,str) or not path.startswith('/api/') or '..' in path or '\\' in path or '#' in path:raise ValueError('Management API path required')
        payload=encode(body).encode() if body is not None else None
        headers={'Authorization':'Bearer '+self.token,'Accept':'application/json','X-AgentGuard-API-Version':'1'}
        if payload is not None:headers['Content-Type']='application/json'
        request=urllib.request.Request(self.url+path,data=payload,headers=headers,method='POST' if body is not None else 'GET')
        try:
            with self.opener.open(request,timeout=10) as response:
                raw=response.read(4*1024*1024+1)
                if response.status!=200 or len(raw)>4*1024*1024:raise AccessError('RPC_RESPONSE_UNVERIFIED',502)
                return _json(raw)
        except urllib.error.HTTPError as error:
            try:
                raw=error.read(4097);data=_json(raw) if len(raw)<=4096 else {}
                code=data.get('error','REMOTE_OPERATION_FAILED')
                if not isinstance(code,str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,99}',code):code='REMOTE_OPERATION_FAILED'
            except Exception:code='REMOTE_OPERATION_FAILED'
            finally:error.close()
            raise AccessError(code,error.code) from None
        except (urllib.error.URLError,TimeoutError,OSError):raise AccessError('RPC_TRANSPORT_UNVERIFIED',502) from None
