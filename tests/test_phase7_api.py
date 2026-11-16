import json
import urllib.request
import urllib.error
import pytest
from test_phase7_workflow import setup_project

pytestmark=pytest.mark.integration

def test_real_http_auth_origin_limits_and_packaged_ui(tmp_path):
    from agentguard.management.http import running_server
    service,token,project=setup_project(tmp_path)
    with running_server(service) as url:
        def request(path,body=None,key=token,extra=None):
            headers={'Authorization':'Bearer '+key,**(extra or {})}
            if body is not None:headers['Content-Type']='application/json'
            req=urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            return urllib.request.urlopen(req,timeout=5)
        with request('/api/projects') as response:assert json.load(response)[0]['id']==project
        with pytest.raises(urllib.error.HTTPError) as error:request('/api/projects',key='wrong')
        assert error.value.code==401
        with pytest.raises(urllib.error.HTTPError) as error:request('/api/projects',extra={'Origin':'https://attacker.invalid'})
        assert error.value.code==403
        with pytest.raises(urllib.error.HTTPError) as error:request('/api/projects',extra={'X-AgentGuard-API-Version':'99'})
        assert error.value.code==400
        with request('/') as response:
            page=response.read().decode();assert 'Agent’ını Bağla' in page and "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
        with request('/app.js') as response:
            javascript=response.read().decode();assert 'localStorage' not in javascript and 'innerHTML' not in javascript
        with pytest.raises(urllib.error.HTTPError) as error:request('/api/projects/'+project+'/operator/activate',{'receipt':'fake'})
        assert error.value.code==404

def test_request_parser_rejects_duplicate_json_and_never_logs_keys(tmp_path,capsys):
    from agentguard.management.http import running_server
    service,token,_=setup_project(tmp_path)
    with running_server(service) as url:
        req=urllib.request.Request(url+'/api/projects',data=b'{"name":"A","name":"B"}',headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        with pytest.raises(urllib.error.HTTPError) as error:urllib.request.urlopen(req,timeout=5)
        assert error.value.code==400 and token.encode() not in error.value.read()
    captured=capsys.readouterr();assert token not in captured.out+captured.err

def test_absolute_connection_deadline_stops_dripping_body(tmp_path):
    import socket
    import threading
    import time
    from agentguard.management.http import make_server
    service,_,_=setup_project(tmp_path)
    server=make_server(service,request_timeout=.2)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with socket.create_connection(server.server_address,timeout=2) as client:
            port=server.server_port
            client.sendall(('POST /api/projects HTTP/1.0\r\nHost: 127.0.0.1:'+str(port)+'\r\nContent-Type: application/json\r\nContent-Length: 10000\r\n\r\n').encode())
            deadline=time.monotonic()+1;closed=False
            while time.monotonic()<deadline:
                try:client.sendall(b' ')
                except OSError:closed=True;break
                time.sleep(.05)
            if not closed:
                client.settimeout(.1)
                try:closed=client.recv(1024)==b''
                except (ConnectionResetError,BrokenPipeError):closed=True
                except TimeoutError:pass
            assert closed,'A peer must not keep a request alive by sending one byte within each socket timeout'
    finally:server.shutdown();server.server_close();thread.join(timeout=3)
