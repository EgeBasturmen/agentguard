import multiprocessing
import os
from pathlib import Path
import socket
import ssl
import subprocess
import threading
import time
import pytest

pytestmark=pytest.mark.docker

def _orphan_browser(channel):
    from agentguard.environments.browser import BrowserEnvironment
    env=BrowserEnvironment();env.provision({'browser':{'page':'home','pages':{'home':{'fields':{},'text':'Fixture','submitted':False}}}})
    channel.send((env.root,env.namespace));channel.close();os._exit(19)

def test_real_browser_parent_crash_closes_context_and_owned_workspace():
    from agentguard.environments.browser import BrowserEnvironment
    from agentguard.environments.adapters import FilesystemEnvironment
    context=multiprocessing.get_context('spawn');reader,writer=context.Pipe(duplex=False)
    process=context.Process(target=_orphan_browser,args=(writer,));process.start();writer.close()
    root=None
    try:
        assert reader.poll(25);root,namespace=reader.recv();process.join(timeout=10)
        assert process.exitcode==19
        deadline=time.monotonic()+12
        while Path(root).exists() and time.monotonic()<deadline:time.sleep(.1)
        assert not Path(root).exists(),'Attempt-owned browser helper must reap itself after actual controller death'
    finally:
        if process.is_alive():process.kill();process.join(timeout=5)
        reader.close()
        if root and Path(root).exists():
            try:BrowserEnvironment(root=root,namespace=namespace)._request('close',{})
            except Exception:pass
            time.sleep(.3);FilesystemEnvironment(root=root,namespace=namespace).cleanup()

def test_tls_stalled_handshake_does_not_block_other_clients(tmp_path):
    import shutil,http.client
    from test_phase7_workflow import setup_project
    from agentguard.management.http import make_server
    binary=shutil.which('openssl') or 'C:/Program Files/Git/usr/bin/openssl.exe'
    certificate=tmp_path/'cert.pem';key=tmp_path/'key.pem'
    subprocess.run([binary,'req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(certificate),'-days','1','-subj','/CN=localhost'],capture_output=True,check=True,timeout=15)
    service,_,_=setup_project(tmp_path);context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(certificate,key)
    server=make_server(service,tls_context=context,request_timeout=.5);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    stalled=socket.create_connection(server.server_address,timeout=2)
    try:
        time.sleep(.1)
        connection=http.client.HTTPSConnection('127.0.0.1',server.server_port,context=ssl._create_unverified_context(),timeout=2)
        try:
            connection.request('GET','/');response=connection.getresponse();assert response.status==200;response.read()
        finally:connection.close()
    finally:stalled.close();server.shutdown();server.server_close();thread.join(timeout=5)

def _run_until_crash(root):
    from agentguard.integrations.plan import IntegrationPlan,run_integration_plan
    value=IntegrationPlan.model_validate(dict(schema_version=1,scenario=dict(name='crash',task='wait',initial_state={'files':{}},expected_state=[dict(path='files',value={})]),
        agent=dict(transport='python',reference='agent:Agent'),environment=dict(kind='filesystem'),policy=dict(timeout_seconds=120)))
    run_integration_plan(value,base_dir=root)

def test_actual_container_owner_crash_requires_exact_recovery_and_stays_blocked(tmp_path):
    import json
    from agentguard.execution.recovery import reap_container
    (tmp_path/'agent.py').write_text('import time\nclass Agent:\n    def run(self,task,sandbox): time.sleep(90)\n')
    process=multiprocessing.get_context('spawn').Process(target=_run_until_crash,args=(tmp_path,));process.start();container=None
    try:
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            ids=subprocess.check_output(['docker','ps','--no-trunc','--filter','label=io.agentguard.owner_pid='+str(process.pid),'--format','{{.ID}}'],text=True).split()
            if ids:container=ids[0];break
            time.sleep(.1)
        assert container,'Expected a genuinely running controlled container'
        info=json.loads(subprocess.check_output(['docker','inspect',container],text=True))[0];run_id=info['Config']['Labels']['io.agentguard.run_id']
        with pytest.raises(ValueError,match='still alive'):reap_container(container,run_id)
        process.kill();process.join(timeout=10)
        with pytest.raises(ValueError):reap_container(container,'f'*32)
        receipt=reap_container(container,run_id)
        assert receipt['resource_cleanup']=='verified' and receipt['gate_decision']=='BLOCK' and not receipt['authorizes_release']
    finally:
        if process.is_alive():process.kill();process.join(timeout=5)
        if container:subprocess.run(['docker','rm','--force',container],capture_output=True,timeout=15)

def test_late_real_cache_recovers_cleanup_but_never_lost_lease_pass(tmp_path):
    import json
    from test_phase7_workflow import setup_project
    from test_phase7_docker import prepare
    from test_phase7_queue import runner
    from agentguard.management.http import running_server
    from agentguard.management.client import Client
    from agentguard.management.runner import PrivateRunner
    from agentguard.management.service import AccessError
    service,token,project=setup_project(tmp_path);source=tmp_path/'source';source.mkdir()
    (source/'agent.py').write_text("class Agent:\n    def run(self,task,sandbox): sandbox.call('file.write',{'filename':'note.txt','content':'done'})\n")
    _,suite=prepare(service,token,project,source);worker=runner(service,token,project);job=service.enqueue(token,project,suite['id'],'late-cache')
    class Late(PrivateRunner):
        def _cache(self,claim,result):
            super()._cache(claim,result)
            now=service.clock();service.clock=lambda:now+61;service.reconcile()
    with running_server(service) as url:
        private=Late(Client(url,worker['runner_token']),source_root=source,cache_root=tmp_path/'cache')
        with pytest.raises(AccessError,match='STALE_LEASE'):private.run_once()
    report=json.loads((tmp_path/'cache'/project/(job['id']+'.json')).read_text())
    recovered=service.recover_job(job['id'],report,operator='technical-fixture',reason='Verified actual private runner cache')
    assert recovered['cleanup_status']=='verified' and recovered['gate_decision']=='BLOCK'
    assert service.job(token,project,job['id'])['gate_decision']=='BLOCK'
    assert service.usage(token,project)['reserved_seconds']>0
