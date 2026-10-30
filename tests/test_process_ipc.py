
import subprocess
import sys
import textwrap
import pytest
from agentguard.execution.ipc_dispatcher import IPCDispatcher
from agentguard.execution.process_ipc import ProcessIPC
from agentguard.execution.tool_broker import ToolBroker
from agentguard.sandbox.commerce import CommerceSandbox
from agentguard.tracing.collector import TraceCollector
from agentguard.execution.process_ipc import (
    ProcessIPCError,
    ProcessIPCTimeout,
)

def test_real_process_tool_call():
    worker_code = textwrap.dedent("""
        import json
        import sys

        request = {
            "protocol_version": 1,
            "type": "tool_call",
            "call_id": "call-1",
            "tool_name": "payment.refund",
            "arguments": {
                "order_id": "ORD-100",
                "amount": 50.0
            }
        }

        print(json.dumps(request), flush=True)

        response = json.loads(sys.stdin.readline())

        if not response["success"]:
            sys.exit(2)
    """)

    process = subprocess.Popen(
        [sys.executable, "-u", "-c", worker_code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    sandbox = CommerceSandbox(
        initial_state={
            "orders": {
                "ORD-100": {
                    "status": "paid",
                    "total": 100.0,
                }
            },
            "refunds": [],
            "customers": {},
        },
        tracer=TraceCollector(),
    )

    broker = ToolBroker(sandbox)
    dispatcher = IPCDispatcher(broker)

    try:
        session = ProcessIPC(
            process,
            dispatcher,
            timeout_seconds=5.0,
        )

        session.serve()

        assert process.wait(timeout=2) == 0
        assert broker.call_count == 1

        state = sandbox.get_state()

        assert state["orders"]["ORD-100"]["status"] == "refunded"
        assert len(state["refunds"]) == 1

    finally:
        if process.poll() is None:
            process.kill()

        process.wait(timeout=2)

        for stream in (
            process.stdin,
            process.stdout,
            process.stderr,
        ):
            if stream is not None:
                stream.close()

def test_worker_nonzero_exit_is_rejected():
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import sys; sys.exit(7)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    sandbox = CommerceSandbox(
        initial_state={
            "orders": {},
            "refunds": [],
            "customers": {},
        },
        tracer=TraceCollector(),
    )

    dispatcher = IPCDispatcher(ToolBroker(sandbox))

    try:
        session = ProcessIPC(
            process,
            dispatcher,
            timeout_seconds=3.0,
        )

        with pytest.raises(ProcessIPCError):
            session.serve()

    finally:
        if process.poll() is None:
            process.kill()

        process.wait(timeout=2)


def test_worker_timeout_is_rejected():
    process = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "import time; time.sleep(10)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    sandbox = CommerceSandbox(
        initial_state={
            "orders": {},
            "refunds": [],
            "customers": {},
        },
        tracer=TraceCollector(),
    )

    dispatcher = IPCDispatcher(ToolBroker(sandbox))

    try:
        session = ProcessIPC(
            process,
            dispatcher,
            timeout_seconds=0.2,
        )

        with pytest.raises(ProcessIPCTimeout):
            session.serve()

    finally:
        if process.poll() is None:
            process.kill()

        process.wait(timeout=2)
