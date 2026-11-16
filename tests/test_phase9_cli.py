import json
import pytest
from typer.testing import CliRunner
from agentguard.cli.main import app
from test_phase7_workflow import setup_project

pytestmark=pytest.mark.integration

def test_operator_backup_restore_cli_no_secret_output_or_overwrite(tmp_path):
    service,token,_=setup_project(tmp_path);archive=tmp_path/'private.agbackup';cli=CliRunner()
    result=cli.invoke(app,['management','backup','--state',str(service.root),'--output',str(archive)])
    assert result.exit_code==0 and json.loads(result.output)['backup_verified']
    assert token not in result.output and service.signing_key.hex() not in result.output
    before=archive.read_bytes();assert cli.invoke(app,['management','backup','--state',str(service.root),'--output',str(archive)]).exit_code==2
    assert archive.read_bytes()==before
    result=cli.invoke(app,['management','restore',str(archive),'--target',str(tmp_path/'restored'),'--signing-key',str(service.key_path),'--deletions',str(service.root/'deletions.json')])
    assert result.exit_code==0 and json.loads(result.output)['credentials_revoked'] and token not in result.output

def test_account_provisioning_is_explicit_private_and_never_public_signup(tmp_path):
    service,token,_=setup_project(tmp_path);owner=tmp_path/'owner.key';owner.write_text(token)
    if __import__('os').name!='nt':owner.chmod(0o600)
    target=tmp_path/'new.key'
    result=CliRunner().invoke(app,['management','create-account','--state',str(service.root),'--owner-credential',str(owner),'--user','technical-new-account','--name','Technical fixture','--credential-file',str(target)])
    assert result.exit_code==0 and target.read_text().startswith('ag7_') and target.read_text() not in result.output
    assert token not in result.output
