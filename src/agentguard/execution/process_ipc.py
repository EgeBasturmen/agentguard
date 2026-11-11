
import json
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
        run_manifest=None, cancel_event=None, max_stdout_bytes=1024 * 1024,
        expected_turns=0,
    ) -> None:
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive.")

        if type(max_messages) is not int or max_messages < 1:
            raise ValueError("max_messages must be a positive integer.")

        if type(max_stderr_bytes) is not int or max_stderr_bytes < 1:
            raise ValueError("max_stderr_bytes must be a positive integer.")

        if process.stdin is None or process.stdout is None:
            raise ValueError("Process must have stdin and stdout pipes.")

        self._manifest = run_manifest
        self._cancel = cancel_event
        self._max_stdout_bytes = max_stdout_bytes
        self.completion = None
        self.expected_turns=expected_turns
        self.turn_outputs=[]
        self._stop = threading.Event()
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

    def close(self):
        self._stop.set()
        self._reader.join(timeout=1)
        if self._stderr_reader is not None:
            self._stderr_reader.join(timeout=1)

    def _enqueue(self, item):
        while not self._stop.is_set():
            try:
                self._messages.put(item, timeout=0.05)
                return
            except queue.Full:
                pass

    def _read_worker_output(self) -> None:
        stream = self._process.stdout
        total = 0

        try:
            while True:
                line = stream.readline(MAX_IPC_MESSAGE_BYTES + 2)

                if not line:
                    self._enqueue(("eof", None))
                    return

                total += len(line)
                if total > self._max_stdout_bytes or len(line) > MAX_IPC_MESSAGE_BYTES + 1:
                    self._enqueue(("error", "MESSAGE_TOO_LARGE"))
                    return

                if not line.endswith(b"\n"):
                    self._enqueue(("error", "INVALID_FRAMING"))
                    return

                self._enqueue(("message", line))

        except Exception:
            self._enqueue(("error", "READ_ERROR"))

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

        while not finished.wait(timeout=0.05):
            if self._cancel is not None and self._cancel.is_set():
                from agentguard.execution.errors import ExecutionCancelled
                raise ExecutionCancelled("IPC write cancelled.")
            if time.monotonic() >= self._deadline:
                raise ProcessIPCTimeout("IPC response write exceeded its deadline.")

        if errors:
            raise ProcessIPCError(
                "Could not send IPC response."
            ) from errors[0]

    def serve(self) -> None:
        while True:
            if self._cancel is not None and self._cancel.is_set():
                from agentguard.execution.errors import ExecutionCancelled
                raise ExecutionCancelled("Execution cancelled.")
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
                kind, payload = self._messages.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                continue

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

                while self._process.poll() is None:
                    if self._cancel is not None and self._cancel.is_set():
                        from agentguard.execution.errors import ExecutionCancelled
                        raise ExecutionCancelled("Worker exit cancelled.")
                    if time.monotonic() >= self._deadline:
                        raise ProcessIPCTimeout("Worker did not exit after closing stdout.")
                    time.sleep(0.025)
                exit_code = self._process.returncode

                if exit_code != 0:
                    raise ProcessIPCError(
                        "Worker exited with a non-zero status."
                    )

                if self._stderr_reader is not None:
                    while self._stderr_reader.is_alive():
                        if self._cancel is not None and self._cancel.is_set():
                            from agentguard.execution.errors import ExecutionCancelled
                            raise ExecutionCancelled("Worker stderr drain cancelled.")
                        if time.monotonic() >= self._deadline:
                            raise ProcessIPCTimeout("Worker stderr drain exceeded its deadline.")
                        self._stderr_reader.join(timeout=0.025)
                if self._stderr_overflow.is_set():
                    raise ProcessIPCError("Worker stderr exceeded allowed limit.")
                if self._manifest is not None and self.completion is None:
                    raise ProcessIPCError("Worker exited without explicit completion.")
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

            if self._manifest is not None:
                from agentguard.execution.lifecycle_protocol import Started, Completed,TurnCompleted
                from agentguard.execution.ipc_protocol import decode_ipc_message
                try:
                    header = json.loads(payload)
                    kind = header.get("type")
                    if kind == "started":
                        message = decode_ipc_message(payload, Started)
                        if self._manifest.started or message.run_id != self._manifest.run_id:
                            raise ValueError("Unexpected worker start")
                        self._manifest.started = True
                        continue
                    if not self._manifest.started or self.completion is not None:
                        raise ValueError("Invalid worker lifecycle order")
                    if kind=='turn_completed':
                        message=decode_ipc_message(payload,TurnCompleted)
                        if message.run_id!=self._manifest.run_id or message.turn!=len(self.turn_outputs)+1 or message.turn>self.expected_turns:raise ValueError('Invalid session turn order/identity')
                        self.turn_outputs.append(message.output)
                        continue
                    if kind == "completed":
                        message = decode_ipc_message(payload, Completed)
                        if message.run_id != self._manifest.run_id:
                            raise ValueError("Completion belongs to a different run")
                        if message.status=='ok' and (len(self.turn_outputs)!=self.expected_turns or self.expected_turns and message.output!=self.turn_outputs[-1]):raise ValueError('Session completion lacks declared turn evidence')
                        self.completion = message
                        self._manifest.completed = True
                        continue
                    self._dispatcher._broker.remaining_seconds = max(0, self._deadline-time.monotonic())
                except (ValueError, TypeError, IPCProtocolError) as exc:
                    raise ProcessIPCError("Invalid worker lifecycle message") from exc
            try:
                response = self._dispatcher.dispatch(payload)
            except IPCProtocolError as exc:
                raise ProcessIPCError(
                    "Worker sent an invalid IPC request."
                ) from exc

            if self._manifest is not None and self._dispatcher._broker.runtime_error is not None:
                raise self._dispatcher._broker.runtime_error
            self._write_with_deadline(response)
