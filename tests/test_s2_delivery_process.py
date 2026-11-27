"""Process death after SQLite commit, then HTTP restart on same origin. PYTEST_DONT_REWRITE."""
import multiprocessing,os,threading
import pytest
from agentguard.management.cache import EvidenceCache
from agentguard.management.client import Client
from agentguard.management.delivery import RecoveryPolicy,deliver_pending
from test_s2_dispatch import fixture
pytestmark=pytest.mark.integration

def _server_process(root,pipe,committed,stop):
    from agentguard.management.service import ControlPlane
    from agentguard.management.http import make_server
    service=ControlPlane(root,migrate_schema=False);original=service.finish
    def finish(*a,**kw):
        original(*a,**kw);committed.set();os._exit(71)
    service.finish=finish;server=make_server(service);server.timeout=.1
    pipe.send(server.server_port);pipe.close()
    try:
        while not stop.is_set():server.handle_request()
    finally:server.server_close()

def test_actual_server_process_crash_recovers_same_receipt_without_execution(tmp_path):
    from scripts.sprint1_verification_support import secure_fixture_directory
    from agentguard.management.service import ControlPlane
    from agentguard.management.http import make_server
    secure_fixture_directory(tmp_path)
    s,o,p,c,suite,w,j,case=fixture(tmp_path);claim=s.claim(w['runner_token'])
    context=multiprocessing.get_context('spawn');committed=context.Event();stop=context.Event();receive,send=context.Pipe(duplex=False)
    process=context.Process(target=_server_process,args=(s.root,send,committed,stop),daemon=True);process.start();send.close()
    servers=[];threads=[];errors=[]
    try:
        assert receive.poll(15);port=receive.recv();url='http://127.0.0.1:'+str(port)
        cache=EvidenceCache(tmp_path/'cache',server=url,credential=w['runner_token']);cache.begin(claim);cache.complete(claim,{'failure':'RUNNER_EXECUTION_ERROR'})
        def restart():
            try:
                assert committed.wait(15);process.join(5);assert process.exitcode==71
                server=make_server(ControlPlane(s.root,migrate_schema=False),port=port);servers.append(server)
                thread=threading.Thread(target=server.serve_forever,daemon=True);threads.append(thread);thread.start()
            except BaseException as error:errors.append(type(error).__name__)
        observer=threading.Thread(target=restart,daemon=True);observer.start()
        reply=deliver_pending(cache,Client(url,w['runner_token']),p,j['id'],policy=RecoveryPolicy(max_attempts=5,total_seconds=8,request_timeout=1,base_delay=.2,max_delay=.5))
        observer.join(5)
        assert not errors and process.exitcode==71 and reply['delivery_accepted'] and reply['gate_decision']=='BLOCK'
        assert cache.scan()[0]['state']=='acknowledged'
        with s.db() as db:
            assert db.execute('SELECT count(*) FROM artifacts WHERE job=?',(j['id'],)).fetchone()[0]==0
            assert db.execute("SELECT count(*) FROM usage WHERE job=? AND kind='admitted_run'",(j['id'],)).fetchone()[0]==1
            assert db.execute("SELECT count(*) FROM events WHERE job=? AND kind='RUNNER_EXECUTION_ERROR'",(j['id'],)).fetchone()[0]==1
    finally:
        stop.set();process.join(5);receive.close()
        for server in servers:server.shutdown();server.server_close()
        for thread in threads:thread.join(3)
