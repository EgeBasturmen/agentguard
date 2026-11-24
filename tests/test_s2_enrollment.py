"""S2-01 uses isolated files and fake exchanges only; never live enrollment."""
import getpass
import io
import json
import os
import warnings

import pytest
from typer.testing import CliRunner
from agentguard.cli.main import app
from agentguard.management import cli
from agentguard.management.store import AccessError

pytestmark=pytest.mark.integration
INVITE='ag7e_'+'A'*43
RUNNER='ag7r_'+'B'*43


@pytest.fixture(autouse=True)
def private_directory(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    secure_fixture_directory(tmp_path)


def invoke(tmp_path,*args):
    return CliRunner().invoke(app,['management','enroll','--server','http://127.0.0.1:1','--credential-file',str(tmp_path/'runner.key'),*args])


@pytest.mark.parametrize('kind',['invalid','cancelled','echo_fallback'])
def test_invalid_or_cancelled_input_never_contacts_server_or_leaves_final_file(tmp_path,monkeypatch,kind):
    calls=[]
    monkeypatch.setattr(cli.Client,'request',lambda *a,**k:calls.append(1))
    def prompt(*args,**kwargs):
        if kind=='cancelled':raise KeyboardInterrupt()
        if kind=='echo_fallback':warnings.warn('SYNTHETIC_SECRET',getpass.GetPassWarning)
        return 'bad'
    monkeypatch.setattr(cli.getpass,'getpass',prompt)
    result=invoke(tmp_path)
    assert result.exit_code==2
    assert calls==[] and not (tmp_path/'runner.key').exists()
    assert 'SYNTHETIC_SECRET' not in result.output


def test_explicit_stdin_is_bounded_and_secret_never_echoed(tmp_path,monkeypatch):
    calls=[]
    def exchange(self,path,body):
        calls.append((path,body=={'enrollment_token':INVITE}))
        return dict(id='a'*32,project='b'*32,runner_token=RUNNER)
    monkeypatch.setattr(cli.Client,'request',exchange)
    result=CliRunner().invoke(app,['management','enroll','--server','http://127.0.0.1:1','--credential-file',str(tmp_path/'runner.key'),'--enrollment-stdin'],input=INVITE+'\n')
    assert result.exit_code==0
    assert calls==[('/api/runner/exchange',True)]
    assert INVITE not in result.output and RUNNER not in result.output
    assert cli.credential(tmp_path/'runner.key')==RUNNER


@pytest.mark.parametrize('value',['',INVITE+'\nextra',INVITE+' ', 'X'*1024])
def test_invalid_stdin_never_exchanges(tmp_path,monkeypatch,value):
    calls=[];monkeypatch.setattr(cli.Client,'request',lambda *a,**k:calls.append(1))
    result=CliRunner().invoke(app,['management','enroll','--server','http://127.0.0.1:1','--credential-file',str(tmp_path/'runner.key'),'--enrollment-stdin'],input=value)
    assert result.exit_code==2 and not calls and not (tmp_path/'runner.key').exists()
    assert INVITE not in result.output


def test_transport_uncertainty_is_not_retried(tmp_path,monkeypatch):
    calls=[];monkeypatch.setattr(cli.getpass,'getpass',lambda *a:INVITE)
    def fail(*args,**kwargs):calls.append(1);raise AccessError('RPC_TRANSPORT_UNVERIFIED',502)
    monkeypatch.setattr(cli.Client,'request',fail)
    result=invoke(tmp_path)
    assert result.exit_code==2 and calls==[1]
    report=json.loads(result.output)
    assert report['operation_status']=='unknown' and report['recovery_required']
    assert not (tmp_path/'runner.key').exists()


def test_operation_failure_does_not_publish_empty_file(tmp_path):
    def failed():raise AccessError('UNAUTHENTICATED',401)
    with pytest.raises(Exception):cli.private_file(tmp_path/'runner.key',failed)
    assert not (tmp_path/'runner.key').exists()


def test_existing_file_untouched_and_operation_not_called(tmp_path):
    target=tmp_path/'runner.key';target.write_bytes(b'existing-user-data');before=target.stat().st_mtime_ns
    calls=[]
    with pytest.raises(Exception):cli.private_file(target,lambda:calls.append(1))
    assert calls==[] and target.read_bytes()==b'existing-user-data' and target.stat().st_mtime_ns==before


def test_publication_failure_after_acceptance_has_separate_safe_status(tmp_path,monkeypatch):
    from agentguard.management import credentials
    calls=[];monkeypatch.setattr(cli.getpass,'getpass',lambda *a:INVITE)
    def accepted(*args,**kwargs):calls.append(1);return dict(runner_token=RUNNER,id='a'*32,project='b'*32)
    monkeypatch.setattr(cli.Client,'request',accepted)
    def full(*args):raise OSError(28,'SYNTHETIC_SECRET')
    monkeypatch.setattr(credentials,'_write_secret',full)
    result=invoke(tmp_path);report=json.loads(result.output)
    assert result.exit_code==2 and calls==[1]
    assert report['operation_status']=='accepted' and report['recovery_required']
    assert not report['credential_written'] and not (tmp_path/'runner.key').exists()
    assert INVITE not in result.output and RUNNER not in result.output and 'SYNTHETIC_SECRET' not in result.output


def test_accidental_secret_cli_argument_is_rejected_without_echo(tmp_path):
    result=invoke(tmp_path,INVITE)
    assert result.exit_code==2 and INVITE not in result.output


def test_hidden_input_success_and_definite_rejection_are_distinct(tmp_path,monkeypatch):
    monkeypatch.setattr(cli.getpass,'getpass',lambda *a:INVITE)
    calls=[]
    def reject(*args,**kwargs):calls.append(1);raise AccessError('ENROLLMENT_INVALID',401)
    monkeypatch.setattr(cli.Client,'request',reject)
    result=invoke(tmp_path);report=json.loads(result.output)
    assert report['operation_status']=='rejected' and not report['recovery_required']
    assert calls==[1] and not (tmp_path/'runner.key').exists()
    monkeypatch.setattr(cli.Client,'request',lambda *a,**k:dict(runner_token=RUNNER))
    assert invoke(tmp_path).exit_code==0


def test_stdin_option_refuses_interactive_echo_input(tmp_path,monkeypatch):
    stream=io.StringIO(INVITE);stream.isatty=lambda:True
    monkeypatch.setattr(cli.sys,'stdin',stream)
    # Direct invocation avoids CliRunner replacing stdin with its own pipe.
    with pytest.raises(Exception):cli.enroll('http://127.0.0.1:1',tmp_path/'runner.key',True)
    assert stream.tell()==0 and not (tmp_path/'runner.key').exists()


def test_recover_owner_preserves_private_file_contract(tmp_path):
    initialized=CliRunner().invoke(app,['management','init','--state',str(tmp_path/'state'),
        '--owner','synthetic-owner','--credential-file',str(tmp_path/'owner.key')])
    workspace=json.loads(initialized.output)['workspace']
    args=['management','recover-owner','--state',str(tmp_path/'state'),'--workspace',workspace,
        '--user','synthetic-owner','--reason','isolated regression','--credential-file',str(tmp_path/'recovered.key')]
    result=CliRunner().invoke(app,args)
    assert result.exit_code==0
    secret=cli.credential(tmp_path/'recovered.key');before=(tmp_path/'recovered.key').read_bytes()
    assert secret not in result.output
    assert CliRunner().invoke(app,args).exit_code==2
    assert (tmp_path/'recovered.key').read_bytes()==before


def test_real_exchange_committed_then_connection_lost_is_unknown_without_retry(tmp_path,monkeypatch):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import threading
    from test_phase7_workflow import setup_project
    service,owner,project=setup_project(tmp_path)
    invitation=service.enroll_runner(owner,project,'Synthetic disconnect fixture')['enrollment_token']
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            service.exchange_enrollment(data['enrollment_token']);calls.append(1)
            self.close_connection=True  # The response never reaches the client.
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    monkeypatch.setattr(cli.getpass,'getpass',lambda *a:invitation)
    try:
        result=CliRunner().invoke(app,['management','enroll','--server',f'http://127.0.0.1:{server.server_port}',
            '--credential-file',str(tmp_path/'runner.key')])
    finally:server.shutdown();server.server_close();thread.join(5)
    assert result.exit_code==2 and calls==[1]
    report=json.loads(result.output)
    assert report['operation_status']=='unknown' and report['recovery_required']
    assert not (tmp_path/'runner.key').exists() and invitation not in result.output
    with service.db() as db:
        assert db.execute('SELECT count(*) FROM enrollments WHERE used=1').fetchone()[0]==1
        assert db.execute('SELECT count(*) FROM runners').fetchone()[0]==1
