"""Real Chromium review/reload checks on isolated fixtures; no runner or paid APIs."""
import json
from pathlib import Path
import tempfile
import argparse
import threading
import re
from dataclasses import dataclass
from contextlib import contextmanager

from agentguard.management.http import make_server
from agentguard.management.service import ControlPlane
from sprint1_verification_support import secure_fixture_directory, FixturePermissionError


class ReviewSmokeError(RuntimeError):
    """Fixed diagnostic codes only; never API bodies, identities or credentials."""


@dataclass(frozen=True)
class ReviewTarget:
    object_id: str
    case_id: str
    revision: int
    fingerprint: str


def _target(item):
    if not isinstance(item,dict) or not isinstance(item.get('case'),dict):
        raise ReviewSmokeError('REVIEW_IDENTITY_UNVERIFIED')
    case=item['case'];identity=item.get('id');case_id=case.get('id');revision=case.get('revision');fingerprint=case.get('fingerprint')
    if (not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{32}',identity)
        or not isinstance(case_id,str) or not case_id.strip() or len(case_id)>200
        or type(revision) is not int or revision<1
        or not isinstance(fingerprint,str) or not re.fullmatch('[0-9a-f]{64}',fingerprint)):
        raise ReviewSmokeError('REVIEW_IDENTITY_UNVERIFIED')
    return ReviewTarget(identity,case_id,revision,fingerprint)


def select_review_case(items):
    if not isinstance(items,list):raise ReviewSmokeError('REVIEW_CASE_LIST_UNVERIFIED')
    targets=[_target(item) for item in items if isinstance(item,dict) and item.get('ready') is True and item.get('status') in ('draft','reviewed')]
    if not targets:raise ReviewSmokeError('NO_REVIEWABLE_CASE')
    if len({target.object_id for target in targets})!=len(targets):
        raise ReviewSmokeError('REVIEW_IDENTITY_AMBIGUOUS')
    # API ordering is not selection authority: created timestamps may tie and IDs
    # are random. Pin both object identity and immutable candidate revision.
    return min(targets,key=lambda target:(target.case_id,target.revision,target.object_id))


def validate_review_target(item,target):
    if _target(item)!=target or item.get('ready') is not True or item.get('status') not in ('draft','reviewed'):
        raise ReviewSmokeError('REVIEW_TARGET_CHANGED_OR_UNREADY')


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
    from playwright.sync_api import sync_playwright, expect
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
                with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/generate')) as generated:
                    page.locator('#generate button').click()
                if generated.value.status!=200:raise ReviewSmokeError('GENERATION_POST_UNVERIFIED')
                created={item['id']:item for item in generated.value.json()['cases']}
                def read_cases(suffix=''):
                    response=page.request.get(url+'/api/projects/'+project+'/cases'+suffix,headers={'Authorization':'Bearer '+owner['token']})
                    if response.status!=200:raise ReviewSmokeError('REVIEW_CASE_HTTP_UNVERIFIED')
                    return response.json()
                rows=[item for item in read_cases() if item['id'] in created]
                if len(rows)!=len(created):raise ReviewSmokeError('REVIEW_CASE_LIST_INCOMPLETE')
                target=select_review_case(rows)
                if _target(created[target.object_id])!=target:raise ReviewSmokeError('GENERATED_CASE_BINDING_CHANGED')
                card=page.locator('#cases article').filter(has=page.locator('input[data-case="'+target.object_id+'"]'))
                expect(card).to_have_count(1)
                expect(card).to_be_visible()
                object_id = card.locator('input[data-case]').get_attribute('data-case')
                if object_id!=target.object_id:raise ReviewSmokeError('REVIEW_CARD_IDENTITY_UNVERIFIED')
                expect(card.locator('strong')).to_contain_text(target.case_id)
                def save_review(expected_status=200):
                    validate_review_target(read_cases('/'+object_id),target)
                    with page.expect_response(lambda response: response.request.method=='POST' and response.url.endswith('/cases/'+object_id+'/review')) as saved:
                        card.get_by_role('button',name='İncelemeyi kaydet').click()
                    if saved.value.status!=expected_status:
                        raise ReviewSmokeError('REVIEW_POST_STATUS_UNVERIFIED')
                selectors = card.locator('select')
                for selector in selectors.all():
                    selector.select_option('confirmed')
                card.locator('input[type=checkbox]').nth(1).check()
                card.locator('input[type=checkbox]').nth(2).check()
                save_review()
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
                    save_review()
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
                    save_review()
                before = service.case(owner['token'], project, object_id)['review']
                if not legacy:
                    # Another tab saved a newer record; this page must not overwrite it.
                    before = service.review_case(owner['token'], project, object_id,
                        decisions=before['decisions'], fixture=True, classification=True,
                        notes='Independent review note from another tab',
                        expected_review_fingerprint=before['fingerprint'])
                    save_review(409)
                    expect(page.locator('#status')).to_contain_text('REVIEW_CHANGED_RELOAD')
                    assert service.case(owner['token'], project, object_id)['review'] == before
                    page.reload()
                    page.locator('#token').fill(owner['token'])
                    page.locator('#login button').click()
                    expect(page.locator('#account-info')).to_contain_text('workspace_id')
                    expect(card.locator('strong')).to_contain_text('reviewed')
                with page.expect_response(lambda response: response.request.method == 'GET' and response.url.endswith('/cases')):
                    save_review()
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
                frozen_invalidation_verified=True, legacy_server_compatible=legacy, live_api_calls=0,
                ready_case_selection_verified=True, case_identity_revision_pinned=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Invalid test port')
    if args.result is not None and args.result.exists():
        parser.error('Result file exists; refusing overwrite')
    try:
        result = dict(current=verify(port=args.port), legacy=verify(legacy=True))
    except FixturePermissionError as error:
        print(json.dumps(dict(status='failed',stage=error.stage,category=error.category,exit_code=error.exit_code)))
        raise SystemExit(2) from None
    except ReviewSmokeError as error:
        print(json.dumps(dict(status='failed',stage='review_smoke',code=str(error))))
        raise SystemExit(2) from None
    if args.result is not None:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        with args.result.open('x', encoding='utf-8') as output:
            json.dump(result, output, indent=2)
    print(json.dumps(result))
