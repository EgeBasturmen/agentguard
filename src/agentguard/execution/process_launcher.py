import subprocess
import threading
import time
from dataclasses import dataclass, field


class ProcessLaunchError(RuntimeError):
    pass


class ProcessLaunchTimeout(ProcessLaunchError):
    def __init__(self, handle: "LaunchHandle"):
        super().__init__(
            "Process launch exceeded its deadline."
        )
        self.handle = handle


@dataclass
class LaunchHandle:
    finished: threading.Event = field(
        default_factory=threading.Event
    )
    cancelled: threading.Event = field(
        default_factory=threading.Event
    )

    process: subprocess.Popen | None = None
    error: Exception | None = None

    _lock: threading.Lock = field(
        default_factory=threading.Lock,
        repr=False,
    )

    def cancel(self) -> None:
        with self._lock:
            self.cancelled.set()

    def wait_until_finished(self, timeout: float) -> bool:
        return self.finished.wait(timeout=timeout)


def launch_with_deadline(
    command: list[str],
    deadline: float,
) -> subprocess.Popen:
    """
    Start a subprocess with a deadline.

    On timeout, the exception exposes a LaunchHandle
    so the caller can observe whether the launcher
    thread has finished.

    This function does not manage Docker containers.
    """

    handle = LaunchHandle()

    def close_streams(process: subprocess.Popen) -> None:
        for stream in (
            process.stdin,
            process.stdout,
            process.stderr,
        ):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def launcher() -> None:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )

            with handle._lock:
                if handle.cancelled.is_set():
                    late_process = True
                else:
                    handle.process = process
                    late_process = False

            if late_process:
                try:
                    process.kill()
                    process.wait(timeout=5)
                finally:
                    close_streams(process)

        except Exception as exc:
            with handle._lock:
                handle.error = exc

        finally:
            handle.finished.set()

    thread = threading.Thread(
        target=launcher,
        daemon=True,
    )
    thread.start()

    remaining = max(
        0.0,
        deadline - time.monotonic(),
    )

    completed = handle.finished.wait(
        timeout=remaining
    )

    if not completed:
        handle.cancel()
        raise ProcessLaunchTimeout(handle)

    with handle._lock:
        process = handle.process
        error = handle.error

    if error is not None:
        raise ProcessLaunchError(
            f"Could not launch process: {error}"
        ) from error

    if process is None:
        raise ProcessLaunchError(
            "Process launch produced no process."
        )

    return process