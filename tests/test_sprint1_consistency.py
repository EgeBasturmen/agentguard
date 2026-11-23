"""Control/scenario commits and review concurrency; all state is test-owned."""
import copy
import multiprocessing
import os
import sqlite3

import pytest

from agentguard.management.service import ControlPlane, AccessError
from test_phase7_workflow import setup_project, runtime

pytestmark = pytest.mark.integration


def journal_connection_factory(root, mode):
    """Exercise real pager modes on test-owned connections, including ATTACH."""
    original = sqlite3.connect
    root = root.resolve()
    observed = set()

    class JournalConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if sql.startswith('ATTACH DATABASE ? AS "scenario_'):
                schema = sql.split(' AS ', 1)[1]
                actual = super().execute(f'PRAGMA {schema}.journal_mode={mode}').fetchone()[0]
                assert actual == mode.lower()
                observed.add('scenario')
            return cursor

    def connect(database, *args, **kwargs):
        from pathlib import Path
        if Path(database).resolve().is_relative_to(root):
            kwargs['factory'] = JournalConnection
            connection = original(database, *args, **kwargs)
            actual = connection.execute('PRAGMA main.journal_mode=' + mode).fetchone()[0]
            assert actual == mode.lower()
            observed.add('main')
            return connection
        return original(database, *args, **kwargs)

    return connect, observed


@pytest.fixture(params=['DELETE', 'TRUNCATE', 'PERSIST'])
def journal_mode(request, tmp_path, monkeypatch):
    connect, observed = journal_connection_factory(tmp_path, request.param)
    monkeypatch.setattr(sqlite3, 'connect', connect)
    yield request.param
    assert observed == {'main', 'scenario'}


def prepare(tmp_path, *, reviewed=True, approved=True, frozen=True):
    service, token, project = setup_project(tmp_path)
    connection = service.connect(token, project, 'Sprint 1 fixture', runtime(), artifact_digest='a' * 64)
    request = dict(task='Write done', fixture={'files': {'note.txt': 'draft'}},
                   rules=[dict(id='goal', text='expect files = {"note.txt":"done"}')])
    batch = service.generate_cases(token, project, connection['id'], **request)
    case = service.case(token, project, batch['cases'][0]['id'])
    if reviewed:
        service.review_case(token, project, case['id'], decisions={k: 'confirmed' for k in case['oracle_checks']},
                            fixture=True, classification=True, notes='Independent fixture review')
    if approved:
        service.approve_case(token, project, case['id'], case['case']['fingerprint'])
    suite = service.freeze_suite(token, project, 'Sprint 1', [case['id']]) if frozen else None
    return service, token, project, connection, request, case, suite


def state(service, project):
    with sqlite3.connect(service.db_path) as db:
        control = tuple(db.execute('SELECT id,kind,payload FROM objects ORDER BY id'))
        audit = tuple(db.execute('SELECT id,action,object FROM audit ORDER BY id'))
    with sqlite3.connect(service.scenario_repository_path(project)) as db:
        scenarios = tuple(db.execute('SELECT * FROM cases ORDER BY id,revision'))
        sources = tuple(db.execute('SELECT * FROM sources ORDER BY id'))
        suites = tuple(db.execute('SELECT * FROM suites ORDER BY digest'))
    return control, audit, scenarios, sources, suites


def test_generation_rollback_preserves_existing_frozen_approval(tmp_path, monkeypatch, journal_mode):
    service, token, project, connection, request, case, suite = prepare(tmp_path)
    before = state(service, project)
    original = service._put
    writes = 0

    def fail_second(db, project, kind, value):
        nonlocal writes
        if kind == 'case':
            writes += 1
            if writes == 2:
                raise RuntimeError('synthetic second object failure')
        return original(db, project, kind, value)

    monkeypatch.setattr(service, '_put', fail_second)
    with pytest.raises(RuntimeError, match='synthetic'):
        service.generate_cases(token, project, connection['id'], **{**request, 'task': 'A new task'})
    assert writes == 2
    assert state(service, project) == before
    service.suite_spec(token, project, suite['id'])


def test_failed_commit_rolls_back_control_sources_and_approval(tmp_path, monkeypatch, journal_mode):
    service, token, project, connection, request, _, suite = prepare(tmp_path)
    before = state(service, project)
    original = service.event

    def deny_commit(db, workspace, project, actor, action, obj):
        original(db, workspace, project, actor, action, obj)
        if action == 'scenario.generate':
            db.set_authorizer(lambda action, arg1, arg2, database, trigger:
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION and arg1 == 'COMMIT' else sqlite3.SQLITE_OK)

    monkeypatch.setattr(service, 'event', deny_commit)
    with pytest.raises(sqlite3.DatabaseError):
        service.generate_cases(token, project, connection['id'], **{**request, 'task': 'A new task'})
    assert state(service, project) == before
    service.suite_spec(token, project, suite['id'])


