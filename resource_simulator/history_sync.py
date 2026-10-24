"""Immutable cloud history; recoverable delivery cache, never prune DB v3 outbox.

The optional sidecar may be lost: source OHLC/outbox remain and resend safely.
No network operation runs on the calculation thread or inside its transaction.
"""
from contextlib import closing
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from threading import Event, Thread
from uuid import uuid4

from azure.core.exceptions import ResourceExistsError
from azure.data.tables import UpdateMode
from api.history_contract import candle, partition
from .persistence import SCHEMA_VERSION


def payload(raw):
    return json.dumps(candle(json.loads(raw)), sort_keys=True, separators=(',', ':'), allow_nan=False)


class HistorySync:
    def __init__(self, database, publisher):
        self.database = Path(database).resolve()
        self.publisher = publisher
        self.stop = Event()
        self.thread = Thread(target=self._run, name='ohlc-history-sync', daemon=True)

    def once(self, limit=100):
        client = self.publisher.client
        # Destination binding prevents receipts for one cloud from suppressing another.
        target = hashlib.sha256((client.url + '|' + self.publisher.partition).encode()).hexdigest()
        owner = hashlib.sha256(str(self.database).casefold().encode()).hexdigest()
        sidecar = self.database.with_name(self.database.name + '.history-delivery.sqlite3')
        with closing(sqlite3.connect(sidecar, timeout=1)) as delivery, delivery:
            delivery.execute('PRAGMA synchronous=FULL')
            if delivery.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('delivery database is corrupt; preserve and rebuild')
            # v1 receipts lacked source identity/content matching; preserve but do not trust them.
            delivery.execute('CREATE TABLE IF NOT EXISTS receipts_v2 ('
                             'owner TEXT NOT NULL,target TEXT NOT NULL,run TEXT NOT NULL,resource TEXT NOT NULL,'
                             'minute TEXT NOT NULL,hash TEXT NOT NULL,raw_hash TEXT NOT NULL,'
                             'PRIMARY KEY(owner,target,run,resource,minute))')
        source = sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True, timeout=1)
        try:
            if source.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION:
                raise ValueError('unsupported history source')
            source.execute('ATTACH DATABASE ? AS delivery', (sidecar.as_uri() + '?mode=ro',))
            source.create_function('raw_hash', 1, lambda raw: hashlib.sha256(raw.encode()).hexdigest(), deterministic=True)
            rows = source.execute('SELECT c.run_id,c.resource_id,c.minute_start,c.candle_json '
                                  'FROM outbox o JOIN candles c USING(run_id,resource_id,minute_start) '
                                  'WHERE NOT EXISTS (SELECT 1 FROM delivery.receipts_v2 r WHERE '
                                  'r.owner=? AND r.target=? AND r.run=c.run_id AND r.resource=c.resource_id '
                                  'AND r.minute=c.minute_start AND r.raw_hash=raw_hash(c.candle_json)) '
                                  'ORDER BY c.minute_start,c.run_id,c.resource_id LIMIT ?', (owner, target, limit)).fetchall()
        finally:
            source.close()
        verified, days = [], set()
        for run, resource, minute, raw in rows:
            if self.stop.is_set():
                break
            body = payload(raw)
            pk = partition(run, resource)
            rk = json.loads(body)['minute_start']
            entity = dict(PartitionKey=pk, RowKey=rk, payload=body)
            try:
                client.create_entity(entity)
            except ResourceExistsError:
                # Retry after an ambiguous success: immutable values must agree.
                pass
            saved = client.get_entity(pk, rk)
            if payload(saved['payload']) != body:
                raise ValueError('immutable cloud OHLC conflict')
            verified.append((owner, target, run, resource, minute, hashlib.sha256(body.encode()).hexdigest(),
                             hashlib.sha256(raw.encode()).hexdigest()))
            days.add((pk, rk[:10]))
        # Publish day invalidations before recording receipt. Any failure replays safely.
        for pk, day in sorted(days):
            client.upsert_entity(dict(PartitionKey=pk, RowKey='day|' + day, revision=str(uuid4())), mode=UpdateMode.REPLACE)
        self._before_receipts()
        with closing(sqlite3.connect(sidecar, timeout=1)) as delivery, delivery:
            delivery.executemany('INSERT OR REPLACE INTO receipts_v2 VALUES (?,?,?,?,?,?,?)', verified)
        return len(verified)

    def _before_receipts(self):
        """Fault-injection seam: successful cloud readback, before atomic local ack."""

    def _run(self):
        failures = 0
        while not self.stop.is_set():
            try:
                sent = self.once()
                if failures:
                    logging.info('history_sync_recovered')
                failures = 0
                delay = 1 if sent == 100 else 5
            except Exception as exc:
                if not failures:
                    logging.warning('history_sync_unavailable type=%s', type(exc).__name__)
                failures += 1
                delay = min(60, 2 ** min(failures, 6))
            self.stop.wait(delay)

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=12)
