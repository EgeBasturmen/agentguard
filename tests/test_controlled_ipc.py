"""Lifecycle order, explicit completion and cancellation over real subprocess pipes."""
import json
import subprocess
import sys
import threading
import time
import pytest
from agentguard.execution.process_ipc import ProcessIPC, ProcessIPCError
from agentguard.execution.ipc_dispatcher import IPCDispatcher
from agentguard.execution.tool_broker import ToolBroker
from agentguard.execution.tool_registry import ToolRegistry
from agentguard.execution.records import RuntimeManifest
from agentguard.execution.errors import ExecutionCancelled

RUN_ID='a'*32
START=dict(protocol_version=2,type='started',run_id=RUN_ID)
DONE=dict(protocol_version=2,type='completed',run_id=RUN_ID,status='ok')


@pytest.mark.parametrize('frames,code',[
    ([],0), ([START],0), ([DONE],0), ([START,DONE,DONE],0),
    ([dict(START,run_id='b'*32)],0), ([START,dict(DONE,passed=True)],0),
    ([START,DONE],3), ([START,dict(DONE,output='x'*9000)],0),
])
def test_invalid_terminal_evidence_is_refused(frames,code):
    program='import sys\n'
    for frame in frames: program += 'print('+repr(json.dumps(frame))+',flush=True)\n'
    program += f'sys.exit({code})'
    process=subprocess.Popen([sys.executable,'-u','-c',program],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    ipc=ProcessIPC(process,IPCDispatcher(ToolBroker(registry=ToolRegistry())),timeout_seconds=3,
        run_manifest=RuntimeManifest(run_id=RUN_ID))
    try:
        with pytest.raises(ProcessIPCError): ipc.serve()
    finally:
        if process.poll() is None: process.kill()
        process.wait(timeout=2)
        ipc.close()
        for stream in (process.stdin,process.stdout,process.stderr): stream.close()


def test_cancellation_after_stdout_close_does_not_wait_for_run_deadline():
    process=subprocess.Popen([sys.executable,'-u','-c','import os,time; os.close(1); time.sleep(10)'],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    event=threading.Event()
    timer=threading.Timer(0.2,event.set)
    ipc=ProcessIPC(process,IPCDispatcher(ToolBroker(registry=ToolRegistry())),timeout_seconds=5,
        run_manifest=RuntimeManifest(run_id=RUN_ID),cancel_event=event)
    timer.start(); start=time.monotonic()
    try:
        with pytest.raises(ExecutionCancelled): ipc.serve()
        assert time.monotonic()-start < 2
    finally:
        timer.cancel(); process.kill(); process.wait(timeout=2); ipc.close()
        for stream in (process.stdin,process.stdout,process.stderr): stream.close()


def test_exit_zero_does_not_accept_unfinished_stderr_drain():
    program = ('import subprocess,sys; '
        'subprocess.Popen([sys.executable,"-c","import time; time.sleep(0.6)"], stdout=subprocess.DEVNULL); '
        'print('+repr(json.dumps(START))+',flush=True); '
        'print('+repr(json.dumps(DONE))+',flush=True)')
    process = subprocess.Popen([sys.executable,'-u','-c',program],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    ipc = ProcessIPC(process,IPCDispatcher(ToolBroker(registry=ToolRegistry())),timeout_seconds=0.3,
        run_manifest=RuntimeManifest(run_id=RUN_ID))
    try:
        with pytest.raises(ProcessIPCError): ipc.serve()
    finally:
        process.wait(timeout=2)
        ipc.close()
        for stream in (process.stdin,process.stdout,process.stderr): stream.close()