def test_first_generation_failure_rolls_back_repository_ddl_and_can_be_retried(tmp_path, monkeypatch):
    service, token, project = setup_project(tmp_path)
    connection = service.connect(token, project, 'First generation', runtime(), artifact_digest='a' * 64)
    request = dict(task='Write done', fixture={'files': {'note.txt': 'draft'}},
                   rules=[dict(id='goal', text='expect files = {"note.txt":"done"}')])
    original = service.event

    def fail_audit(db, workspace, project, actor, action, obj):
        if action == 'scenario.generate':
            raise RuntimeError('synthetic first generation failure')
        original(db, workspace, project, actor, action, obj)

    monkeypatch.setattr(service, 'event', fail_audit)
    with pytest.raises(RuntimeError, match='synthetic'):
        service.generate_cases(token, project, connection['id'], **request)
    with sqlite3.connect(service.db_path) as db:
        assert db.execute("SELECT count(*) FROM objects WHERE kind='case'").fetchone()[0] == 0
    with sqlite3.connect(service.scenario_repository_path(project)) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    monkeypatch.setattr(service, 'event', original)
    assert service.generate_cases(token, project, connection['id'], **request)['cases']


@pytest.mark.parametrize('operation', ['review', 'approve', 'retire'])
def test_review_lifecycle_and_audit_rollback_together(tmp_path, monkeypatch, operation):
    service, token, project, _, _, case, _ = prepare(
        tmp_path, reviewed=operation != 'review', approved=operation == 'retire', frozen=False)
    before = state(service, project)
    original = service.event

    def fail_audit(db, workspace, project, actor, action, obj):
        original(db, workspace, project, actor, action, obj)
        if action == 'scenario.' + operation:
            raise RuntimeError('synthetic audit failure')

    monkeypatch.setattr(service, 'event', fail_audit)
    with pytest.raises(RuntimeError, match='synthetic'):
        if operation == 'review':
            service.review_case(token, project, case['id'], decisions={k: 'confirmed' for k in case['oracle_checks']},
                                fixture=True, classification=True)
        elif operation == 'approve':
            service.approve_case(token, project, case['id'], case['case']['fingerprint'])
        else:
            service.retire_case(token, project, case['id'])
    assert state(service, project) == before


@pytest.mark.parametrize('operation', ['revise', 'freeze'])
def test_failed_control_object_write_rolls_back_scenario_changes(tmp_path, monkeypatch, operation):
    service, token, project, _, _, case, suite = prepare(tmp_path)
    before = state(service, project)
    original = service._put

    def fail_object(db, project, kind, value):
        if kind == ('case' if operation == 'revise' else 'suite'):
            raise RuntimeError('synthetic object storage failure')
        return original(db, project, kind, value)

    monkeypatch.setattr(service, '_put', fail_object)
    with pytest.raises(RuntimeError, match='synthetic'):
        if operation == 'revise':
            scenario = copy.deepcopy(case['case']['scenario'])
            scenario['task'] = 'Revised task'
            service.revise_case(token, project, case['id'], scenario, 'Explicit new revision')
        else:
            service.freeze_suite(token, project, 'Uncommitted suite', [case['id']])
    assert state(service, project) == before
    service.suite_spec(token, project, suite['id'])


def _crash_generation(root, token, project, connection, request, moment, journal_mode):
    from pathlib import Path
    sqlite3.connect, _ = journal_connection_factory(Path(root), journal_mode)
    service = ControlPlane(root)
    if moment == 'scenario':
        original = service.scenario_repository

        def repository(project):
            repo = original(project)
            add = repo.add

            def crash_after_add(case):
                add(case)
                os._exit(77)

            repo.add = crash_after_add
            return repo

        service.scenario_repository = repository
    elif moment == 'before_commit':
        original = service.event

        def event(db, workspace, project, actor, action, obj):
            original(db, workspace, project, actor, action, obj)
            if action == 'scenario.generate':
                os._exit(77)

        service.event = event
    service.generate_cases(token, project, connection, **{**request, 'task': 'A new task'})
    os._exit(77)


