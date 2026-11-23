"""Real Chromium review/reload checks on isolated fixtures; no runner or paid APIs."""
import json
from pathlib import Path
import tempfile
import argparse
import threading
from contextlib import contextmanager

from playwright.sync_api import sync_playwright, expect

from agentguard.management.http import make_server
from agentguard.management.service import ControlPlane
from sprint1_verification_support import secure_fixture_directory


@contextmanager
def fixture_server(service, port):
    server = make_server(service, '127.0.0.1', port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def verify(*, legacy=False, port=0):
    with tempfile.TemporaryDirectory(prefix='agentguard-sprint1-review-') as directory:
        secure_fixture_directory(directory)
        service = ControlPlane(Path(directory) / 'server')
        owner = service.bootstrap('synthetic-review-operator')
        if legacy:
            # Updated static files must also work with an already-running old server.
            original_view = service._case_view
            original_review = service.review_case

            def old_view(*args):
                value = original_view(*args)
                value.pop('review_concurrency', None)
                return value

            def old_review(token, project, case_id, *, decisions, fixture, classification, notes=''):
                return original_review(token, project, case_id, decisions=decisions,
                                       fixture=fixture, classification=classification, notes=notes)

            service._case_view = old_view
            service.review_case = old_review
        with fixture_server(service, port) as url, sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url)
                page.locator('#token').fill(owner['token'])
                page.locator('#login button').click()
                expect(page.locator('#account-info')).to_contain_text('workspace_id')
                page.locator('#project-name').fill('Sprint 1 review fixture')
                page.locator('#project-create button').click()
                expect(page.locator('#projects option')).to_have_count(1)
                project = page.locator('#projects').input_value()
                page.locator('#connection-name').fill('Synthetic configuration only')
                page.locator('#digest').fill('a' * 64)
                page.locator('#connect button').click()
                expect(page.locator('#connections option')).to_have_count(1)
                page.locator('#task').fill('Write done')
                page.locator('#rules').fill(json.dumps([
                    dict(id='goal', text='expect files = {"note.txt":"done"}'),
                    dict(id='tool', text='tool file.write times 1')]))
                page.locator('#generate button').click()
                card = page.locator('#cases article').first
                expect(card).to_be_visible()
                object_id = card.locator('input[data-case]').get_attribute('data-case')
                selectors = card.locator('select')
                for selector in selectors.all():
                    selector.select_option('confirmed')
                card.locator('input[type=checkbox]').nth(1).check()
                card.locator('input[type=checkbox]').nth(2).check()
                card.get_by_role('button', name='İncelemeyi kaydet').click()
                expect(card.locator('strong')).to_contain_text('reviewed')
                for selector in card.locator('select').all():
                    expect(selector).to_have_value('confirmed')
                expect(card.locator('input[type=checkbox]').nth(1)).to_be_checked()
                expect(card.locator('input[type=checkbox]').nth(2)).to_be_checked()
                # Preserve mixed decisions/false confirmations as well as the happy path.
                card.locator('select').nth(0).select_option('unknown')
                card.locator('select').nth(1).select_option('rejected')
                card.locator('input[type=checkbox]').nth(1).uncheck()
                with page.expect_response(lambda response: response.request.method == 'GET' and response.url.endswith('/cases')):
                    card.get_by_role('button', name='İncelemeyi kaydet').click()
                expect(card.locator('select').nth(0)).to_have_value('unknown')
                expect(card.locator('select').nth(1)).to_have_value('rejected')
                expect(card.locator('input[type=checkbox]').nth(1)).not_to_be_checked()
                expect(card.locator('input[type=checkbox]').nth(2)).to_be_checked()
                card.get_by_role('button', name='Bu sürümü onayla').click()
                expect(page.locator('#status')).to_contain_text('INVALID_SPEC_OR_STATE')
                assert service.case(owner['token'], project, object_id)['status'] == 'reviewed'
                for selector in card.locator('select').all():
                    selector.select_option('confirmed')
                card.locator('input[type=checkbox]').nth(1).check()
                with page.expect_response(lambda response: response.request.method == 'GET' and response.url.endswith('/cases')):
                    card.get_by_role('button', name='İncelemeyi kaydet').click()
                before = service.case(owner['token'], project, object_id)['review']
                if not legacy:
                    # Another tab saved a newer record; this page must not overwrite it.
                    before = service.review_case(owner['token'], project, object_id,
                        decisions=before['decisions'], fixture=True, classification=True,
                        notes='Independent review note from another tab',
                        expected_review_fingerprint=before['fingerprint'])
                    card.get_by_role('button', name='İncelemeyi kaydet').click()
                    expect(page.locator('#status')).to_contain_text('REVIEW_CHANGED_RELOAD')
                    assert service.case(owner['token'], project, object_id)['review'] == before
                    page.reload()
                    page.locator('#token').fill(owner['token'])
                    page.locator('#login button').click()
                    expect(page.locator('#account-info')).to_contain_text('workspace_id')
                    expect(card.locator('strong')).to_contain_text('reviewed')
                with page.expect_response(lambda response: response.request.method == 'GET' and response.url.endswith('/cases')):
                    card.get_by_role('button', name='İncelemeyi kaydet').click()
                assert service.case(owner['token'], project, object_id)['review'] == before
                page.reload()
                page.locator('#token').fill(owner['token'])
                page.locator('#login button').click()
                expect(page.locator('#account-info')).to_contain_text('workspace_id')
                expect(card.locator('strong')).to_contain_text('reviewed')
                for selector in card.locator('select').all():
                    expect(selector).to_have_value('confirmed')
                expect(card.locator('input[type=checkbox]').nth(1)).to_be_checked()
                expect(card.locator('input[type=checkbox]').nth(2)).to_be_checked()
                assert service.case(owner['token'], project, object_id)['review'] == before
                card.get_by_role('button', name='Bu sürümü onayla').click()
                expect(card.locator('strong')).to_contain_text('approved')
                card.locator('input[data-case]').check()
                page.locator('#suite-name').fill('Sprint 1 reviewed suite')
                page.locator('#freeze button').click()
                expect(page.locator('#suites option')).to_have_count(1)
                suite = page.locator('#suites').input_value()
                service.suite_spec(owner['token'], project, suite)
                scenario = service.case(owner['token'], project, object_id)['case']['scenario']
                service.revise_case(owner['token'], project, object_id, scenario, 'Independent test revision')
                try:
                    service.suite_spec(owner['token'], project, suite)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Old frozen suite survived approval invalidation')
            finally:
                browser.close()
    return dict(status='passed', real_chromium=True, isolated_state=True,
                review_rehydrated=True, reload_preserved=True, repeat_save_preserved=True,
                origin=url, permissions_verified=True, approval_verified=True, mixed_decisions_preserved=True, stale_review_blocked=not legacy,
                frozen_invalidation_verified=True, legacy_server_compatible=legacy, live_api_calls=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Invalid test port')
    if args.result is not None and args.result.exists():
        parser.error('Result file exists; refusing overwrite')
    result = dict(current=verify(port=args.port), legacy=verify(legacy=True))
    if args.result is not None:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        with args.result.open('x', encoding='utf-8') as output:
            json.dump(result, output, indent=2)
    print(json.dumps(result))
