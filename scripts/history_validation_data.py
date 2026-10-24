"""Synthetic, real model/SQLite three-calendar-month data; isolated Azure only."""
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api.history_contract import months_before, partition, stamp
from resource_simulator.config import ModelConfig
from resource_simulator.engine import advance
from resource_simulator.history_sync import payload
from resource_simulator.persistence import Store
from azure.core.credentials import AzureNamedKeyCredential
from azure.data.tables import TableClient, UpdateMode

WORK = ROOT / '.cache/history-azure'
PRIVATE = ROOT / '.secrets/history-test'
DATABASE = WORK / 'rehearsal.sqlite3'


def seed():
    WORK.mkdir(parents=True, exist_ok=True)
    if DATABASE.exists():
        raise ValueError('never overwrite a rehearsal source')
    end = datetime.now(timezone.utc).replace(second=0, microsecond=0) - timedelta(minutes=2)
    start = months_before(end, 3).astimezone(timezone.utc)
    gap_from = start + timedelta(days=40, hours=2)
    count = int((end - start).total_seconds() / 60)
    beta_start = None
    began = time.monotonic()
    with Store(DATABASE) as store:
        session = store.resume(ModelConfig(coefficient=.02), start - timedelta(seconds=1))
        store.transaction = lambda: nullcontext()
        store.connection.execute('BEGIN IMMEDIATE')
        for index in range(count):
            at = start + timedelta(minutes=index)
            if index == 30 * 1440:
                beta_start = (session.state.step, session.beta.resource)
            if gap_from <= at < gap_from + timedelta(minutes=16):
                continue
            for second, sample in [(1, .5 + .3 * math.sin(index / 50)),
                                   (2, .5 + .3 * math.cos(index / 37)),
                                   (3, .5 - .3 * math.sin(index / 50))]:
                state = advance(session.state, session.config, sample, at + timedelta(seconds=second))
                session = store.commit_step(session, state)
            if index % 10000 == 0:
                (WORK / 'seed-progress.json').write_text(json.dumps(dict(minutes=index, total=count)))
        # Closing the last open minute does not invent observations.
        session = store.resume(session.config, end)
        boundary = stamp(start + timedelta(days=30)).replace('.000000', '')
        # Validation-only Beta attachment: preserve recorded initial value/start position,
        # remove all pre-attachment Beta observations rather than fabricating that history.
        store.connection.execute("DELETE FROM outbox WHERE resource_id='beta' AND minute_start<?", (boundary,))
        store.connection.execute("DELETE FROM candles WHERE resource_id='beta' AND minute_start<?", (boundary,))
        store.connection.execute("UPDATE resource_starts SET start_step=?,initial_value=? WHERE resource_id='beta'", beta_start)
        store.connection.execute('COMMIT')
        store.validate_all()
        summary = dict(run_id=session.run_id, generation=1, step=session.state.step,
                       beta_start_step=beta_start[0], start=start.isoformat(), end=end.isoformat(),
                       gap_start=gap_from.isoformat(), gap_minutes=16, minutes=count,
                       source_rows=store.connection.execute('SELECT COUNT(*) FROM candles').fetchone()[0],
                       elapsed_seconds=round(time.monotonic() - began, 3))
    (WORK / 'seed.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary))


def bulk():
    manifest = json.loads((PRIVATE / 'deployment.json').read_text())
    if manifest['storage'] != 'rsimhist20261024' or manifest['group'] != 'rg-resource-simulator-history-test':
        raise ValueError('test isolation guard')
    credential = AzureNamedKeyCredential(manifest['storage'], (PRIVATE / 'loader-key.txt').read_text().strip())
    with sqlite3.connect(DATABASE) as db:
        groups = [(r, resource, day) for r, resource, day in db.execute(
            'SELECT DISTINCT run_id,resource_id,substr(minute_start,1,10) FROM candles ORDER BY 1,2,3')]
    began = time.monotonic()
    def day_upload(group):
        run, resource, day = group
        client = TableClient(manifest['table_endpoint'], 'CurrentState', credential=credential,
                             retry_total=3, logging_enable=False)
        with sqlite3.connect(DATABASE.as_uri() + '?mode=ro', uri=True) as db:
            rows = db.execute('SELECT candle_json FROM candles WHERE run_id=? AND resource_id=? AND '
                              'minute_start>=? AND minute_start<? ORDER BY minute_start',
                              (run, resource, day, (datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat())).fetchall()
        entities = []
        for (raw,) in rows:
            body = payload(raw); item = json.loads(body)
            entities.append(dict(PartitionKey=partition(run, resource), RowKey=item['minute_start'], payload=body))
        for at in range(0, len(entities), 100):
            # Test-only, idempotent seed in a dedicated account/run. Worker still uses create/readback.
            client.submit_transaction([('upsert', e, {'mode': UpdateMode.REPLACE}) for e in entities[at:at + 100]])
        client.upsert_entity(dict(PartitionKey=partition(run, resource), RowKey='day|' + day, revision=str(uuid4())), mode=UpdateMode.REPLACE)
        client.close()
        return len(entities)
    total = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for done, rows in enumerate(pool.map(day_upload, groups), 1):
            total += rows
            (WORK / 'bulk-progress.json').write_text(json.dumps(dict(days=done, total_days=len(groups), rows=total)))
    result = dict(rows=total, elapsed_seconds=round(time.monotonic() - began, 3), test_only=True)
    (WORK / 'bulk.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == '__main__':
    seed() if sys.argv[1] == 'seed' else bulk()
