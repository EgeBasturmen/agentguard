import json
import subprocess
import threading
import time
import uuid
from typing import Any

from pydantic import ValidationError
import re
from agentguard.execution.protocol import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    WorkerRequest,
    WorkerResponse,
)
from agentguard.security.policy import SecurityPolicy
from agentguard.execution.process_io import (
    ProcessIOError,
    read_limited,
    write_with_deadline,
)
from agentguard.execution.process_launcher import (
    ProcessLaunchError,
    ProcessLaunchTimeout,
    launch_with_deadline,
)
from agentguard.execution.container_lifecycle import (
    ContainerLifecycle,
    ContainerState,
)

class ContainerRuntimeError(RuntimeError):
    pass


class DockerRuntime:
    IMAGE = "agentguard-worker:dev"

    STDERR_LIMIT_BYTES = 16 * 1024
    CLEANUP_TIMEOUT_SECONDS = 15
    OUTPUT_GRACE_SECONDS = 2

    def _docker_create_command(
        self,
        container_name: str,
        policy: SecurityPolicy,
    ) -> list[str]:
        return [
            "docker",
            "create",
            "--name",
            container_name,
            "-i",
            "--restart",
            "no",
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
    def _create_container(
        self,
        container_name: str,
        policy: SecurityPolicy,
        deadline: float,
    ) -> str:
        process = launch_with_deadline(
            command=self._docker_create_command(
                container_name,
                policy,
            ),
            deadline=deadline,
        )

        stdout_data = bytearray()
        stderr_data = bytearray()
        stdout_overflow = threading.Event()
        stderr_overflow = threading.Event()

        if process.stdout is None or process.stderr is None:
            raise ContainerRuntimeError(
                "Docker create output streams are unavailable."
            )

        stdout_thread = threading.Thread(
            target=read_limited,
            args=(
                process.stdout,
                MAX_RESPONSE_BYTES,
                stdout_overflow,
                stdout_data,
            ),
            daemon=True,
        )

        stderr_thread = threading.Thread(
            target=read_limited,
            args=(
                process.stderr,
                self.STDERR_LIMIT_BYTES,
                stderr_overflow,
                stderr_data,
            ),
            daemon=True,
        )

        stdout_thread.start()
        stderr_thread.start()

        try:
            while process.poll() is None:
                if stdout_overflow.is_set() or stderr_overflow.is_set():
                    raise ContainerRuntimeError(
                        "Docker create output exceeds size limit."
                    )

                if time.monotonic() >= deadline:
                    raise ContainerRuntimeError(
                        "Docker container creation timed out."
                    )

                time.sleep(0.05)

            stdout_thread.join(timeout=self.OUTPUT_GRACE_SECONDS)
            stderr_thread.join(timeout=self.OUTPUT_GRACE_SECONDS)

            if stdout_thread.is_alive() or stderr_thread.is_alive():
                raise ContainerRuntimeError(
                    "Docker create output streams did not finish."
                )

            if stdout_overflow.is_set() or stderr_overflow.is_set():
                raise ContainerRuntimeError(
                    "Docker create output exceeds size limit."
                )

            if process.returncode != 0:
                error_text = stderr_data.decode(
                    "utf-8",
                    errors="replace",
                )
                raise ContainerRuntimeError(
                    f"Docker container creation failed: {error_text}"
                )

            created_id = stdout_data.decode(
                "utf-8",
                errors="replace",
            ).strip()

            if not created_id:
                raise ContainerRuntimeError(
                    "Docker create returned no container ID."
                )

            if re.fullmatch(r"[0-9a-f]{64}", created_id) is None:
                raise ContainerRuntimeError(
                    "Docker create returned an invalid container ID."
                )
            return created_id

        finally:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired as exc:
                    raise ContainerRuntimeError(
                        "Docker create CLI process did not terminate."
                    ) from exc
    def _inspect_container_state(
        self,
        container_ref: str,
    ) -> str | None:
        """
        Return the Docker container state.

        None means Docker confirms that the container
        does not exist.
        """

        try:
            inspection = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{.State.Status}}",
                    container_ref,
                ],
                capture_output=True,
                text=True,
                timeout=self.CLEANUP_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContainerRuntimeError(
                "Could not inspect container state."
            ) from exc

        if inspection.returncode == 0:
            state = inspection.stdout.strip()

            if not state:
                raise ContainerRuntimeError(
                    "Docker inspect returned an empty "
                    "container state."
                )

            return state

        error_text = inspection.stderr.lower()

        if (
            "no such object" in error_text
            or "no such container" in error_text
        ):
            return None

        raise ContainerRuntimeError(
            "Container state inspection failed: "
            f"{inspection.stderr.strip()}"
        )
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

        lifecycle = ContainerLifecycle(
            name=container_name
        )
        container_id: str | None = None

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
                        destination.extend(
                            chunk[:remaining]
                        )
                        overflow.set()
                        break

                    destination.extend(chunk)

            finally:
                stream.close()

        execution_error = None

        try:
            # -------------------------------------------------
            # 1. CREATE
            # Container oluşturulur.
            # Worker henüz çalışmaz.
            # -------------------------------------------------

            container_id= self._create_container(
                container_name=container_name,
                policy=policy,
                deadline=deadline,
            )

            lifecycle.transition(
                ContainerState.CREATED
            )

            if time.monotonic() >= deadline:
                raise ContainerRuntimeError(
                    "Container creation consumed the "
                    "execution deadline; container startup "
                    "was not attempted."
                )

            # -------------------------------------------------
            # 2. STARTING
            # Docker'a container'ı başlatma isteği
            # gönderilmek üzere.
            # -------------------------------------------------

            lifecycle.transition(
                ContainerState.STARTING
            )

            if container_id is None:
                raise ContainerRuntimeError(
                    "Container ID is unavailable after creation."
                )

            process = launch_with_deadline(
                command=[
                    "docker",
                    "start",
                    "-a",
                    "-i",
                    container_id,
                ],
                deadline=deadline,
            )

            # -------------------------------------------------
            # 3. OUTPUT READERS
            # stdout / stderr sınırlı olarak okunur.
            # -------------------------------------------------

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

            # -------------------------------------------------
            # 4. REQUEST
            # Worker'a request gönderilir.
            # -------------------------------------------------

            write_with_deadline(
                process=process,
                data=request_bytes,
                deadline=deadline,
            )

            # -------------------------------------------------
            # 5. PROCESS MONITORING
            # -------------------------------------------------

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

            # -------------------------------------------------
            # 6. OUTPUT THREADS
            # -------------------------------------------------

            stdout_thread.join(
                timeout=self.OUTPUT_GRACE_SECONDS
            )

            stderr_thread.join(
                timeout=self.OUTPUT_GRACE_SECONDS
            )

            if (
                stdout_thread.is_alive()
                or stderr_thread.is_alive()
            ):
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

            # -------------------------------------------------
            # 7. EXIT CODE
            # -------------------------------------------------

            if process.returncode != 0:
                error_text = stderr_data.decode(
                    "utf-8",
                    errors="replace",
                )

                raise ContainerRuntimeError(
                    "Container execution failed: "
                    f"{error_text}"
                )

            lifecycle.transition(
                ContainerState.EXECUTED
            )

        except ProcessLaunchTimeout as exc:
            launcher_finished = (
                exc.handle.wait_until_finished(
                    timeout=self.CLEANUP_TIMEOUT_SECONDS
                )
            )

            if not launcher_finished:
                execution_error = ContainerRuntimeError(
                    "Docker CLI launch timed out and its "
                    "launcher thread did not finish. "
                    "A delayed Docker operation may still "
                    "complete after cleanup."
                )

            else:
                inspect_target = (
                    container_id
                    if container_id is not None
                    else container_name
                )

                try:
                    docker_state = self._inspect_container_state(
                        inspect_target
                    )
                except ContainerRuntimeError as inspect_exc:
                    execution_error = ContainerRuntimeError(
                        "Docker CLI launch exceeded its deadline "
                        "and the resulting container state could "
                        "not be verified: "
                        f"{inspect_exc}"
                    )
                else:
                    if docker_state is None:
                        execution_error = ContainerRuntimeError(
                            "Docker CLI launch exceeded its deadline. "
                            "Docker reports that the container no "
                            "longer exists."
                        )

                    else:
                        execution_error = ContainerRuntimeError(
                            "Docker CLI launch exceeded its deadline. "
                            "Observed container state before cleanup: "
                            f"{docker_state}."
                        )

        except ProcessLaunchError as exc:
            execution_error = ContainerRuntimeError(
                f"Docker CLI launch failed: {exc}"
            )

        except (
            OSError,
            BrokenPipeError,
            ProcessIOError,
        ) as exc:
            execution_error = ContainerRuntimeError(
                "Container process communication failed: "
                f"{exc}"
            )

        except ContainerRuntimeError as exc:
            execution_error = exc

        finally:
            # -------------------------------------------------
            # 8. PROCESS TERMINATION
            # -------------------------------------------------

            stop_reading.set()

            if process is not None:
                try:
                    self._terminate_process(process)

                except ContainerRuntimeError as exc:
                    if execution_error is None:
                        execution_error = exc
                    else:
                        execution_error = ContainerRuntimeError(
                            f"{execution_error} "
                            f"Process termination also failed: "
                            f"{exc}"
                        )

            # -------------------------------------------------
            # 9. CONTAINER CLEANUP
            # -------------------------------------------------

            lifecycle.transition(
                ContainerState.CLEANING_UP
            )

            try:
                cleanup_target = (
                    container_id
                    if container_id is not None
                    else container_name
                )

                self._cleanup_container(
                    cleanup_target
                )

            except ContainerRuntimeError as exc:
                lifecycle.transition(
                    ContainerState.CLEANUP_UNVERIFIED
                )

                # Cleanup doğrulanamıyorsa başarılı sonuç
                # dönmek güvenli değildir.
                if execution_error is None:
                    execution_error = exc

                else:
                    execution_error = ContainerRuntimeError(
                        f"{execution_error} "
                        "Container cleanup could not be "
                        f"verified: {exc}"
                    )

            else:
                lifecycle.transition(
                    ContainerState.REMOVED
                )

        # -----------------------------------------------------
        # 10. FAIL CLOSED
        # -----------------------------------------------------

        if execution_error is not None:
            raise execution_error

        # -----------------------------------------------------
        # 11. RESPONSE VALIDATION
        # -----------------------------------------------------

        try:
            response = WorkerResponse.model_validate_json(
                bytes(stdout_data)
            )

        except ValidationError as exc:
            raise ContainerRuntimeError(
                f"Invalid container response: {exc}"
            ) from exc

        if (
            response.scenario_name
            != validated_request.scenario_name
        ):
            raise ContainerRuntimeError(
                "Container response scenario does not "
                "match request."
            )

        return response.model_dump()
