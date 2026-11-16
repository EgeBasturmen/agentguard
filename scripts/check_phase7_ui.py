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

def verify(output):
    output=Path(output)
    if output.exists() or output.is_symlink():raise FileExistsError('Verification output exists')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='agentguard-browser-fixture-') as directory:
        root=Path(directory);source=root/'source';source.mkdir()
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
                page.locator('#logout').click();expect(page.locator('#projects option')).to_have_count(0)
            finally:
                stop.set();worker.join(timeout=30);context.close();browser.close()
                if worker.is_alive():raise RuntimeError('Private fixture runner did not stop')
        result=dict(status='passed',browser='chromium',real_http=True,real_docker=True,
            connect_generate_review_freeze_execute_report=True,unknown_oracle_not_approved=True,
            xss_prevented=True,no_browser_credential_storage=True,cross_session_view_cleared=True,
            live_model_calls=0,external_pilot_verified=False)
        with output.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
        return result

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    print(json.dumps(verify(parser.parse_args().output)))
