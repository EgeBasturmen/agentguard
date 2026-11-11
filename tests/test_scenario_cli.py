"""Public CLI: no hidden auto-approval; real generation/review/execution and safe reports."""
import json
from typer.testing import CliRunner
from phase3_service import Service
from test_generated_execution import http_request
from agentguard.cli.main import app
from agentguard.generation.io import import_dataset,load_execution


def test_cli_generate_review_approve_and_run_real_http_suite(tmp_path):
    runner=CliRunner()
    with Service() as service:
        request=tmp_path/'request.json';request.write_text(http_request(service).model_dump_json())
        database=tmp_path/'scenarios.sqlite';dataset=tmp_path/'drafts.json';snapshot=tmp_path/'suite.json';report=tmp_path/'run.json'
        def call(arguments,code=0):
            result=runner.invoke(app,['scenario',*arguments])
            assert result.exit_code==code,(result.output,result.exception)
            return json.loads(result.output) if code==0 else {"error":result.output}
        generated=call(['generate',str(request),'--db',str(database),'--output',str(dataset)])
        assert generated['approved']==0 and generated['review_required']
        case=import_dataset(dataset).cases[0]
        call(['freeze','release','--db',str(database),'--member',case.id+':1','--output',str(snapshot)],code=2)
        validation=call(['validate',case.id,'1','--db',str(database)])
        assert validation['ready_for_review'] and validation['review_required']
        args=['review',case.id,'1','--db',str(database),'--reviewer','operator','--confirm-fixture','--confirm-classification']
        for key in validation['oracle_ids']:args.extend(['--confirm-oracle',key])
        call(args)
        call(['approve',case.id,'1','--db',str(database),'--reviewer','approver','--fingerprint',case.fingerprint])
        call(['freeze','release','--db',str(database),'--member',case.id+':1','--output',str(snapshot)])
        result=call(['run',str(snapshot),'--db',str(database),'--base-dir',str(tmp_path),'--output',str(report)])
        assert result['gate_decision']=='ALLOW'
        loaded=load_execution(report)
        assert loaded.passed
        forged=json.loads(report.read_text());forged['cases']=[]
        report2=tmp_path/'forged.json';report2.write_text(json.dumps(forged))
        import pytest
        with pytest.raises(ValueError):load_execution(report2)
        matrix=call(['coverage',str(dataset),'--db',str(database),'--execution',str(report)])
        assert matrix['missing_executed'] and any(row['passed'] for row in matrix['rows'])
        assert not service.spaces
