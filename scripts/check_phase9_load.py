"""Bounded real-HTTP controller load/soak; queued/cancelled fixtures never count as agent PASS."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import statistics
import tempfile
import time

def verify(output,iterations=40,pause_seconds=0):
    from agentguard.management.service import ControlPlane
    from agentguard.management.client import Client
    from agentguard.management.http import running_server
    output=Path(output)
    if output.exists() or output.is_symlink():raise FileExistsError('Result exists')
    if not 1<=iterations<=80 or not 0<=pause_seconds<=60:raise ValueError('Bounded fixture profile required')
    latencies=[];started=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='agentguard-http-soak-') as directory:
        service=ControlPlane(Path(directory)/'state');owner=service.bootstrap('synthetic-load-owner');token=owner['token'];project=service.create_project(token,'Synthetic load fixture')['id']
        credentials=[]
        for index in range(8):
            user='synthetic-load-principal-'+str(index);service.add_member(token,project,user,'developer')
            credentials.append(service.issue_key(token,project,user,['project:read','run:write'])['token'])
        runtime=dict(agent=dict(transport='python',reference='synthetic:Agent'),environment=dict(kind='filesystem'),tools=[dict(connector='environment')],policy=dict(allowed_tools=['file.write']))
        connection=service.connect(token,project,'Configuration only; never executed',runtime,artifact_digest='a'*64)
        batch=service.generate_cases(token,project,connection['id'],task='Fixture scope',fixture={'files':{}},rules=[dict(id='goal',text='expect files = {}')])
        case=service.case(token,project,batch['cases'][0]['id']);service.review_case(token,project,case['id'],decisions={key:'confirmed' for key in case['oracle_checks']},fixture=True,classification=True)
        service.approve_case(token,project,case['id'],case['case']['fingerprint']);suite=service.freeze_suite(token,project,'Fixture',[case['id']])
        with running_server(service) as url:
            base='/api/projects/'+project
            def request(path,body=None,principal=None):
                before=time.monotonic();value=Client(url,token if principal is None else credentials[principal%8]).request(base+path,body);latencies.append(time.monotonic()-before);return value
            def duplicate(index):return request('/runs',dict(suite=suite['id'],idempotency_key='same-key'),principal=index)['id']
            with ThreadPoolExecutor(max_workers=8) as pool:ids=list(pool.map(duplicate,range(24)))
            assert len(set(ids))==1;request('/runs/'+ids[0]+'/cancel',{})
            for index in range(iterations):
                job=request('/runs',dict(suite=suite['id'],idempotency_key='cycle-'+str(index)),principal=index)
                assert request('/runs',dict(suite=suite['id'],idempotency_key='cycle-'+str(index)),principal=index)['id']==job['id']
                request('/runs/'+job['id']+'/cancel',{},principal=index)
                if pause_seconds:time.sleep(pause_seconds)
            usage=request('/usage');health=request('/operations')
            with service.db() as db:
                total=db.execute("SELECT SUM(amount) FROM usage WHERE project=? AND kind='admitted_run'",(project,)).fetchone()[0]
                current=db.execute("SELECT SUM(amount) FROM usage WHERE project=? AND kind='admitted_run' AND created>=?",(project,usage['period_start'])).fetchone()[0]
            assert total==iterations+1 and usage['admitted_runs']==(current or 0) and usage['reserved_seconds']==0 and health['status']=='ready'
            assert all(row['gate_decision']=='BLOCK' for row in request('/runs?limit=100'))
    values=sorted(latencies)
    result=dict(status='passed',real_http=True,cycles=iterations,duplicate_requests=24,concurrency=8,
        admitted_runs=total,requests=len(values),duration_seconds=time.monotonic()-started,
        latency_p50_seconds=statistics.median(values),latency_p95_seconds=values[min(len(values)-1,int(len(values)*.95))],
        principal_rate_limit=120,synthetic_principals=8,deployment_default_principal_rate_limit=120,
        real_agent_execution=False,external_pilot_verified=False,real_payment_verified=False,live_api_calls=0,
        scope='bounded controller-only fixture soak; not production throughput or long-duration qualification')
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--iterations',type=int,default=40);parser.add_argument('--pause-seconds',type=float,default=0)
    args=parser.parse_args();print(json.dumps(verify(args.output,args.iterations,args.pause_seconds)))
