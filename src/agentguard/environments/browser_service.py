"""Private JSON-only broker target. Browser objects never enter tool process pickles."""
import copy
import hmac
import html
from http.server import HTTPServer,BaseHTTPRequestHandler
import json
from pathlib import Path
import sys
from agentguard.environments.browser import fixture_check
from agentguard.integrations.http import _json

def serve(root):
    from playwright.sync_api import sync_playwright
    root=Path(root);config=json.loads((root/'config.json').read_text(encoding='utf-8'));state=copy.deepcopy(config['fixture'])
    with sync_playwright() as driver:
        browser=driver.chromium.launch(headless=True)
        try:
            context=browser.new_context(service_workers='block',accept_downloads=False)
            context.route('**/*',lambda route:route.abort())
            page=context.new_page();page.set_default_timeout(1000)
            def render():
                current=state['browser']['pages'][state['browser']['page']]
                fields=''.join('<input id="'+html.escape(k,quote=True)+'" value="'+html.escape(v,quote=True)+'">' for k,v in current['fields'].items())
                page.set_content('<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; form-action &#39;none&#39;"><p id="fixture-text">'+html.escape(current['text'])+'</p><form data-submitted="'+str(current['submitted']).lower()+'">'+fields+'<button type="button" id="fixture-submit">Submit</button></form>')
                # Trusted fixed fixture behavior; no user-supplied executable HTML/JS.
                page.evaluate("()=>{document.querySelector('#fixture-submit').onclick=()=>{document.querySelector('form').dataset.submitted='true'};}")
            def observe():
                current=state['browser']['pages'][state['browser']['page']]
                for name in current['fields']:current['fields'][name]=page.locator('[id="'+name+'"]').input_value()
                current['text']=page.locator('#fixture-text').text_content()
                current['submitted']=page.locator('form').get_attribute('data-submitted')=='true'
                return copy.deepcopy(state)
            render()
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def setup(self):super().setup();self.connection.settimeout(2)
                def do_POST(self):
                    status=200
                    try:
                        if self.path!='/' or not hmac.compare_digest(self.headers.get('X-AgentGuard-Browser-Key',''),config['key']):raise ValueError()
                        size=int(self.headers.get('Content-Length','-1'))
                        if not 0<=size<=131072:raise ValueError()
                        request=_json(self.rfile.read(size))
                        if set(request)!={'action','value'}:raise ValueError()
                        action=request['action'];value=request['value']
                        if action=='snapshot':result=observe()
                        elif action=='reset':
                            fixture_check(value);state.clear();state.update(copy.deepcopy(value));context.clear_cookies();render();result=observe()
                        elif action=='browser.open':
                            if set(value)!={'page'} or value['page'] not in state['browser']['pages']:raise ValueError()
                            observe();state['browser']['page']=value['page'];render();result=observe()
                        elif action=='browser.fill':
                            current=state['browser']['pages'][state['browser']['page']]
                            if set(value)!={'field','value'} or value['field'] not in current['fields'] or not isinstance(value['value'],str) or len(value['value'])>4096:raise ValueError()
                            page.locator('[id="'+value['field']+'"]').fill(value['value']);result=observe()
                        elif action=='browser.submit':
                            if value:raise ValueError()
                            page.locator('#fixture-submit').click();result=observe()
                        elif action=='browser.text':
                            if value:raise ValueError()
                            result={'text':page.locator('#fixture-text').text_content()}
                        elif action=='close':self.server.finished=True;result={'closed':True}
                        else:raise ValueError()
                    except Exception:status=400;result={'error':'BROWSER_OPERATION_REJECTED'}
                    payload=json.dumps(result).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
            with HTTPServer(('127.0.0.1',0),Handler) as server:
                server.timeout=1;server.finished=False
                temporary=root/'ready-next.json';temporary.write_text(json.dumps(dict(port=server.server_port)),encoding='utf-8');temporary.replace(root/'ready.json')
                while not server.finished:server.handle_request()
            context.close()
        finally:browser.close()

if __name__=='__main__':serve(sys.argv[1])
