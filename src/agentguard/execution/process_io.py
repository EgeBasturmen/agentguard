import subprocess
import threading
import time

class ProcessIOError(RuntimeError):
    pass


class ProcessIOTimeout(ProcessIOError):
    pass


def write_with_deadline(
    process: subprocess.Popen,
    data: bytes,
    deadline: float,
) -> None:
    """
    Send data to a subprocess without blocking the
    calling thread indefinitely.

    The caller remains responsible for terminating
    the subprocess and cleaning up its resources.
    """

    finished = threading.Event()
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            if process.stdin is None:
                raise ProcessIOError(
                    "Process stdin is unavailable."
                )

            process.stdin.write(data)
            process.stdin.flush()

        except BaseException as exc:
            errors.append(exc)

        finally:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass

            finished.set()

    thread = threading.Thread(
        target=writer,
        daemon=True,
    )

    thread.start()

    remaining = deadline - time.monotonic()

    if remaining <= 0 or not finished.wait(
        timeout=remaining
    ):
        raise ProcessIOTimeout(
            "Process input exceeded its deadline."
        )

    if errors:
        raise ProcessIOError(
            f"Could not send process input: {errors[0]}"
        ) from errors[0]


class ProcessOutputLimitExceeded(ProcessIOError):
    pass


def read_limited(
    stream,
    limit: int,
    overflow: threading.Event,
    destination: bytearray,
) -> None:
    """
    Read subprocess output without storing more than
    `limit` bytes.

    The caller is responsible for terminating the
    process if overflow is detected.
    """
    try:
        while True:
            chunk = stream.read(4096)

            if not chunk:
                break

            remaining = limit - len(destination)

            if len(chunk) > remaining:
                destination.extend(chunk[:remaining])
                overflow.set()
                break

            destination.extend(chunk)
    finally:
        stream.close()