@pytest.mark.parametrize('moment', ['scenario', 'before_commit', 'after_commit'])
def test_actual_process_death_never_leaves_half_committed_generation(tmp_path, moment, journal_mode):
    service, token, project, connection, request, case, suite = prepare(tmp_path)
    before = state(service, project)
    process = multiprocessing.get_context('spawn').Process(
        target=_crash_generation, args=(str(service.root), token, project, connection['id'], request, moment, journal_mode))
    process.start()
    try:
        process.join(timeout=30)
        assert not process.is_alive(), 'Test-owned crash process did not finish'
        assert process.exitcode == 77
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
    reopened = ControlPlane(service.root)
    after = state(reopened, project)
    if moment != 'after_commit':
        assert after == before
        reopened.suite_spec(token, project, suite['id'])
    else:
        assert len(after[0]) > len(before[0]) and len(after[2]) > len(before[2])
        assert reopened.case(token, project, case['id'])['status'] == 'stale'
        with pytest.raises(ValueError):
            reopened.suite_spec(token, project, suite['id'])


def test_stale_browser_review_cannot_overwrite_a_newer_review(tmp_path):
    service, token, project, _, _, case, _ = prepare(tmp_path, approved=False, frozen=False)
    original = service.case(token, project, case['id'])['review']
    decisions = {k: 'confirmed' for k in case['oracle_checks']}
    saved = service.review_case(token, project, case['id'], decisions=decisions, fixture=True, classification=True,
                                notes=original['notes'], expected_review_fingerprint=original['fingerprint'])
    assert saved == original  # Reload and no-change save retain the review identity.
    updated = service.review_case(token, project, case['id'], decisions={k: 'rejected' for k in decisions},
                                  fixture=True, classification=False, expected_review_fingerprint=saved['fingerprint'])
    with pytest.raises(AccessError, match='REVIEW_CHANGED_RELOAD'):
        service.review_case(token, project, case['id'], decisions=decisions, fixture=True, classification=True,
                            expected_review_fingerprint=original['fingerprint'])
    assert service.case(token, project, case['id'])['review'] == updated
    with pytest.raises(ValueError):
        service.approve_case(token, project, case['id'], case['case']['fingerprint'])


def test_case_view_advertises_review_concurrency_without_changing_case_hash(tmp_path):
    service, token, project, _, _, case, _ = prepare(tmp_path, approved=False, frozen=False)
    view = service.case(token, project, case['id'])
    assert view.get('review_concurrency') == 'fingerprint-v1'
    assert view['case']['fingerprint'] == case['case']['fingerprint']


def test_attached_scenarios_are_project_qualified_and_do_not_commit_early(tmp_path):
    service, token, project, _, _, case, _ = prepare(tmp_path, frozen=False)
    other = service.create_project(token, 'Second isolated project')['id']
    original = service.scenario_repository(project).get(case['case']['id'], 1)
    with pytest.raises(RuntimeError, match='synthetic'):
        with service.db():
            first = service.scenario_repository(project)
            second = service.scenario_repository(other)
            assert first.get(case['case']['id'], 1) == original
            with pytest.raises(KeyError):
                second.get(case['case']['id'], 1)
            first.retire(case['case']['id'], 1)
            raise RuntimeError('synthetic outer rollback')
    assert service.scenario_repository(project).get(case['case']['id'], 1) == original


def test_stale_draft_tab_cannot_replace_the_first_saved_review(tmp_path):
    service, token, project, _, _, case, _ = prepare(tmp_path, reviewed=False, approved=False, frozen=False)
    decisions = {k: 'confirmed' for k in case['oracle_checks']}
    saved = service.review_case(token, project, case['id'], decisions=decisions, fixture=True, classification=True,
                                expected_review_fingerprint='')
    with pytest.raises(AccessError, match='REVIEW_CHANGED_RELOAD'):
        service.review_case(token, project, case['id'], decisions={k: 'unknown' for k in decisions},
                            fixture=False, classification=False, expected_review_fingerprint='')
    assert service.case(token, project, case['id'])['review'] == saved


def test_successful_revision_still_invalidates_the_old_frozen_suite(tmp_path):
    service, token, project, _, _, case, suite = prepare(tmp_path)
    revised = service.revise_case(token, project, case['id'], case['case']['scenario'], 'New revision')
    assert revised['status'] == 'draft'
    assert service.case(token, project, case['id'])['status'] == 'stale'
    with pytest.raises(ValueError):
        service.suite_spec(token, project, suite['id'])


@pytest.mark.parametrize('which', ['control', 'scenario'])
def test_non_atomic_journal_mode_is_rejected_without_implicit_conversion(tmp_path, which):
    service, token, project, _, _, case, _ = prepare(tmp_path, approved=False, frozen=False)
    target = service.db_path if which == 'control' else service.scenario_repository_path(project)
    with sqlite3.connect(target) as db:
        assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    with pytest.raises(AccessError, match='ATOMIC_SCENARIO_STORAGE_REQUIRED'):
        service.review_case(token, project, case['id'], decisions={k: 'confirmed' for k in case['oracle_checks']},
                            fixture=True, classification=True)
    with sqlite3.connect(target) as db:
        assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
