
import math
import queue
import subprocess
import threading
import time

from agentguard.execution.ipc_dispatcher import IPCDispatcher
from agentguard.execution.ipc_protocol import (
    IPCProtocolError,
    MAX_IPC_MESSAGE_BYTES,
)


class ProcessIPCError(RuntimeError):
    pass


class ProcessIPCTimeout(ProcessIPCError):
    pass


class ProcessIPC:
    """
    Controller-side IPC session.

    Supports sequential tool calls over stdin/stdout.
    Caller remains responsible for process termination,
    resource cleanup and isolation.
    """

    def __init__(
        self,
        process: subprocess.Popen,
        dispatcher: IPCDispatcher,
        timeout_seconds: float = 10.0,
        max_messages: int = 100,
        max_stderr_bytes: int = 64 * 1024,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive.")

        if type(max_messages) is not int or max_messages < 1:
            raise ValueError("max_messages must be a positive integer.")

        if type(max_stderr_bytes) is not int or max_stderr_bytes < 1:
            raise ValueError("max_stderr_bytes must be a positive integer.")

        if process.stdin is None or process.stdout is None:
            raise ValueError("Process must have stdin and stdout pipes.")

        self._process = process
        self._dispatcher = dispatcher
        self._deadline = time.monotonic() + timeout_seconds
        self._max_messages = max_messages
        self._messages_seen = 0

        self._messages = queue.Queue(maxsize=1)
        self._stderr_overflow = threading.Event()
        self._max_stderr_bytes = max_stderr_bytes

        self._reader = threading.Thread(
            target=self._read_worker_output,
            daemon=True,
        )
        self._reader.start()

        self._stderr_reader = None

        if process.stderr is not None:
            self._stderr_reader = threading.Thread(
                target=self._drain_stderr,
                daemon=True,
            )
            self._stderr_reader.start()

    def _read_worker_output(self) -> None:
        stream = self._process.stdout

        try:
            while True:
                line = stream.readline(MAX_IPC_MESSAGE_BYTES + 2)

                if not line:
                    self._messages.put(("eof", None))
                    return

                if len(line) > MAX_IPC_MESSAGE_BYTES + 1:
                    self._messages.put(("error", "MESSAGE_TOO_LARGE"))
                    return

                if not line.endswith(b"\n"):
                    self._messages.put(("error", "INVALID_FRAMING"))
                    return

                self._messages.put(("message", line))

        except Exception:
            self._messages.put(("error", "READ_ERROR"))

    def _drain_stderr(self) -> None:
        stream = self._process.stderr
        total = 0

        try:
            while True:
                chunk = stream.read(4096)

                if not chunk:
                    return

                total += len(chunk)

                if total > self._max_stderr_bytes:
                    self._stderr_overflow.set()
                    return

        except Exception:
            self._stderr_overflow.set()

    def _write_with_deadline(self, data: bytes) -> None:
        finished = threading.Event()
        errors = []

        def writer() -> None:
            try:
                self._process.stdin.write(data)
                self._process.stdin.flush()
            except (OSError, ValueError) as exc:
                errors.append(exc)
            finally:
                finished.set()

        thread = threading.Thread(
            target=writer,
            daemon=True,
        )
        thread.start()

        remaining = self._deadline - time.monotonic()

        if remaining <= 0 or not finished.wait(timeout=remaining):
            raise ProcessIPCTimeout(
                "IPC response write exceeded its deadline."
            )

        if errors:
            raise ProcessIPCError(
                "Could not send IPC response."
            ) from errors[0]

    def serve(self) -> None:
        while True:
            if self._stderr_overflow.is_set():
                raise ProcessIPCError(
                    "Worker stderr exceeded allowed limit."
                )

            remaining = self._deadline - time.monotonic()

            if remaining <= 0:
                raise ProcessIPCTimeout(
                    "IPC session exceeded its deadline."
                )

            try:
                kind, payload = self._messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise ProcessIPCTimeout(
                    "IPC session exceeded its deadline."
                ) from exc

            if self._stderr_overflow.is_set():
                raise ProcessIPCError(
                    "Worker stderr exceeded allowed limit."
                )

            if kind == "eof":
                remaining = self._deadline - time.monotonic()

                if remaining <= 0:
                    raise ProcessIPCTimeout(
                        "Worker exit exceeded its deadline."
                    )

                try:
                    exit_code = self._process.wait(timeout=remaining)
                except subprocess.TimeoutExpired as exc:
                    raise ProcessIPCTimeout(
                        "Worker did not exit after closing stdout."
                    ) from exc

                if exit_code != 0:
                    raise ProcessIPCError(
                        "Worker exited with a non-zero status."
                    )

                return

            if kind == "error":
                raise ProcessIPCError(
                    f"Worker IPC read failed: {payload}"
                )

            self._messages_seen += 1

            if self._messages_seen > self._max_messages:
                raise ProcessIPCError(
                    "Worker exceeded IPC message limit."
                )

            try:
                response = self._dispatcher.dispatch(payload)
            except IPCProtocolError as exc:
                raise ProcessIPCError(
                    "Worker sent an invalid IPC request."
                ) from exc

            self._write_with_deadline(response)
