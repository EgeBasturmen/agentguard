"""Deadline-owned management sockets; TLS host verification and no redirects remain.

A duplicate socket permits shutdown during headers/TLS/body even when the HTTP
response owns the original descriptor. DNS work is a bounded-wait daemon task.
"""
from contextlib import contextmanager
import http.client
import socket
import ssl
import threading
import time
import urllib.request

@contextmanager
def deadline_opener(url,timeout,no_redirect):
    deadline=time.monotonic()+timeout;expired=threading.Event();duplicates=[];lock=threading.Lock()
    def remaining():
        value=deadline-time.monotonic()
        if value<=0 or expired.is_set():raise TimeoutError('RPC deadline exceeded')
        return value
    def abort():
        expired.set()
        with lock:
            for duplicate in duplicates:
                try:duplicate.shutdown(socket.SHUT_RDWR)
                except OSError:pass
    timer=threading.Timer(timeout,abort);timer.daemon=True;timer.start()
    def connect(address,timeout=None,source_address=None,**kwargs):
        host,port=address;rows=[];errors=[];done=threading.Event()
        def resolve():
            try:rows.extend(socket.getaddrinfo(host,port,type=socket.SOCK_STREAM))
            except Exception:errors.append(True)
            finally:done.set()
        threading.Thread(target=resolve,daemon=True).start()
        while not done.wait(min(.025,remaining())):remaining()
        remaining()
        if errors or not rows:raise OSError('RPC resolution unavailable')
        for family,kind,protocol,_,target in rows:
            sock=socket.socket(family,kind,protocol)
            try:
                sock.settimeout(remaining())
                if source_address:sock.bind(source_address)
                sock.connect(target)
                duplicate=sock.dup()
                with lock:
                    duplicates.append(duplicate)
                    if expired.is_set():
                        try:duplicate.shutdown(socket.SHUT_RDWR)
                        except OSError:pass
                remaining();return sock
            except OSError:sock.close();remaining()
        raise OSError('RPC connection unavailable')
    def factory(kind):
        def create(host,**kwargs):
            kwargs['timeout']=remaining()
            conn=kind(host,**kwargs);conn._create_connection=connect
            return conn
        return create
    class HTTP(urllib.request.HTTPHandler):
        def http_open(self,request):return self.do_open(factory(http.client.HTTPConnection),request)
    class HTTPS(urllib.request.HTTPSHandler):
        def https_open(self,request):return self.do_open(factory(http.client.HTTPSConnection),request,context=ssl.create_default_context())
    try:
        yield urllib.request.build_opener(urllib.request.ProxyHandler({}),no_redirect(),HTTP(),HTTPS()),expired
    finally:
        timer.cancel()
        with lock:
            for duplicate in duplicates:duplicate.close()
