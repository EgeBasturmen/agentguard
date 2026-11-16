"""Pinned transactional v1 -> v2 migration. Never execute operator-supplied SQL."""
VERSION=2
STATEMENTS=[
    'CREATE TABLE schema_history(version INTEGER PRIMARY KEY,source INTEGER NOT NULL)',
    'CREATE TABLE deletion_applied(project TEXT PRIMARY KEY)',
    'CREATE TABLE billing_reconciliation(reference TEXT PRIMARY KEY,project TEXT NOT NULL,status TEXT NOT NULL,created REAL NOT NULL)',
    'CREATE TABLE quota_state(project TEXT PRIMARY KEY,blocked INTEGER NOT NULL,reason TEXT NOT NULL,checked REAL NOT NULL)',
    'CREATE TABLE recovery_receipts(id TEXT PRIMARY KEY,project TEXT,job TEXT,kind TEXT,reference TEXT,created REAL)',
    'CREATE INDEX jobs_project_state ON jobs(project,state)',
    'CREATE INDEX artifacts_expiry ON artifacts(expires)',
    'CREATE INDEX usage_project_period ON usage(project,created)',
    'CREATE INDEX events_job_cursor ON events(job,id)',
    'CREATE INDEX audit_project_cursor ON audit(project,id)',
    'INSERT INTO schema_history VALUES(2,1)',
    'UPDATE control_meta SET version=2',
]
def apply_step(db,statement):db.execute(statement)
def migrate(db):
    for statement in STATEMENTS:apply_step(db,statement)
