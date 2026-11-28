"""Explicit private pilot setup. Credentials are written exclusively, never printed or auto-discovered."""
from pathlib import Path
import getpass
import json
import re
import ssl
import time
import sys
import warnings
import typer
try:
    from typer._click.exceptions import UsageError
except ImportError:  # Typer versions using the standalone Click dependency.
    from click.exceptions import UsageError
from typer.core import TyperCommand
from agentguard.management.service import ControlPlane,AccessError
from agentguard.management.client import Client
from agentguard.management.credentials import private_file,credential,CredentialError
from agentguard.management.delivery import DeliveryError

app=typer.Typer(help='Private control plane and customer-side runner. No managed/shared-hostile execution.')


class EnrollmentCommand(TyperCommand):
    def parse_args(self,ctx,args):
        try:return super().parse_args(ctx,args)
        except UsageError:
            # Click normally echoes unexpected positional values, which could be
            # a mistakenly pasted enrollment secret. Do not echo parser inputs.
            raise UsageError('Invalid enrollment arguments; use hidden input or --enrollment-stdin.',ctx) from None

def attempt(fn):
    try:return fn()
    except DeliveryError as error:
        typer.echo(json.dumps(dict(error=error.code,delivery_state=error.delivery_state,attempts=error.attempts,category=error.category,recovery_required=True)),err=True)
        raise typer.Exit(2) from None
    except CredentialError as error:
        typer.echo(json.dumps(dict(error=error.code,operation_status=error.operation_status,
            publication_status=error.publication_status,credential_written=False,recovery_required=error.recovery_required)),err=True)
        raise typer.Exit(2) from None
    except Exception as error:
        code=error.code if isinstance(error,AccessError) else 'MANAGEMENT_OPERATION_FAILED'
        typer.echo(json.dumps(dict(error=code)),err=True);raise typer.Exit(2) from None

def emit(value):typer.echo(json.dumps(value,ensure_ascii=False,indent=2))

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

@app.command('enroll',cls=EnrollmentCommand)
def enroll(server:str=typer.Option(...,'--server'),credential_file:Path=typer.Option(...,'--credential-file'),
    enrollment_stdin:bool=typer.Option(False,'--enrollment-stdin',help='Read one bounded enrollment token from a pipe; never a CLI argument.')):
    def execute():
        try:
            if enrollment_stdin:
                if sys.stdin.isatty():raise CredentialError('ENROLLMENT_STDIN_REQUIRES_PIPE')
                value=sys.stdin.read(128).removesuffix('\n').removesuffix('\r')
            else:
                with warnings.catch_warnings():
                    warnings.simplefilter('error',getpass.GetPassWarning)
                    value=getpass.getpass('One-time AgentGuard enrollment (hidden): ')
        except (KeyboardInterrupt,EOFError):raise CredentialError('ENROLLMENT_INPUT_CANCELLED') from None
        except getpass.GetPassWarning:raise CredentialError('ENROLLMENT_HIDDEN_INPUT_UNAVAILABLE') from None
        if not re.fullmatch(r'ag7e_[A-Za-z0-9_-]{43}',value):raise CredentialError('ENROLLMENT_INPUT_INVALID')
        client=Client(server)
        def exchange():
            try:return client.request('/api/runner/exchange',dict(enrollment_token=value))
            except AccessError as error:
                if 400<=error.status<500:raise CredentialError(error.code,'rejected') from None
                raise CredentialError('ENROLLMENT_EXCHANGE_UNCERTAIN','unknown') from None
            except Exception:raise CredentialError('ENROLLMENT_EXCHANGE_UNCERTAIN','unknown') from None
        return private_file(credential_file,exchange)
    result=attempt(execute);emit(dict(enrolled=True,credential_written=True,**result))

@app.command('runner')
def runner(server:str=typer.Option(...,'--server'),credential_file:Path=typer.Option(...,'--credential-file'),
    workspace:Path=typer.Option(...,'--workspace'),cache:Path=typer.Option(...,'--cache'),once:bool=False,allow_external:bool=False,allow_live_models:bool=False,recover_pending:bool=False):
    from agentguard.management.runner import PrivateRunner
    def execute():
        from agentguard.management.delivery import RecoveryPolicy
        worker=PrivateRunner(Client(server,credential(credential_file)),source_root=workspace,cache_root=cache,allow_external=allow_external,allow_live_models=allow_live_models,recovery_policy=RecoveryPolicy() if recover_pending else None)
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

@app.command('backup')
def backup_state(state:Path=typer.Option(...,'--state'),output:Path=typer.Option(...,'--output')):
    from agentguard.management.operations import backup
    from agentguard.management.store import Store
    def execute():
        if not (state/'control.sqlite').is_file():raise ValueError('Existing state required')
        return backup(Store(state,migrate_schema=False),output)
    emit(attempt(execute))

@app.command('restore')
def restore_state(archive:Path,target:Path=typer.Option(...,'--target'),signing_key:Path=typer.Option(...,'--signing-key'),deletions:Path=typer.Option(...,'--deletions')):
    from agentguard.management.operations import restore
    emit(attempt(lambda:restore(archive,target,signing_key=signing_key,deletions=deletions)))

@app.command('migrate')
def migrate_state(state:Path=typer.Option(...,'--state')):
    def execute():
        service=ControlPlane(state)
        with service.db() as db:return dict(schema_version=db.execute('SELECT version FROM control_meta').fetchone()[0],transactional=True)
    emit(attempt(execute))

@app.command('reconcile-billing')
def reconcile_billing(state:Path=typer.Option(...,'--state'),project:str=typer.Option(...,'--project'),reference:str=typer.Option(...,'--reference'),paid:bool=typer.Option(...,'--paid/--unpaid')):
    emit(attempt(lambda:ControlPlane(state).reconcile_billing(project,operator=getpass.getuser(),reference=reference,paid=paid)))

@app.command('recover-job')
def recover_job(state:Path=typer.Option(...,'--state'),job:str=typer.Option(...,'--job'),report:Path=typer.Option(...,'--report'),reason:str=typer.Option(...,'--reason'),project:str=typer.Option(...,'--project'),credential_file:Path=typer.Option(...,'--credential-file')):
    def execute():
        from agentguard.integrations.http import _json
        from agentguard.management.operator_recovery import read_private_evidence
        evidence=read_private_evidence(report)
        return ControlPlane(state).recover_job(job,evidence,operator=getpass.getuser(),reason=reason,token=credential(credential_file),project=project)
    emit(attempt(execute))

@app.command('reap-container')
def reap_container(container_id:str=typer.Option(...,'--container-id'),run_id:str=typer.Option(...,'--run-id')):
    from agentguard.execution.recovery import reap_container as reap
    emit(attempt(lambda:reap(container_id,run_id)))

@app.command('create-account')
def create_account(state:Path=typer.Option(...,'--state'),owner_credential:Path=typer.Option(...,'--owner-credential'),user:str=typer.Option(...,'--user'),name:str=typer.Option(...,'--name'),credential_file:Path=typer.Option(...,'--credential-file')):
    emit(attempt(lambda:private_file(credential_file,lambda:ControlPlane(state).create_workspace(credential(owner_credential),user,name))))

@app.command('delete-account')
def delete_account(state:Path=typer.Option(...,'--state'),credential_file:Path=typer.Option(...,'--credential-file'),confirmation:str=typer.Option(...,'--confirmation')):
    emit(attempt(lambda:ControlPlane(state).delete_workspace(credential(credential_file),confirmation=confirmation)))
