"""Synthetic backup/restore drill only; no production files or credentials are read."""
import json
from pathlib import Path
import sqlite3
import tempfile
import argparse

from agentguard.management.operations import backup, restore
from agentguard.management.service import ControlPlane
from sprint1_verification_support import secure_fixture_directory, FixturePermissionError


def inspect(root, project):
    result = {}
    for name, table in [('control.sqlite', 'control_meta'), (project + '.scenarios.sqlite', 'agentguard_meta')]:
        connection = sqlite3.connect((root / name).as_uri() + '?mode=ro', uri=True)
        try:
            assert connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
            result[name] = connection.execute('SELECT version FROM ' + table).fetchone()[0]
        finally:
            connection.close()
    return result


def verify():
    with tempfile.TemporaryDirectory(prefix='agentguard-sprint1-restore-') as directory:
        root = secure_fixture_directory(directory)
        service = ControlPlane(root / 'source')
        owner = service.bootstrap('synthetic-restore-operator')
        token = owner['token']
        project = service.create_project(token, 'Synthetic restore fixture')['id']
        runtime = dict(agent=dict(transport='python', reference='agent:Agent', source_dir='.'),
            environment=dict(kind='filesystem'), tools=[dict(connector='environment')],
            policy=dict(allowed_tools=['file.write']))
        connection = service.connect(token, project, 'Synthetic configuration', runtime, artifact_digest='a' * 64)
        batch = service.generate_cases(token, project, connection['id'], task='Write done',
            fixture={'files': {'note.txt': 'draft'}},
            rules=[dict(id='goal', text='expect files = {"note.txt":"done"}')])
        case = service.case(token, project, batch['cases'][0]['id'])
        review = service.review_case(token, project, case['id'],
            decisions={k: 'confirmed' for k in case['oracle_checks']}, fixture=True, classification=True)
        service.approve_case(token, project, case['id'], case['case']['fingerprint'])
        suite = service.freeze_suite(token, project, 'Synthetic immutable suite', [case['id']])
        invitation = service.enroll_runner(token, project, 'Synthetic idle runner')
        service.exchange_enrollment(invitation['enrollment_token'])
        repository = service.scenario_repository(project)
        expected_case = repository.get(case['case']['id'], 1)
        expected_sources = repository.sources()
        expected_schema = inspect(service.root, project)
        archive = root / 'synthetic.agbackup'
        backed = backup(service, archive)
        target = root / 'restored'
        restored = restore(archive, target, signing_key=service.key_path, deletions=service.root / 'deletions.json')
        assert backed['backup_verified'] and restored['credentials_revoked']
        assert inspect(target, project) == expected_schema
        recovered = ControlPlane(target)
        repo = recovered.scenario_repository(project)
        assert repo.get(case['case']['id'], 1) == expected_case
        assert repo.sources() == expected_sources
        from agentguard.generation.repository import Snapshot
        snapshot = Snapshot.model_validate(suite['snapshot'])
        repo.check_snapshot(snapshot)
        repo.admission(snapshot, snapshot.cases[0])
        with recovered.db() as db:
            assert db.execute('SELECT count(*) FROM keys WHERE revoked=0').fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM runners WHERE revoked=0').fetchone()[0] == 0
            assert db.execute('SELECT payload FROM objects WHERE id=?', (suite['id'],)).fetchone() is not None
        # The original synthetic state was not changed by the restore.
        assert service.case(token, project, case['id'])['review'] == expected_case[2]
        assert review['case_digest'] == case['case']['fingerprint']
    return dict(status='passed', synthetic_data_only=True, real_user_data_verified=False,
        permissions_verified=True, integrity_verified=True, schemas_verified=True,
        review_source_fingerprints_preserved=True, frozen_suite_pins_preserved=True,
        restored_credentials_revoked=True, original_fixture_credentials_still_valid=True,
        live_state_accessed=False, live_api_calls=0)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', type=Path)
    args = parser.parse_args()
    if args.result is not None and args.result.exists():
        parser.error('Result file exists; refusing overwrite')
    try:
        result = verify()
    except FixturePermissionError as error:
        print(json.dumps(dict(status='failed',stage=error.stage,category=error.category,exit_code=error.exit_code)))
        raise SystemExit(2) from None
    if args.result is not None:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        with args.result.open('x', encoding='utf-8') as output:
            json.dump(result, output, indent=2)
    print(json.dumps(result))
