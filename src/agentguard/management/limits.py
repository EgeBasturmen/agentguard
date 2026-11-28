"""Operator-owned pilot plans. Units are logical bytes and reserved wall seconds, not invoices."""
PLANS={
    'developer':dict(runs=100,concurrency=2,user_concurrency=2,seconds=36000,storage=16*1024*1024,objects=500,model_tokens=1000000,model_cost=1000000),
    'pilot':dict(runs=1000,concurrency=4,user_concurrency=2,seconds=360000,storage=256*1024*1024,objects=2000,model_tokens=10000000,model_cost=10000000),
}
PERIOD=30*86400

def logical_storage(db,project):
    total=0
    for table,field in [('objects','payload'),('artifacts','payload'),('jobs','spec'),('commercial','payload')]:
        total+=db.execute(f'SELECT COALESCE(SUM(length(CAST({field} AS BLOB))),0) FROM {table} WHERE project=?',(project,)).fetchone()[0]
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_receipts'").fetchone():
        total+=db.execute('SELECT COALESCE(SUM(length(CAST(reference AS BLOB))),0) FROM recovery_receipts WHERE project=?',(project,)).fetchone()[0]
    total+=db.execute("SELECT COALESCE(SUM(reserved_bytes),0) FROM jobs WHERE project=? AND state IN ('queued','leased','running','error') AND decision IS NULL",(project,)).fetchone()[0]
    return total
