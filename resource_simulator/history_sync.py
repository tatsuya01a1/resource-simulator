"""Immutable cloud history; recoverable delivery cache, never prune DB v3 outbox.

The optional sidecar may be lost: source OHLC/outbox remain and resend safely.
No network operation runs on the calculation thread or inside its transaction.
"""
from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from threading import Event, Thread
from uuid import uuid4

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
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

    def once(self, limit=1000):
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
                                  'ORDER BY c.minute_start DESC,c.run_id,c.resource_id LIMIT ?', (owner, target, limit)).fetchall()
        finally:
            source.close()
        verified, days, groups = [], set(), {}
        for run, resource, minute, raw in rows:
            body = payload(raw)
            pk = partition(run, resource)
            rk = json.loads(body)['minute_start']
            groups.setdefault(pk, []).append((run, resource, minute, raw, rk, body))
        for pk, group in groups.items():
            if self.stop.is_set():
                break
            upper = (datetime.fromisoformat(max(r[4] for r in group)) + timedelta(microseconds=1)).isoformat(timespec='microseconds')
            def readback():
                entities = client.query_entities('PartitionKey eq @pk and RowKey ge @start and RowKey lt @end',
                    parameters=dict(pk=pk, start=min(r[4] for r in group), end=upper), results_per_page=1000)
                return {e['RowKey']: e for e in entities}
            saved = readback()
            missing = []
            for _, _, _, _, rk, body in group:
                if rk in saved:
                    if payload(saved[rk]['payload']) != body:
                        raise ValueError('immutable cloud OHLC conflict')
                else:
                    missing.append(dict(PartitionKey=pk, RowKey=rk, payload=body))
            by_day = {}
            for entity in missing:
                by_day.setdefault(entity['RowKey'][:10], []).append(entity)
            for day, entities in by_day.items():
                for at in range(0, len(entities), 99):
                    try:
                        # One partition transaction makes new candles + invalidation atomic.
                        marker = dict(PartitionKey=pk, RowKey='day|' + day, revision=str(uuid4()), protocol=2)
                        client.submit_transaction([('create', e) for e in entities[at:at + 99]] +
                                                  [('upsert', marker, {'mode': UpdateMode.REPLACE})])
                    except HttpResponseError as exc:
                        if exc.status_code != 409:
                            raise
                        # Concurrent immutable creates are resolved by exact readback.
            if missing:
                saved = readback()
            for run, resource, minute, raw, rk, body in group:
                if rk not in saved or payload(saved[rk]['payload']) != body:
                    raise ValueError('immutable cloud OHLC conflict or incomplete batch')
                verified.append((owner, target, run, resource, minute, hashlib.sha256(body.encode()).hexdigest(),
                                 hashlib.sha256(raw.encode()).hexdigest()))
                days.add((pk, rk[:10]))
        # Publish day invalidations before recording receipt. Any failure replays safely.
        for pk, day in sorted(days):
            try:
                marker = client.get_entity(pk, 'day|' + day)
            except ResourceNotFoundError:
                marker = {}
            # Upgrade legacy/unindexed cloud rows once. Duplicate delivery must not
            # invalidate unchanged days and trigger full browser history reloads.
            if marker.get('protocol') != 2:
                client.upsert_entity(dict(PartitionKey=pk, RowKey='day|' + day, revision=str(uuid4()), protocol=2), mode=UpdateMode.REPLACE)
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
                delay = .25 if sent == 1000 else 5
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
