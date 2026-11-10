"""Bounded, killable trusted tool execution on a disposable state copy."""
import json
import multiprocessing
import time
from agentguard.execution.errors import ExecutionCancelled, ToolDeadlineError, ExecutionCleanupError

MAX_TOOL_RESULT_BYTES = 1024 * 1024


def _invoke(connection, handler, state, arguments):
    try:
        result = handler(state, **arguments)
        payload = json.dumps({"ok": True, "result": result, "state": state}, allow_nan=False).encode()
        if len(payload) > MAX_TOOL_RESULT_BYTES:
            raise ValueError("Tool result/state exceeds size limit.")
    except BaseException as exc:
        payload = json.dumps({"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:1000]}", "cleanup_unverified": isinstance(exc,ExecutionCleanupError)}).encode()
    try:
        connection.send_bytes(payload)
    finally:
        connection.close()


def invoke_transaction(handler, state, arguments, timeout, cancel_event=None):
    context = multiprocessing.get_context("spawn")
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_invoke, args=(writer, handler, state, arguments), daemon=True)
    deadline = time.monotonic() + timeout
    try:
        process.start()
        writer.close()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise ExecutionCancelled("Tool execution cancelled.")
            if time.monotonic() >= deadline:
                raise ToolDeadlineError("Tool deadline exceeded; canonical state was not committed.")
            if reader.poll(min(0.05, max(0.0, deadline - time.monotonic()))):
                result = json.loads(reader.recv_bytes(MAX_TOOL_RESULT_BYTES))
                process.join(timeout=max(0.0, deadline - time.monotonic()))
                if process.is_alive() or time.monotonic() > deadline:
                    raise ToolDeadlineError("Tool did not finish within its deadline.")
                if result.get("cleanup_unverified") is True:
                    raise ExecutionCleanupError(result.get("error","Tool cleanup was not verified"))
                if process.exitcode != 0 or not result["ok"]:
                    raise RuntimeError(result.get("error", "Tool process failed."))
                return result["result"], result["state"]
            if not process.is_alive():
                raise RuntimeError("Tool process exited without evidence.")
    finally:
        if process.pid is not None and process.is_alive():
            process.kill()
            process.join(timeout=2)
            if process.is_alive():
                raise RuntimeError("Tool process termination could not be verified.")
        reader.close()
        writer.close()
