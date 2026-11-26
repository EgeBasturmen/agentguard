"""Real Chromium + HTTP control plane + Docker private runner. Synthetic technical fixture, never AG-075 proof."""
import argparse
import json
from pathlib import Path
import tempfile
import threading
from playwright.sync_api import sync_playwright,expect
from agentguard.management.service import ControlPlane
from agentguard.management.http import running_server
from agentguard.management.runner import PrivateRunner
from agentguard.management.client import Client
from agentguard.execution.agent_spec import AgentLaunchSpec

def verify(output,operations=False):
    output=Path(output)
    if output.exists() or output.is_symlink():raise FileExistsError('Verification output exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='agentguard-browser-fixture-') as directory:
        root=Path(directory)
        from agentguard.management import credentials as private_permissions
        import os
        if os.name=='nt':private_permissions._windows_security(root,'harden')
        else:root.chmod(0o700)
        source=root/'source';source.mkdir()
        (source/'agent.py').write_text("class Agent:\n    def run(self,task,sandbox):\n        sandbox.call('file.write',{'filename':'note.txt','content':'done'})\n        return '<script>globalThis.agentguardInjected=1</script>'\n",encoding='utf-8')
        _,sha=AgentLaunchSpec(reference='agent:Agent',source_dir=source).sources()
        service=ControlPlane(root/'control');owner=service.bootstrap('technical-fixture-operator')
        stop=threading.Event();worker_errors=[]
        with running_server(service) as url,sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True)
            context=browser.new_context();page=context.new_page();page_errors=[]
            page.on('pageerror',lambda error:page_errors.append(type(error).__name__))
            page.goto(url);page.locator('#token').fill(owner['token']);page.locator('#login button').click()
            page.locator('#project-name').fill('Technical browser fixture');page.locator('#project-create button').click()
            expect(page.locator('#projects option')).to_have_count(1)
            project=page.locator('#projects').input_value()
            page.locator('#connection-name').fill('Fixture agent');page.locator('#digest').fill(sha);page.locator('#connect button').click()
            expect(page.locator('#connections option')).to_have_count(1)
            page.locator('#enroll button').click();expect(page.locator('#enrollment')).to_contain_text('ag7e_')
            invitation=page.locator('#enrollment').text_content()
            registration=Client(url).request('/api/runner/exchange',dict(enrollment_token=invitation))
            page.locator('#hide-enrollment').click();assert page.locator('#enrollment').text_content()==''
            private=PrivateRunner(Client(url,registration['runner_token']),source_root=source,cache_root=root/'private-evidence')
            def work():
                while not stop.is_set():
                    try:result=private.run_once()
                    except Exception as error:worker_errors.append(type(error).__name__);return
                    if result is None:stop.wait(.2)
            worker=threading.Thread(target=work,daemon=True);worker.start()
            try:
                page.locator('#task').fill('Write done');page.locator('#generate button').click()
                expect(page.locator('#cases article').first).to_be_visible()
                card=page.locator('#cases article').first
                assert all(value=='unknown' for value in card.locator('select').evaluate_all('(nodes)=>nodes.map(n=>n.value)'))
                card.get_by_role('button',name='Bu sürümü onayla').click()
                expect(page.locator('#status')).to_contain_text('INVALID_SPEC_OR_STATE')
                for selector in card.locator('select').all():selector.select_option('confirmed')
                card.locator('input[type=checkbox]').nth(1).check();card.locator('input[type=checkbox]').nth(2).check()
                card.get_by_role('button',name='İncelemeyi kaydet').click()
                expect(page.locator('#cases article').first.locator('strong')).to_contain_text('reviewed')
                page.locator('#cases article').first.get_by_role('button',name='Bu sürümü onayla').click()
                expect(page.locator('#cases article').first.locator('strong')).to_contain_text('approved')
                page.locator('#cases article').first.locator('input[type=checkbox]').first.check()
                page.locator('#freeze button').click();expect(page.locator('#suites option')).to_have_count(1)
                page.locator('#preflight').click()
                expect(page.locator('#runs')).to_contain_text('completed',timeout=90000)
                page.locator('#run').click()
                expect(page.locator('#runs')).to_contain_text('ALLOW',timeout=120000)
                page.locator('#runs article').first.get_by_role('button',name='Raporu görüntüle / indir').click()
                expect(page.locator('#evidence')).to_contain_text('PASS')
                expect(page.locator('#evidence')).to_contain_text('verified')
                assert page.evaluate('globalThis.agentguardInjected===undefined')
                assert page.evaluate('localStorage.length===0 && sessionStorage.length===0')
                assert context.cookies()==[]
                page.locator('#feedback-title').fill('Synthetic technical feedback')
                page.locator('#feedback-body').fill('Browser fixture only; not an external pilot result.')
                page.locator('#feedback button').click();expect(page.locator('#status')).to_contain_text('open')
                assert service.pilot_readiness(owner['token'],project)['external_pilot_verified'] is False
                other=service.create_workspace(owner['token'],'second-technical-fixture','Second fixture')
                service.create_project(other['token'],'Second fixture project')
                # Hold a real private file read across credential replacement. An old
                # action must not upload its evidence using the new session's key.
                actual=next(row for row in service.jobs(owner['token'],project) if row['gate_decision']=='ALLOW')
                private_report=root/'private-evidence'/project/(actual['id']+'.json')
                page.locator('details').filter(has=page.locator('#compare')).locator('summary').click()
                page.locator('#baseline-id').fill(actual['id']);page.locator('#candidate-id').fill(actual['id'])
                page.locator('#baseline-file').set_input_files(private_report);page.locator('#candidate-file').set_input_files(private_report)
                uploads=[]
                def record_upload(route):uploads.append(True);route.continue_()
                page.route('**/api/projects/*/compare',record_upload)
                page.evaluate("""()=>{globalThis.originalPrivateRead=File.prototype.text;globalThis.delayPrivateRead=true;File.prototype.text=function(){if(!globalThis.delayPrivateRead)return globalThis.originalPrivateRead.call(this);globalThis.delayPrivateRead=false;return new Promise(resolve=>{globalThis.releasePrivateRead=()=>globalThis.originalPrivateRead.call(this).then(resolve);});};}""")
                page.locator('#compare button').click()
                for _ in range(40):
                    if page.evaluate('Boolean(globalThis.releasePrivateRead)'):break
                    page.wait_for_timeout(50)
                assert page.evaluate('Boolean(globalThis.releasePrivateRead)')
                page.locator('#token').fill(other['token']);page.locator('#login button').click()
                expect(page.locator('#projects option')).to_have_text('Second fixture project')
                page.evaluate('()=>{globalThis.releasePrivateRead();}');page.wait_for_timeout(500)
                assert not uploads,'Private evidence was uploaded after credential replacement'
                page.evaluate('()=>{File.prototype.text=globalThis.originalPrivateRead;}')
                page.locator('#token').fill(owner['token']);page.locator('#login button').click()
                expect(page.locator('#projects option')).to_have_text('Technical browser fixture')
                held=[];delay=[True]
                def delayed_runs(route):
                    if delay[0] and '/'+project+'/' in route.request.url:
                        delay[0]=False;held.append((route,route.fetch()));return
                    route.continue_()
                page.route('**/api/projects/*/runs',delayed_runs)
                page.locator('#refresh').click()
                for _ in range(50):
                    if held:break
                    page.wait_for_timeout(100)
                assert held
                assert held[0][1].json(),'Expected real prior-project run data in delayed HTTP response'
                page.locator('#token').fill(other['token']);page.locator('#login button').click()
                expect(page.locator('#projects option')).to_have_text('Second fixture project')
                expect(page.locator('#pilot')).to_contain_text('PENDING_EXTERNAL_VALIDATION')
                held[0][0].fulfill(response=held[0][1]);page.wait_for_timeout(500)
                assert page.locator('#runs article').count()==0,'Late prior-tenant response was rendered after credential change'
                assert page.locator('#evidence').text_content()==''
                assert page.locator('#enrollment').text_content()==''
                assert not page_errors and not worker_errors
                if operations:
                    page.locator('#token').fill(owner['token']);page.locator('#login button').click()
                    expect(page.locator('#projects option')).to_have_text('Technical browser fixture')
                    expect(page.locator('#funnel')).to_contain_text('verified_current_runs')
                    page.locator('#plan').click();expect(page.locator('#status')).to_contain_text('pending_manual_payment')
                    with service.db() as db:request=db.execute("SELECT id FROM commercial WHERE project=? AND kind='plan' ORDER BY created DESC",(project,)).fetchone()[0]
                    service.operator_activate_plan(request,operator='synthetic-technical-fixture',receipt_reference='synthetic-offline-reference-no-real-payment')
                    service.reconcile_billing(project,operator='synthetic-technical-fixture',reference='synthetic-offline-reference-no-real-payment',paid=True)
                    page.locator('#cancel-plan').click();expect(page.locator('#status')).to_contain_text('pending_operator_cancellation')
                    with service.db() as db:request=db.execute("SELECT id FROM commercial WHERE project=? AND kind='cancel' ORDER BY created DESC",(project,)).fetchone()[0]
                    service.operator_cancel_plan(request,operator='synthetic-technical-fixture',reason='Technical cancellation only')
                    stop.set();worker.join(timeout=30);assert not worker.is_alive()
                    page.locator('#operations-check').click();expect(page.locator('#operations')).to_contain_text('ready')
                    page.locator('#delete-confirmation').fill('Technical browser fixture');page.locator('#delete-project button').click()
                    expect(page.locator('#status')).to_contain_text('Proje primary verisi silindi')
                    expect(page.locator('#projects option')).to_have_count(0)
                    assert service.projects(owner['token'])==[] and not service.scenario_repository_path(project).exists()
                    expect(page.locator('#account-info')).to_contain_text('Initial workspace')
                    page.locator('#delete-account-confirmation').fill('Initial workspace');page.locator('#delete-account button').click()
                    expect(page.locator('#status')).to_contain_text('Hesap/workspace primary verisi silindi')
                    with service.db() as db:assert db.execute('SELECT 1 FROM workspace_members WHERE workspace=?',(owner['workspace'],)).fetchone() is None
                page.locator('#logout').click();expect(page.locator('#projects option')).to_have_count(0)
            finally:
                stop.set();worker.join(timeout=30);context.close();browser.close()
                if worker.is_alive():raise RuntimeError('Private fixture runner did not stop')
        result=dict(status='passed',browser='chromium',real_http=True,real_docker=True,
            connect_generate_review_freeze_execute_report=True,unknown_oracle_not_approved=True,
            xss_prevented=True,no_browser_credential_storage=True,cross_session_view_cleared=True,private_file_read_fenced=True,
            live_model_calls=0,external_pilot_verified=False)
        if operations:result.update(phase9_journey=True,payment_profile='synthetic operator-recorded manual flow; no real payment',primary_deletion_verified=True,primary_account_deletion_verified=True)
        with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
        return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--operations',action='store_true')
    args=parser.parse_args();print(json.dumps(verify(args.output,operations=args.operations)))
