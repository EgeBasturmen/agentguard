"""Explicit private pilot setup. Credentials are written exclusively, never printed or auto-discovered."""
from pathlib import Path
import getpass
import json
import os
import re
import ssl
import time
import typer
from agentguard.management.service import ControlPlane,AccessError
from agentguard.management.client import Client

app=typer.Typer(help='Private control plane and customer-side runner. No managed/shared-hostile execution.')

def attempt(fn):
    try:return fn()
    except Exception as error:
        code=error.code if isinstance(error,AccessError) else 'MANAGEMENT_OPERATION_FAILED'
        typer.echo(json.dumps(dict(error=code)),err=True);raise typer.Exit(2) from None

def emit(value):typer.echo(json.dumps(value,ensure_ascii=False,indent=2))

def private_file(path,operation):
    path=Path(path).absolute()
    if path.is_symlink() or path.exists():raise FileExistsError('Credential output exists')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
        result=operation();stream.write(result['token'] if 'token' in result else result['runner_token'])
    return {key:value for key,value in result.items() if key not in {'token','runner_token'}}

def credential(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_nlink!=1 or path.stat().st_size>512:raise ValueError('Unsafe credential file')
    if os.name!='nt' and path.stat().st_mode&0o077:raise ValueError('Credential file must be owner-only')
    value=path.read_text(encoding='utf-8').strip()
    if not re.fullmatch(r'ag7r?_[A-Za-z0-9_-]{43}',value):raise ValueError('Explicit AgentGuard credential required; provider keys are never accepted')
    return value

@app.command('init')
def initialize(state:Path=typer.Option(...,'--state'),owner:str=typer.Option(...,'--owner'),credential_file:Path=typer.Option(...,'--credential-file')):
    result=attempt(lambda:private_file(credential_file,lambda:ControlPlane(state).bootstrap(owner)))
    emit(dict(initialized=True,credential_written=True,**result))

@app.command('serve')
def serve(state:Path=typer.Option(...,'--state'),host:str='127.0.0.1',port:int=8765,cert:Path|None=None,private_key:Path|None=None,public_origin:str|None=None):
    from agentguard.management.http import make_server
    def start():
        service=ControlPlane(state)
        with service.db() as db:
            if db.execute('SELECT 1 FROM workspaces').fetchone() is None:raise AccessError('INITIALIZE_WITH_OFFLINE_OPERATOR_CLI',409)
        context=None
        if cert is not None or private_key is not None:
            if cert is None or private_key is None:raise ValueError('Both TLS files required')
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2;context.load_cert_chain(cert,private_key)
        server=make_server(service,host,port,tls_context=context,public_origin=public_origin)
        emit(dict(listening=True,host=host,port=server.server_port,external_pilot_verified=False))
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()
    attempt(start)

@app.command('enroll')
def enroll(server:str=typer.Option(...,'--server'),credential_file:Path=typer.Option(...,'--credential-file')):
    def exchange():
        value=getpass.getpass('One-time AgentGuard enrollment (hidden): ')
        if not re.fullmatch(r'ag7e_[A-Za-z0-9_-]{43}',value):raise ValueError('Explicit enrollment required')
        return Client(server).request('/api/runner/exchange',dict(enrollment_token=value))
    result=attempt(lambda:private_file(credential_file,exchange));emit(dict(enrolled=True,credential_written=True,**result))

@app.command('runner')
def runner(server:str=typer.Option(...,'--server'),credential_file:Path=typer.Option(...,'--credential-file'),
    workspace:Path=typer.Option(...,'--workspace'),cache:Path=typer.Option(...,'--cache'),once:bool=False,allow_external:bool=False,allow_live_models:bool=False):
    from agentguard.management.runner import PrivateRunner
    def execute():
        worker=PrivateRunner(Client(server,credential(credential_file)),source_root=workspace,cache_root=cache,allow_external=allow_external,allow_live_models=allow_live_models)
        while True:
            result=worker.run_once()
            if result is not None:emit(dict(id=result['id'],state=result['state'],gate_decision=result['gate_decision'],cleanup_status=result['cleanup_status']))
            if once:return
            if result is None:time.sleep(2)
    try:attempt(execute)
    except KeyboardInterrupt:emit(dict(stopped=True,remote_in_flight_cleanup_requires_verification=True))

@app.command('source-hash')
def source_hash(workspace:Path=typer.Option(...,'--workspace'),reference:str=typer.Option(...,'--reference'),include:list[str]=typer.Option([],'--include')):
    from agentguard.execution.agent_spec import AgentLaunchSpec
    def calculate():
        files,value=AgentLaunchSpec(source_dir=workspace,reference=reference,include_files=include).sources()
        return dict(artifact_digest=value,files=sorted(files),agent_imported_on_host=False)
    emit(attempt(calculate))

@app.command('recover-owner')
def recover_owner(state:Path=typer.Option(...,'--state'),workspace:str=typer.Option(...,'--workspace'),user:str=typer.Option(...,'--user'),
    reason:str=typer.Option(...,'--reason'),credential_file:Path=typer.Option(...,'--credential-file')):
    emit(attempt(lambda:private_file(credential_file,lambda:ControlPlane(state).operator_recover_owner(workspace,user,operator=getpass.getuser(),reason=reason))))

@app.command('activate-plan')
def activate_plan(request_id:str,state:Path=typer.Option(...,'--state'),receipt_reference:str=typer.Option(...,'--receipt-reference')):
    emit(attempt(lambda:ControlPlane(state).operator_activate_plan(request_id,operator=getpass.getuser(),receipt_reference=receipt_reference)))

@app.command('cancel-plan')
def cancel_plan(request_id:str,state:Path=typer.Option(...,'--state'),reason:str=typer.Option(...,'--reason')):
    emit(attempt(lambda:ControlPlane(state).operator_cancel_plan(request_id,operator=getpass.getuser(),reason=reason)))

@app.command('model-budget')
def model_budget(state:Path=typer.Option(...,'--state'),project:str=typer.Option(...,'--project'),max_tokens:int=typer.Option(...,'--max-tokens'),
    max_cost_micro_usd:int=typer.Option(...,'--max-cost-micro-usd'),reason:str=typer.Option(...,'--reason'),
    input_rate:int=1000000,output_rate:int=10000000,pricing_version:str='operator-conservative-v1'):
    emit(attempt(lambda:ControlPlane(state).operator_model_budget(project,max_tokens=max_tokens,max_cost_micro_usd=max_cost_micro_usd,
        operator=getpass.getuser(),reason=reason,input_rate=input_rate,output_rate=output_rate,pricing_version=pricing_version)))
