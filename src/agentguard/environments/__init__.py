from .adapters import MemoryEnvironment, FilesystemEnvironment, DocumentEnvironment, HTTPEnvironment
from .services import RecordEnvironment
from .clock import ClockEnvironment
from .browser import BrowserEnvironment

def fixture_environment(kind):
    factories={'memory':MemoryEnvironment,'filesystem':FilesystemEnvironment,'dataset':DocumentEnvironment,
        'browser':BrowserEnvironment,'clock':ClockEnvironment}
    if kind in {'database','inbox','calendar'}:return RecordEnvironment(kind=kind)
    return factories[kind]()
