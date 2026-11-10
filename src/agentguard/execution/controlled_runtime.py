"""Controlled Docker session. Builds copy source data; never execute agent build scripts."""
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import threading
import time

from agentguard.execution.docker_runtime import DockerRuntime
from agentguard.execution.errors import BuildTimeout, ExecutionCancelled, ExecutionTimeout
from agentguard.execution.ipc_dispatcher import IPCDispatcher
from agentguard.execution.lifecycle_protocol import StartRequest
from agentguard.execution.ipc_protocol import encode_ipc_message
from agentguard.execution.process_io import read_limited
from agentguard.execution.process_launcher import launch_with_deadline, ProcessLaunchTimeout
from agentguard.execution.process_ipc import ProcessIPC, ProcessIPCTimeout


def check_deadline(deadline, cancel):
    if cancel is not None and cancel.is_set():
        raise ExecutionCancelled("Execution cancelled.")
    if time.monotonic() >= deadline:
        raise ExecutionTimeout("Execution deadline exceeded.")


def command(args, deadline, cancel=None):
    """Bound both Docker CLI streams, including build logs."""
    check_deadline(deadline, cancel)
    process = launch_with_deadline(args, deadline)
    buffers = [bytearray(), bytearray()]
    overflows = [threading.Event(), threading.Event()]
    threads = []
    try:
        process.stdin.close()
        for stream, buffer, overflow in zip((process.stdout, process.stderr), buffers, overflows):
            thread = threading.Thread(target=read_limited, args=(stream, 1024*1024, overflow, buffer), daemon=True)
            thread.start(); threads.append(thread)
        while process.poll() is None:
            check_deadline(deadline, cancel)
            if any(event.is_set() for event in overflows): raise RuntimeError("Docker command output limit exceeded")
            time.sleep(0.025)
        for thread in threads: thread.join(timeout=max(0, deadline-time.monotonic()))
        check_deadline(deadline, cancel)
        if any(thread.is_alive() for thread in threads) or any(event.is_set() for event in overflows):
            raise RuntimeError("Docker output could not be completely bounded/drained")
        if process.returncode:
            raise RuntimeError("Docker command failed: " + buffers[1].decode(errors="replace")[-1000:])
        return bytes(buffers[0]).decode().strip()
    finally:
        if process.poll() is None:
            process.kill(); process.wait(timeout=2)
        for thread in threads: thread.join(timeout=1)
        for stream in (process.stdin, process.stdout, process.stderr): stream.close()


