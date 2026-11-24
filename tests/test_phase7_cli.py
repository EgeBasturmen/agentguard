import pytest
from typer.testing import CliRunner

pytestmark=pytest.mark.integration


@pytest.fixture(autouse=True)
def private_cli_directory(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    secure_fixture_directory(tmp_path)

def test_bootstrap_cli_writes_explicit_private_credential_without_printing_it(tmp_path):
    from agentguard.cli.main import app
    state=tmp_path/'control';key=tmp_path/'credentials'/'owner.key'
    result=CliRunner().invoke(app,['management','init','--state',str(state),'--owner','local-operator','--credential-file',str(key)])
    assert result.exit_code==0
    credential=key.read_text().strip()
    assert credential.startswith('ag7_') and credential not in result.output
    original=key.read_bytes()
    repeated=CliRunner().invoke(app,['management','init','--state',str(state),'--owner','local-operator','--credential-file',str(key)])
    assert repeated.exit_code==2 and key.read_bytes()==original

def test_source_hash_cli_never_imports_agent(tmp_path):
    from agentguard.cli.main import app
    (tmp_path/'agent.py').write_text("raise RuntimeError('HOST_IMPORT_FORBIDDEN')")
    result=CliRunner().invoke(app,['management','source-hash','--workspace',str(tmp_path),'--reference','agent:Agent'])
    assert result.exit_code==0 and 'HOST_IMPORT_FORBIDDEN' not in result.output
