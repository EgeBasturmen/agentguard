"""Observe an owning process without sending a signal (including Windows)."""
import os

class OwnerWatch:
    def __init__(self,pid):
        if type(pid) is not int or pid<=0:raise ValueError('Explicit owner process required')
        self.pid=pid;self.handle=None
        if os.name=='nt':
            import ctypes
            from ctypes import wintypes
            self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            self.kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];self.kernel.OpenProcess.restype=wintypes.HANDLE
            self.kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD];self.kernel.WaitForSingleObject.restype=wintypes.DWORD
            self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
            self.handle=self.kernel.OpenProcess(0x00100000,False,pid)
            self.error=ctypes.get_last_error() if not self.handle else 0
    def alive(self):
        if os.name=='nt':return bool(self.handle) and self.kernel.WaitForSingleObject(self.handle,0)==258
        return os.getppid()==self.pid
    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None