class ControlledDockerRuntime(DockerRuntime):
    def __init__(self):
        self.cleanup_status = "not_required"
        self.output = None

    def build(self, spec, manifest, deadline, cancel):
        from agentguard.integrations.agents import ContainerLaunchSpec
        if isinstance(spec, ContainerLaunchSpec):
            info=json.loads(command(["docker","image","inspect",spec.image],deadline,cancel))[0]
            image=info['Id']
            if not re.fullmatch(r"sha256:[0-9a-f]{64}",image): raise ValueError("Invalid pinned container image")
            self.IMAGE=image
            manifest.image_id=image
            manifest.base_digest=image
            manifest.source_digest=image.split(':')[1]
            manifest.worker_digest=image.split(':')[1]
            manifest.artifact_kind='container_image'
            return
        sources, manifest.source_digest = spec.sources()
        directory = Path(__file__).parent
        worker = (directory/"controlled_worker.py").read_bytes()
        client = (directory/"worker_client.py").read_bytes()
        manifest.worker_digest = hashlib.sha256(worker + client).hexdigest()
        try:
            info = json.loads(command(["docker", "image", "inspect", "python:3.11-slim"], deadline, cancel))[0]
            digests = info.get("RepoDigests", [])
            base = next((item for item in digests if re.fullmatch(r"(?:docker.io/library/)?python@sha256:[0-9a-f]{64}", item)), None)
            if base is None: raise ValueError("Base image must have a registry digest; pull python:3.11-slim first")
            manifest.base_digest = base
            with tempfile.TemporaryDirectory(prefix="agentguard-build-") as temporary:
                context = Path(temporary)
                (context/"agent").mkdir()
                for name, data in sources.items():
                    destination = context/"agent"/name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                (context/"worker.py").write_bytes(worker)
                (context/"worker_client.py").write_bytes(client)
                dockerfile = (
                    f"FROM {base}\n"
                    "COPY worker.py worker_client.py /opt/guard/\n"
                    "COPY agent /opt/agent/\n"
                    "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1\n"
                    "USER 65534:65534\n"
                    'ENTRYPOINT ["python", "-I", "/opt/guard/worker.py"]\n'
                )
                (context/"Dockerfile").write_text(dockerfile, encoding="utf-8")
                tag = "agentguard-agent:" + hashlib.sha256((manifest.source_digest+manifest.worker_digest+base).encode()).hexdigest()
                command(["docker", "build", "--network=none", "--pull=false", "--quiet", "--tag", tag, str(context)], deadline, cancel)
                image = json.loads(command(["docker", "image", "inspect", tag], deadline, cancel))[0]["Id"]
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", image): raise ValueError("Invalid built image id")
                manifest.image_id = image
                self.IMAGE = image
        except ExecutionTimeout as exc:
            self.cleanup_status = "unverified"  # daemon-side build cancellation cannot be attested
            raise BuildTimeout(str(exc)) from exc
        except (ProcessLaunchTimeout, ExecutionCancelled):
            self.cleanup_status = "unverified"
            raise

    def execute(self, spec, task, broker, policy, manifest, cancel=None, *, execution_deadline=None):
        now=time.monotonic()
        deadline=now+policy.timeout_seconds
        if execution_deadline is not None:
            if type(execution_deadline) not in {int,float} or not math.isfinite(execution_deadline) or execution_deadline<=now:
                raise ExecutionTimeout('No execution time budget remains')
            deadline=min(deadline,execution_deadline)
        name = "agentguard-run-" + manifest.run_id
        process = None
        ipc = None
        create_attempted = False
        unsettled = False
        failure = None
        try:
            self.build(spec, manifest, deadline, cancel)
            check_deadline(deadline, cancel)
            create_attempted = True
            identifier = command(self._docker_create_command(name, policy), deadline, cancel)
            if not re.fullmatch(r"[0-9a-f]{64}", identifier): raise ValueError("Invalid container id")
            manifest.container_id = identifier
            process = launch_with_deadline(["docker", "start", "--attach", "--interactive", name], deadline)
            ipc = ProcessIPC(process, IPCDispatcher(broker), timeout_seconds=max(0.001, deadline-time.monotonic()),
                max_messages=policy.max_tool_calls+3, max_stderr_bytes=policy.max_stderr_bytes,
                run_manifest=manifest, cancel_event=cancel, max_stdout_bytes=policy.max_stdout_bytes)
            ipc._write_with_deadline(encode_ipc_message(StartRequest(run_id=manifest.run_id, reference=spec.reference, task=task)))
            ipc.serve()
            self.output = ipc.completion.output
            if broker.runtime_error is not None: raise broker.runtime_error
            if broker.policy_violated: raise PermissionError("One or more tool policy violations occurred")
            if ipc.completion.status != "ok":
                raise RuntimeError(f"Worker {ipc.completion.error_type}: {ipc.completion.error_message}")
        except ProcessIPCTimeout:
            failure = ExecutionTimeout("Agent execution deadline exceeded.")
        except ProcessLaunchTimeout as exc:
            unsettled = not exc.handle.wait_until_finished(timeout=1)
            failure = ExecutionTimeout("Docker process launch deadline exceeded.")
        except Exception as exc:
            failure = exc
        finally:
            if process is not None:
                try:
                    if process.poll() is None: process.kill()
                    process.wait(timeout=2)
                except Exception:
                    unsettled = True
            if create_attempted:
                try:
                    self._cleanup_container(name)
                    self.cleanup_status = "unverified" if unsettled or manifest.container_id is None else "verified"
                except Exception as exc:
                    self.cleanup_status = "unverified"
                    failure = failure or exc
            if ipc is not None: ipc.close()
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    try: stream.close()
                    except OSError: pass
        if failure is not None: raise failure
        if self.cleanup_status != "verified": raise RuntimeError("Container cleanup could not be verified")
