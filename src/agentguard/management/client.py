"""Explicit management endpoint only, verified TLS, bounded JSON; no redirects or automatic retries."""
import re
import urllib.request
import urllib.error
import ssl
import math
import http.client
import time
import hashlib
from agentguard.core.manifest import digest
from urllib.parse import urlsplit
from agentguard.integrations.http import _json
from agentguard.management.store import AccessError,encode
from agentguard.management.rpc_deadline import deadline_opener

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class RPCTransportError(AccessError):
    def __init__(self):super().__init__('RPC_TRANSPORT_UNVERIFIED',502)

class RPCRemoteError(AccessError):pass

class Client:
    def __init__(self,url,token=''):
        parsed=urlsplit(url)
        if parsed.scheme not in {'https','http'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'','/'}:raise ValueError('Explicit management origin required')
        if parsed.scheme=='http' and parsed.hostname not in {'127.0.0.1','localhost','::1'}:raise ValueError('Remote runner connections require verified HTTPS')
        if not isinstance(token,str) or token and not re.fullmatch(r'ag7r?_[A-Za-z0-9_-]{43}',token):raise ValueError('Explicit AgentGuard credential required; provider keys are not accepted')
        self.url=url.rstrip('/');self.token=token
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        self._default_opener=self.opener
    def request(self,path,body=None,*,timeout=10,expected_owner=None):
        url,token=self.url,self.token
        if expected_owner is not None and digest(dict(server=url,credential_hash=hashlib.sha256(token.encode()).hexdigest()))!=expected_owner:raise AccessError('RPC_IDENTITY_CHANGED',409)
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=10:raise ValueError('Bounded RPC timeout required')
        if not isinstance(path,str) or not path.startswith('/api/') or '..' in path or '\\' in path or '#' in path:raise ValueError('Management API path required')
        payload=encode(body).encode() if body is not None else None
        headers={'Authorization':'Bearer '+token,'Accept':'application/json','X-AgentGuard-API-Version':'1'}
        if payload is not None:headers['Content-Type']='application/json'
        request=urllib.request.Request(url+path,data=payload,headers=headers,method='POST' if body is not None else 'GET')
        deadline=time.monotonic()+timeout
        def read_bounded(response,limit):
            data=bytearray()
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise RPCTransportError()
                sock=getattr(getattr(getattr(response,'fp',None),'raw',None),'_sock',None)
                if sock is not None:sock.settimeout(remaining)
                chunk=response.read1(min(65536,limit+1-len(data)))
                if not chunk:
                    expected=response.headers.get('Content-Length')
                    if expected is not None and (not expected.isascii() or not expected.isdigit()):raise AccessError('RPC_PROTOCOL_UNVERIFIED',502)
                    if expected is not None and len(data)!=int(expected):raise RPCTransportError()
                    return bytes(data)
                data.extend(chunk)
                if len(data)>limit:return bytes(data)
        with deadline_opener(url,timeout,NoRedirect) as (bounded,expired):
            return self._exchange(request,timeout,read_bounded,bounded if self.opener is self._default_opener else self.opener,expired)

    def _exchange(self,request,timeout,read_bounded,opener,expired):
        try:
            with opener.open(request,timeout=timeout) as response:
                raw=read_bounded(response,4*1024*1024)
                if response.status!=200 or len(raw)>4*1024*1024:raise AccessError('RPC_RESPONSE_UNVERIFIED',502)
                if response.headers.get_content_type()!='application/json':raise AccessError('RPC_PROTOCOL_UNVERIFIED',502)
                try:return _json(raw)
                except (ValueError,UnicodeError,RecursionError):raise AccessError('RPC_PROTOCOL_UNVERIFIED',502) from None
        except urllib.error.HTTPError as error:
            try:
                raw=read_bounded(error,4096);data=_json(raw) if len(raw)<=4096 else {}
                code=data.get('error')
                if not isinstance(code,str) or not re.fullmatch('[A-Z][A-Z0-9_]{0,99}',code):raise ValueError()
            except RPCTransportError:raise
            except (TimeoutError,OSError,http.client.IncompleteRead,http.client.RemoteDisconnected):raise RPCTransportError() from None
            except Exception:raise AccessError('RPC_PROTOCOL_UNVERIFIED',502) from None
            finally:error.close()
            raise RPCRemoteError(code,error.code) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason,ssl.SSLCertVerificationError):raise AccessError('RPC_TLS_UNVERIFIED',502) from None
            if expired.is_set():raise RPCTransportError() from None
            if isinstance(error.reason,ssl.SSLError):raise AccessError('RPC_TLS_UNVERIFIED',502) from None
            raise RPCTransportError() from None
        except ssl.SSLCertVerificationError:raise AccessError('RPC_TLS_UNVERIFIED',502) from None
        except ssl.SSLError:
            if expired.is_set():raise RPCTransportError() from None
            raise AccessError('RPC_TLS_UNVERIFIED',502) from None
        except (TimeoutError,OSError,http.client.IncompleteRead,http.client.RemoteDisconnected):raise RPCTransportError() from None
        except http.client.HTTPException:
            if expired.is_set():raise RPCTransportError() from None
            raise AccessError('RPC_PROTOCOL_UNVERIFIED',502) from None
