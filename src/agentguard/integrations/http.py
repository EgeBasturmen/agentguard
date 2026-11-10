"""Bounded direct HTTP with explicit origin/private-network grants; no proxy or redirects."""
from dataclasses import dataclass
import http.client
import ipaddress
import json
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit, unquote, urlencode
from agentguard.core.manifest import canonical_json
from agentguard.execution.errors import ExecutionCancelled, ExecutionTimeout

@dataclass(frozen=True)
class Endpoint:
    base_url: str
    allow_http: bool = False
    private_hosts: frozenset[str] = frozenset()
    timeout_seconds: float = 10.0
    max_bytes: int = 1024*1024

    def identity(self):
        return dict(base_url=self.base_url,allow_http=self.allow_http,private_hosts=sorted(self.private_hosts),timeout_seconds=self.timeout_seconds,max_bytes=self.max_bytes)

    def __post_init__(self):
        if type(self.allow_http) is not bool or not isinstance(self.private_hosts,frozenset) or any(not isinstance(host,str) or not host for host in self.private_hosts):
            raise ValueError('HTTP/private-network grants require strict boolean and a frozen host set')
        if type(self.timeout_seconds) not in {int,float} or type(self.max_bytes) is not int:
            raise ValueError('HTTP limits require numeric seconds and integer bytes')
        parsed=urlsplit(self.base_url)
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Endpoint requires a fixed HTTP(S) origin without credentials/query/fragment')
        if parsed.scheme=='http' and not self.allow_http: raise PermissionError('Plain HTTP requires an explicit grant')
        if not 0 < self.timeout_seconds <= 60 or not 1 <= self.max_bytes <= 1024*1024:
            raise ValueError('Invalid HTTP bounds')
        validate_path(parsed.path or '/')


def validate_path(path):
    if not isinstance(path,str) or not path.startswith('/') or path.startswith('//') or len(path)>4096:
        raise ValueError('Only bounded origin-relative paths are allowed')
    if any(ord(c)<32 or ord(c)==127 for c in path) or '?' in path or '#' in path or '\\' in path:
        raise ValueError('Unsafe path')
    decoded=path
    for _ in range(4):
        value=unquote(decoded)
        if value==decoded: break
        # Encoded separators can change the server routing/resource boundary.
        if value.count('/')!=decoded.count('/') or '\\' in value: raise ValueError('Encoded path separator')
        decoded=value
    if '%' in decoded or any(part in {'.','..'} for part in decoded.split('/')): raise ValueError('Path traversal')
    return path


def _json(data):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise ValueError('Duplicate HTTP JSON key')
            result[key]=value
        return result
    def reject(value): raise ValueError('Non-finite JSON')
    value=json.loads(data,object_pairs_hook=unique,parse_constant=reject)
    canonical_json(value)
    return value


def request_json(endpoint, method, path='/', body=None, *, headers=None, query=None, cancel_event=None, accepted=(200,201,202,204)):
    endpoint.__post_init__()
    validate_path(path)
    method=method.upper()
    if method not in {'GET','POST','PUT','PATCH','DELETE','HEAD'}: raise ValueError('Unsupported method')
    parsed=urlsplit(endpoint.base_url)
    host=parsed.hostname
    port=parsed.port or (443 if parsed.scheme=='https' else 80)
    deadline=time.monotonic()+endpoint.timeout_seconds
    def check():
        if cancel_event is not None and cancel_event.is_set(): raise ExecutionCancelled('HTTP request cancelled')
        if time.monotonic()>=deadline: raise ExecutionTimeout('HTTP deadline exceeded')
    check()
    resolved=[]; errors=[]; done=threading.Event()
    def resolve():
        try: resolved.extend(socket.getaddrinfo(host,port,type=socket.SOCK_STREAM))
        except Exception as exc: errors.append(exc)
        finally: done.set()
    threading.Thread(target=resolve,daemon=True).start()
    while not done.wait(0.025): check()
    check()
    if errors: raise OSError('Endpoint resolution failed') from errors[0]
    if not resolved: raise OSError('Endpoint has no addresses')
    for row in resolved:
        address=ipaddress.ip_address(row[4][0])
        if address.is_link_local or address.is_multicast or address.is_unspecified:
            raise PermissionError('Link-local/metadata, multicast and unspecified addresses are never allowed')
        if (not address.is_global or address.is_multicast or address.is_unspecified) and host not in endpoint.private_hosts:
            raise PermissionError('Private/reserved endpoint address requires an exact host grant')
    address=resolved[0][4][0]
    connection_type=http.client.HTTPSConnection if parsed.scheme=='https' else http.client.HTTPConnection
    options={'context':ssl.create_default_context()} if parsed.scheme=='https' else {}
    conn=connection_type(host,port,timeout=max(0.001,deadline-time.monotonic()),**options)
    # Resolve once, validate every returned address, then connect only to the pinned address.
    conn._create_connection=lambda ignored,timeout=None,source_address=None,**kw: socket.create_connection((address,port),timeout,source_address)
    payload=None if body is None else canonical_json(body).encode()
    if payload is not None and len(payload)>endpoint.max_bytes: raise ValueError('HTTP request too large')
    request_headers={'Accept':'application/json','Content-Type':'application/json'}
    for key,value in (headers or {}).items():
        if key.lower() in {'host','authorization','proxy-authorization','cookie'} or '\r' in value or '\n' in value:
            raise ValueError('Sensitive/unsafe headers are not supported by this connector')
        request_headers[key]=value
    base=parsed.path.rstrip('/')
    target=base+path
    if query:
        if any(not isinstance(v,(str,int,float,bool)) or v is None for v in query.values()): raise ValueError('Query values must be scalar')
        target+='?'+urlencode(sorted(query.items()))
    if len(target)>8192: raise ValueError('HTTP request target too large')
    try:
        check();conn.request(method,target,body=payload,headers=request_headers)
        response=conn.getresponse()
        if 300<=response.status<400: raise PermissionError('HTTP redirects are disabled')
        data=bytearray()
        while True:
            check()
            if conn.sock is not None: conn.sock.settimeout(max(0.001,deadline-time.monotonic()))
            chunk=response.read1(min(65536,endpoint.max_bytes+1-len(data)))
            if not chunk: break
            data.extend(chunk)
            if len(data)>endpoint.max_bytes: raise ValueError('HTTP response too large')
        check()
        if response.status not in accepted: raise RuntimeError(f'HTTP status {response.status}')
        response_headers={k.lower():v for k,v in response.getheaders()}
        if data:
            if response_headers.get('content-type','').split(';')[0]!='application/json': raise ValueError('Expected JSON HTTP response; SSE is unsupported')
            value=_json(data)
        else: value=None
        return value,response_headers,response.status
    except (TimeoutError,socket.timeout) as exc:
        raise ExecutionTimeout('HTTP deadline exceeded') from exc
    finally: conn.close()
