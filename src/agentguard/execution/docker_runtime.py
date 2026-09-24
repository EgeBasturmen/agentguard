import json
import subprocess
import threading
import time
import uuid
from typing import Any

from pydantic import ValidationError

from agentguard.execution.protocol import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    WorkerRequest,
    WorkerResponse,
)
from agentguard.security.policy import SecurityPolicy
from agentguard.execution.process_io import (
    ProcessIOError,
    write_with_deadline,
)
from agentguard.execution.process_launcher import (
    ProcessLaunchError,
    ProcessLaunchTimeout,
    launch_with_deadline,
)

class ContainerRuntimeError(RuntimeError):
    pass


class DockerRuntime:
    IMAGE = "agentguard-worker:dev"

    STDERR_LIMIT_BYTES = 16 * 1024
    CLEANUP_TIMEOUT_SECONDS = 15
    OUTPUT_GRACE_SECONDS = 2

    def _docker_command(
        self,
        container_name: str,
        policy: SecurityPolicy,
    ) -> list[str]:
        return [
            "docker",
            "run",
            "--name",
            container_name,
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "64",
            "--memory",
            f"{policy.memory_limit_mb}m",
            "--cpus",
            str(policy.cpu_limit),
            "--user",
            "65534:65534",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--workdir",
            "/tmp",
            self.IMAGE,
        ]

    def _cleanup_container(
        self,
        container_name: str,
    ) -> None:
        try:
            removal = subprocess.run(
                [
                    "docker",
                    "rm",
                    "--force",
                    container_name,
                ],
                capture_output=True,
                text=True,
                timeout=self.CLEANUP_TIMEOUT_SECONDS,
                check=False,
            )

            removal_error = removal.stderr.lower()

            if (
                removal.returncode != 0
                and "no such container" not in removal_error
                and "no such object" not in removal_error
            ):
                raise ContainerRuntimeError(
                    "Container removal failed: "
                    f"{removal.stderr.strip()}"
                )

            inspection = subprocess.run(
                [
                    "docker",
                    "inspect",
                    container_name,
                ],
                capture_output=True,
                text=True,
                timeout=self.CLEANUP_TIMEOUT_SECONDS,
                check=False,
            )


        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContainerRuntimeError(
                "Could not verify container cleanup."
            ) from exc

        if inspection.returncode == 0:
            raise ContainerRuntimeError(
                "Container still exists after cleanup."
            )

        inspection_error = inspection.stderr.lower()

        if (
            "no such object" not in inspection_error
            and "no such container" not in inspection_error
        ):
            raise ContainerRuntimeError(
                "Container cleanup verification failed: "
                f"{inspection.stderr.strip()}"
            )

    def _terminate_process(
        self,
        process: subprocess.Popen,
    ) -> None:
        if process.poll() is not None:
            return

        process.kill()

        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            raise ContainerRuntimeError(
                "Docker CLI process did not terminate."
            )

    def run_request(
        self,
        request: dict[str, Any],
        policy: SecurityPolicy,
    ) -> dict[str, Any]:
        if policy.execution_mode != "isolated":
            raise ContainerRuntimeError(
                "DockerRuntime requires isolated execution mode."
            )

        if (
            policy.allow_network
            or policy.allow_host_filesystem
            or policy.allow_host_environment
        ):
            raise ContainerRuntimeError(
                "This runtime does not support granting "
                "network, host filesystem, or host "
                "environment access."
            )



        try:
            validated_request = WorkerRequest.model_validate(
                request
            )


        except ValidationError as exc:
            raise ContainerRuntimeError(
                f"Invalid worker request: {exc}"
            ) from exc

        request_bytes = (
            validated_request.model_dump_json().encode("utf-8")
        )

        if len(request_bytes) > MAX_REQUEST_BYTES:
            raise ContainerRuntimeError(
                "Worker request exceeds size limit."
            )
        container_name = f"agentguard-{uuid.uuid4().hex}"
        deadline = time.monotonic() + policy.timeout_seconds

        

        process = None
        stdout_data = bytearray()
        stderr_data = bytearray()
        stdout_overflow = threading.Event()
        stderr_overflow = threading.Event()
        stop_reading = threading.Event()

        def read_limited(
            stream,
            destination: bytearray,
            limit: int,
            overflow: threading.Event,
        ) -> None:
            try:
                while not stop_reading.is_set():
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

        execution_error = None

        try:
            process = launch_with_deadline(
                command=self._docker_command(
                    container_name,
                    policy,
                ),
                deadline=deadline,
            )

            stdout_thread = threading.Thread(
                target=read_limited,
                args=(
                    process.stdout,
                    stdout_data,
                    MAX_RESPONSE_BYTES,
                    stdout_overflow,
                ),
                daemon=True,
            )

            stderr_thread = threading.Thread(
                target=read_limited,
                args=(
                    process.stderr,
                    stderr_data,
                    self.STDERR_LIMIT_BYTES,
                    stderr_overflow,
                ),
                daemon=True,
            )

            stdout_thread.start()
            stderr_thread.start()

            write_with_deadline(
                process=process,
                data=request_bytes,
                deadline=deadline,
            )

        

            while process.poll() is None:
                if stdout_overflow.is_set():
                    raise ContainerRuntimeError(
                        "Container stdout exceeds size limit."
                    )

                if stderr_overflow.is_set():
                    raise ContainerRuntimeError(
                        "Container stderr exceeds size limit."
                    )

                if time.monotonic() >= deadline:
                    raise ContainerRuntimeError(
                        "Container execution timed out."
                    )

                time.sleep(0.05)

            stdout_thread.join(
                timeout=self.OUTPUT_GRACE_SECONDS
            )
            stderr_thread.join(
                timeout=self.OUTPUT_GRACE_SECONDS
            )

            if stdout_thread.is_alive() or stderr_thread.is_alive():
                raise ContainerRuntimeError(
                    "Container output streams did not finish."
                )

            if stdout_overflow.is_set():
                raise ContainerRuntimeError(
                    "Container stdout exceeds size limit."
                )

            if stderr_overflow.is_set():
                raise ContainerRuntimeError(
                    "Container stderr exceeds size limit."
                )

            if process.returncode != 0:
                error_text = stderr_data.decode(
                    "utf-8",
                    errors="replace",
                )

                raise ContainerRuntimeError(
                    "Container execution failed: "
                    f"{error_text}"
                )

        except ProcessLaunchTimeout as exc:
            if not exc.handle.wait_until_finished(
                timeout=self.CLEANUP_TIMEOUT_SECONDS
            ):
                execution_error = ContainerRuntimeError(
                    "Docker CLI launch timed out and the "
                    "launcher thread did not finish. "
                    "Container cleanup cannot be guaranteed."
                )
            else:
                execution_error = ContainerRuntimeError(
                    "Docker CLI launch exceeded its deadline."
                )

        except ProcessLaunchError as exc:
            execution_error = ContainerRuntimeError(
                f"Docker CLI launch failed: {exc}"
            )

        except (OSError, BrokenPipeError,ProcessIOError) as exc:
            execution_error = ContainerRuntimeError(
                f"Container process communication failed: {exc}"
            )

        except ContainerRuntimeError as exc:
            execution_error = exc

        finally:
            stop_reading.set()

            if process is not None:
                try:
                    self._terminate_process(process)
                except ContainerRuntimeError as exc:
                    if execution_error is None:
                        execution_error = exc

            try:
                self._cleanup_container(container_name)
            except ContainerRuntimeError as exc:
                # Temizleme doğrulanamadıysa başarılı sonuç dönmeyiz.
                execution_error = exc

        if execution_error is not None:
            raise execution_error

        try:
            response = WorkerResponse.model_validate_json(
                bytes(stdout_data)
            )
        except ValidationError as exc:
            raise ContainerRuntimeError(
                f"Invalid container response: {exc}"
            ) from exc

        if response.scenario_name != validated_request.scenario_name:
            raise ContainerRuntimeError(
                "Container response scenario does not match request."
            )

        return response.model_dump()