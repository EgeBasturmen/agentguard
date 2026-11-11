"""Black-box Python CLI tests: expected answers stay in the host evaluator.

Only observed UTF-8 workspace files enter a separate non-root, networkless Docker
container. Agent-created pytest/unittest reports are never accepted as evidence.
"""
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
import uuid
from agentguard.core.manifest import digest
from agentguard.environments.adapters import safe_relative
from agentguard.execution.controlled_runtime import command
from agentguard.execution.docker_runtime import DockerRuntime
from agentguard.security.policy import SecurityPolicy
from agentguard.evaluation.registry import MissingEvidence

def run_tests(config,files,cancel_event=None,run_deadline=None):
    if not isinstance(files,dict) or not files or len(files)>128 or sum(len(v.encode()) for v in files.values() if isinstance(v,str))>1024*1024:raise MissingEvidence('Bounded independent filesystem fixture is required')
    prefixes={}
    for name,content in files.items():
        safe_relative(name)
        if not isinstance(content,str):raise MissingEvidence('Coding input must contain regular UTF-8 files')
        parts=name.split('/')
        for count in range(1,len(parts)+1):
            prefix='/'.join(parts[:count]);previous=prefixes.setdefault(prefix.casefold(),prefix)
            if previous!=prefix:raise MissingEvidence('Case-aliased coding paths cannot identify an immutable portable snapshot')
    entry=config['entrypoint'];safe_relative(entry)
    if entry not in files or not entry.endswith('.py'):raise MissingEvidence('Coding entrypoint is absent or unsupported')
    deadline=time.monotonic()+config['timeout_seconds'];runtime=DockerRuntime()
    if run_deadline is not None:deadline=min(deadline,run_deadline)
    base_info=json.loads(command(['docker','image','inspect','python:3.11-slim'],deadline,cancel_event))[0]
    base=next((d for d in base_info.get('RepoDigests',[]) if re.fullmatch(r'(?:docker.io/library/)?python@sha256:[0-9a-f]{64}',d)),None)
    if base is None:raise MissingEvidence('Pinned evaluator base image is absent')
    with tempfile.TemporaryDirectory(prefix='agentguard-code-eval-') as directory:
        context=Path(directory);(context/'code').mkdir()
        for name,content in files.items():
            path=context/'code'/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding='utf-8',newline='')
        (context/'Dockerfile').write_text('FROM '+base+'\nCOPY code /opt/code\nENV PYTHONDONTWRITEBYTECODE=1\nUSER 65534:65534\nENTRYPOINT '+json.dumps(['python','-I','/opt/code/'+entry])+'\n',encoding='utf-8')
        image=command(['docker','build','--network=none','--pull=false','--quiet',str(context)],deadline,cancel_event).splitlines()[-1]
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',image):raise MissingEvidence('Invalid coding test image identity')
    runtime.IMAGE=image;observations=[];passed=True
    for case in config['cases']:
        name='agentguard-code-'+uuid.uuid4().hex
        try:
            identifier=command(runtime._docker_create_command(name,SecurityPolicy())+case['arguments'],deadline,cancel_event)
            if not re.fullmatch(r'[0-9a-f]{64}',identifier):raise MissingEvidence('Invalid coding container identity')
            try:output=command(['docker','start','--attach',name],deadline,cancel_event)
            except RuntimeError:output=None
            info=json.loads(command(['docker','inspect',name],deadline,cancel_event))[0]
            if info['State'].get('Running') or type(info['State'].get('ExitCode')) is not int or info['State'].get('OOMKilled'):raise MissingEvidence('Coding process exit was not verified')
            okay=info['State']['ExitCode']==0 and output is not None and output==case['expected_stdout'].strip()
            passed=passed and okay
            observations.append(dict(container_id=identifier,exit_code=info['State']['ExitCode'],stdout_digest=hashlib.sha256((output or '').encode()).hexdigest(),passed=okay))
        finally:
            try:runtime._cleanup_container(name)
            except Exception:raise MissingEvidence('Coding evaluator cleanup is unverified') from None
    return passed,dict(image=image,base_digest=base,workspace_digest=digest(files),cases=observations,cleanup_status='verified',assurance='isolated Python CLI argv/stdout checks; stripped surrounding whitespace')
