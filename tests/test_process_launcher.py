import subprocess
import threading
import time
from unittest.mock import patch

import pytest

from agentguard.execution.process_launcher import (
    ProcessLaunchError,
    ProcessLaunchTimeout,
    launch_with_deadline,
)


class FakeProcess:
    def __init__(self):
        self.killed = False
        self.waited = False

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True


def test_launch_returns_process():
    fake_process = FakeProcess()

    with patch(
        "agentguard.execution.process_launcher.subprocess.Popen",
        return_value=fake_process,
    ):
        result = launch_with_deadline(
            command=["docker", "--version"],
            deadline=time.monotonic() + 1,
        )

    assert result is fake_process


def test_launch_reports_startup_error():
    with patch(
        "agentguard.execution.process_launcher.subprocess.Popen",
        side_effect=OSError("Docker executable not found"),
    ):
        with pytest.raises(
            ProcessLaunchError,
            match="Could not launch process",
        ):
            launch_with_deadline(
                command=["docker", "--version"],
                deadline=time.monotonic() + 1,
            )


def test_launch_timeout_kills_late_process():
    release = threading.Event()
    fake_process = FakeProcess()

    def delayed_popen(*args, **kwargs):
        release.wait(timeout=2)
        return fake_process

    with patch(
        "agentguard.execution.process_launcher.subprocess.Popen",
        side_effect=delayed_popen,
    ):
        try:
            with pytest.raises(
                ProcessLaunchTimeout,
                match="exceeded its deadline",
            ):
                launch_with_deadline(
                    command=["docker", "--version"],
                    deadline=time.monotonic() + 0.1,
                )
        finally:
            release.set()

        # Testteki launcher thread'inin tamamlanmasını bekle.
        end = time.monotonic() + 2

        while not fake_process.waited:
            if time.monotonic() >= end:
                pytest.fail(
                    "Late-started process was not reaped."
                )

            time.sleep(0.01)

    assert fake_process.killed is True
    assert fake_process.waited is True


def test_timeout_does_not_return_late_process():
    release = threading.Event()
    fake_process = FakeProcess()

    def delayed_popen(*args, **kwargs):
        release.wait(timeout=2)
        return fake_process

    with patch(
        "agentguard.execution.process_launcher.subprocess.Popen",
        side_effect=delayed_popen,
    ):
        try:
            with pytest.raises(ProcessLaunchTimeout):
                launch_with_deadline(
                    command=["docker", "--version"],
                    deadline=time.monotonic() + 0.1,
                )
        finally:
            release.set()

        end = time.monotonic() + 2

        while not fake_process.waited:
            if time.monotonic() >= end:
                pytest.fail(
                    "Late-started process was not reaped."
                )
            time.sleep(0.01)

    assert fake_process.killed is True