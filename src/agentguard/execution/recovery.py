"""Explicit local-daemon cleanup, fenced by exact container/run/host/dead-owner identity."""
import json
import os
import re
import socket
import time
from agentguard.execution.controlled_runtime import command
WINDOWS=os.name=='nt'

def process_alive(pid):
    if WINDOWS:
        from agentguard.execution.owner_watch import OwnerWatch
        watch=OwnerWatch(pid)
        try:
            if not watch.handle and watch.error!=87:return True
            return watch.alive()
        finally:watch.close()
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True

def reap_container(container_id,run_id):
    if not re.fullmatch('[0-9a-f]{64}',container_id) or not re.fullmatch('[0-9a-f]{32}',run_id):raise ValueError('Exact resource identities required')
    context=os.environ.get('DOCKER_CONTEXT')
    endpoint=None if context else os.environ.get('DOCKER_HOST')
    if endpoint and not endpoint.startswith(('unix://','npipe://')):raise ValueError('Recovery supports only an explicit local Docker daemon')
    if not endpoint:endpoint=json.loads(command(['docker','context','inspect',*([context] if context else []),'--format','{{json .Endpoints.docker.Host}}'],time.monotonic()+10))
    if not isinstance(endpoint,str) or not endpoint.startswith(('unix://','npipe://')):raise ValueError('Recovery supports only an explicit local Docker daemon')
    info=json.loads(command(['docker','inspect',container_id],time.monotonic()+15))[0]
    labels=info['Config'].get('Labels') or {};owner=labels.get('io.agentguard.owner_pid','')
    if info['Id']!=container_id or info['Name']!='/agentguard-run-'+run_id or labels.get('io.agentguard.run_id')!=run_id or labels.get('io.agentguard.host')!=socket.gethostname() or not owner.isdigit() or int(owner)<=0:raise ValueError('Resource ownership is unverified')
    if process_alive(int(owner)):raise ValueError('Owning process is still alive; cleanup refused')
    if info['HostConfig']['NetworkMode']!='none' or info.get('Mounts') or info['Config']['User']!='65534:65534':raise ValueError('Unexpected isolation profile')
    command(['docker','rm','--force',container_id],time.monotonic()+15)
    remaining=command(['docker','ps','-a','--no-trunc','--filter','id='+container_id,'--format','{{.ID}}'],time.monotonic()+15)
    if remaining:raise RuntimeError('Resource removal is unverified')
    return dict(container_id=container_id,run_id=run_id,resource_cleanup='verified',gate_decision='BLOCK',authorizes_release=False,usage_and_external_effects_still_require_review=True)
