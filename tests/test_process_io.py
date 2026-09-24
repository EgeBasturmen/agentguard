import subprocess
import threading
import time

import pytest

from agentguard.execution.process_io import (
    ProcessIOError,
    ProcessIOTimeout,
    write_with_deadline,
)


class FakeProcess:
    def __init__(self, stdin):
        self.stdin = stdin


class RecordingStdin:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class BlockingStdin:
    def __init__(self):
        self.release = threading.Event()
        self.closed = False

    def write(self, data):
        self.release.wait(timeout=5)

    def flush(self):
        pass

    def close(self):
        self.closed = True


def test_write_with_deadline_sends_data():
    stdin = RecordingStdin()
    process = FakeProcess(stdin)

    write_with_deadline(
        process=process,
        data=b'{"scenario_name":"test"}',
        deadline=time.monotonic() + 1,
    )

    assert stdin.data == b'{"scenario_name":"test"}'
    assert stdin.closed is True


def test_write_with_deadline_times_out():
    stdin = BlockingStdin()
    process = FakeProcess(stdin)

    try:
        with pytest.raises(ProcessIOTimeout):
            write_with_deadline(
                process=process,
                data=b"test",
                deadline=time.monotonic() + 0.1,
            )
    finally:
        # Test bittikten sonra writer thread'ini serbest bırak.
        stdin.release.set()


def test_write_with_deadline_rejects_missing_stdin():
    process = FakeProcess(None)

    with pytest.raises(
        ProcessIOError,
        match="stdin is unavailable",
    ):
        write_with_deadline(
            process=process,
            data=b"test",
            deadline=time.monotonic() + 1,
        )