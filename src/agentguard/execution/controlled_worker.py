"""Worker entrypoint; imported agent code executes only in the container."""
import asyncio
import importlib
import inspect
import json
import sys
sys.path.insert(0, "/opt/guard")
from worker_client import ToolClient

def main():
    source, sink = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr
    start = json.loads(source.readline(16386))
    if set(start)-{'turns'} != {"protocol_version", "type", "run_id", "reference", "task"} or start["protocol_version"] != 2 or start["type"] != "start":
        raise ValueError("Invalid start request")
    def send(**data):
        data.update(protocol_version=2, run_id=start["run_id"])
        payload = json.dumps(data, allow_nan=False).encode()
        if len(payload) > 16384: raise ValueError("Worker output too large")
        sink.write(payload + b"\n"); sink.flush()
    send(type="started")
    try:
        sys.path.insert(0, "/opt/agent")
        module, name = start["reference"].split(":")
        agent = getattr(importlib.import_module(module), name)()
        turns=start.get('turns')
        if turns is not None and (not isinstance(turns,list) or not 1<=len(turns)<=20 or any(not isinstance(task,str) or not task or len(task)>2048 for task in turns)):
            raise ValueError('Invalid session turns')
        sandbox=ToolClient(source,sink)
        # Invoke sync code outside a running loop (it may legitimately use asyncio.run).
        # Reuse one loop for async turns and cancel outstanding tasks at session end.
        with asyncio.Runner() as runner:
            output=None
            for index,task in enumerate(turns or [start['task']]):
                asyncio.set_event_loop(runner.get_loop())
                output=agent.run(task=task,sandbox=sandbox)
                if inspect.isawaitable(output):
                    async def finish(value):return await value
                    output=runner.run(finish(output))
                if output is not None and not isinstance(output,str):raise TypeError('Agent output must be a string or None')
                if output is not None and len(output.encode())>8192:raise ValueError('Agent output exceeds 8192 bytes')
                if turns is not None:send(type='turn_completed',turn=index+1,output=output)
        send(type="completed", status="ok", output=output, error_type=None, error_message=None)
    except BaseException as exc:
        send(type="completed", status="error", output=None, error_type=type(exc).__name__[:200], error_message=str(exc)[:1000])

if __name__ == "__main__": main()
