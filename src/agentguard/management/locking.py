"""Reentrant per-state lock shared by threads and controller/operator processes."""
from contextlib import contextmanager
import os
import threading
import time

_registry={};_mutex=threading.Lock()

@contextmanager
def state_lock(root,timeout=15):
    key=str(root.resolve())
    with _mutex:entry=_registry.setdefault(key,(threading.RLock(),threading.local()))
    lock,local=entry
    with lock:
        if getattr(local,'depth',0):
            local.depth+=1
            try:yield
            finally:local.depth-=1
            return
        path=root/'state.lock'
        if path.is_symlink() or path.exists() and path.stat().st_nlink!=1:raise ValueError('Unsafe state lock')
        descriptor=os.open(path,os.O_RDWR|os.O_CREAT,0o600)
        try:
            if os.fstat(descriptor).st_size==0:os.write(descriptor,b'0')
            deadline=time.monotonic()+timeout
            while True:
                try:
                    if os.name=='nt':
                        import msvcrt
                        os.lseek(descriptor,0,0);msvcrt.locking(descriptor,msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic()>=deadline:raise TimeoutError('State operation is busy') from None
                    time.sleep(.02)
            local.depth=1
            try:yield
            finally:
                local.depth=0
                if os.name=='nt':
                    os.lseek(descriptor,0,0);msvcrt.locking(descriptor,msvcrt.LK_UNLCK,1)
                else:fcntl.flock(descriptor,fcntl.LOCK_UN)
        finally:os.close(descriptor)
