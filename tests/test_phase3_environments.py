"""Real environment lifecycle and namespace isolation acceptance tests."""
import pytest

@pytest.mark.parametrize('kind',['filesystem','dataset'])
def test_environment_provision_inspect_reset_cleanup(kind):
    from agentguard.environments import FilesystemEnvironment,DocumentEnvironment
    environment=FilesystemEnvironment() if kind=='filesystem' else DocumentEnvironment()
    fixture={'files':{'note.txt':'draft'}} if kind=='filesystem' else {'documents':{'d1':{'content':'hello world','source':'fixture'}}}
    environment.provision(fixture)
    try:
        assert environment.snapshot()==fixture
        if kind=='filesystem':
            environment.invoke('file.write',{'filename':'note.txt','content':'done'})
            assert environment.snapshot()['files']['note.txt']=='done'
        else:
            assert environment.invoke('document.search',{'query':'world'})[0]['id']=='d1'
        environment.reset(fixture)
        assert environment.snapshot()==fixture
    finally: assert environment.cleanup()=='verified'

@pytest.mark.parametrize('name',['../escape','/absolute','a/../../x','a\\b'])
def test_filesystem_scope_escape_is_rejected(name):
    from agentguard.environments import FilesystemEnvironment
    env=FilesystemEnvironment();env.provision({'files':{}})
    try:
        with pytest.raises((ValueError,PermissionError)): env.invoke('file.write',{'filename':name,'content':'bad'})
        assert env.snapshot()=={'files':{}}
    finally: env.cleanup()
