"""Deadline-owned management sockets; TLS host verification and no redirects remain.

Nonblocking I/O polls the deadline even when Winsock shutdown cannot interrupt
a timed read. DNS work is a bounded-wait daemon task; TLS verification is retained.
"""
from contextlib import contextmanager
import http.client
import socket
import select
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
    class DeadlineIO:
        # Winsock shutdown/close from another handle does not reliably wake a
        # timed select. Keep I/O nonblocking and poll the owned deadline instead.
        def settimeout(self,value):return super().settimeout(0)
        def _deadline_io(self,operation,reading):
            while True:
                remaining()
                try:return operation()
                except ssl.SSLWantReadError:reading=True
                except ssl.SSLWantWriteError:reading=False
                except BlockingIOError:pass
                except InterruptedError:continue
                select.select([self] if reading else [],[] if reading else [self],[],min(.025,remaining()))
        def recv_into(self,*a,**kw):return self._deadline_io(lambda:super(DeadlineIO,self).recv_into(*a,**kw),True)
        def recv(self,*a,**kw):return self._deadline_io(lambda:super(DeadlineIO,self).recv(*a,**kw),True)
        def send(self,*a,**kw):return self._deadline_io(lambda:super(DeadlineIO,self).send(*a,**kw),False)
        def sendall(self,data,flags=0):
            with memoryview(data) as view:
                offset=0
                while offset<len(view):
                    count=self.send(view[offset:],flags)
                    if not count:raise OSError('RPC connection closed')
                    offset+=count
    class DeadlineSocket(DeadlineIO,socket.socket):pass
    class DeadlineSSL(DeadlineIO,ssl.SSLSocket):
        def do_handshake(self,*a,**kw):return self._deadline_io(lambda:super(DeadlineSSL,self).do_handshake(*a,**kw),True)
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
            sock=DeadlineSocket(family,kind,protocol)
            try:
                socket.socket.settimeout(sock,remaining())
                if source_address:sock.bind(source_address)
                sock.connect(target)
                sock.setblocking(False)
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
        def https_open(self,request):
            context=ssl.create_default_context();context.sslsocket_class=DeadlineSSL
            return self.do_open(factory(HTTPSConnection),request,context=context)
    class HTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            http.client.HTTPConnection.connect(self)
            self.sock=self._context.wrap_socket(self.sock,server_hostname=self._tunnel_host or self.host,do_handshake_on_connect=False)
            self.sock.do_handshake()
    try:
        yield urllib.request.build_opener(urllib.request.ProxyHandler({}),no_redirect(),HTTP(),HTTPS()),expired
    finally:
        timer.cancel()
        with lock:
            for duplicate in duplicates:duplicate.close()
