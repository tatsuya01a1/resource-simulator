from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from azure.core.exceptions import ResourceExistsError
from api.history_contract import candle, instant, months_before, partition, stamp
from api.history_service import aggregate, OutsideHistory, read_history
from resource_simulator.history_sync import HistorySync
from resource_simulator.config import ModelConfig
from resource_simulator.persistence import Store
from tests.test_stage1 import AT, WorkspaceTest


class HistoryTable:
    url = 'https://unit.table.core.windows.net/CurrentState'

    def __init__(self):
        self.entities = {}
        self.failed = False
        self.ambiguous = False

    def create_entity(self, value):
        if self.failed:
            raise ConnectionError('offline')
        key = (value['PartitionKey'], value['RowKey'])
        if key in self.entities:
            raise ResourceExistsError()
        self.entities[key] = deepcopy(value)
        if self.ambiguous:
            self.ambiguous = False
            raise ConnectionError('response lost after save')

    def get_entity(self, pk, rk):
        if self.failed:
            raise ConnectionError('offline')
        return deepcopy(self.entities[pk, rk])

    def upsert_entity(self, value, **kwargs):
        if self.failed:
            raise ConnectionError('offline')
        self.entities[value['PartitionKey'], value['RowKey']] = deepcopy(value)

    def query_entities(self, query, *, parameters, **kwargs):
        p = parameters
        return iter(deepcopy([value for (pk, rk), value in sorted(self.entities.items())
                             if pk == p['pk'] and p['start'] <= rk < p['end']
                             and ('after' not in p or rk > p['after'])]))


class HistoryTests(WorkspaceTest):
    def test_full_calendar_quarter_projection(self):
        now = instant('2026-10-23T03:00:00Z')
        start = months_before(now, 3)
        run = str(uuid4())
        missing = start + timedelta(days=40, minutes=12)

        class DenseTable:
            def query_entities(self, query, *, parameters, **kwargs):
                at = instant(parameters['start'])
                end = instant(parameters['end'])
                beta = parameters['pk'].endswith('|beta')
                while at < end:
                    if at != missing and (not beta or at >= start + timedelta(days=30)):
                        raw = dict(minute_start=stamp(at), open=100, high=104, low=98, close=101,
                                   sample_count=60, first_observed_at=stamp(at),
                                   last_observed_at=stamp(at + timedelta(seconds=59)))
                        yield dict(PartitionKey=parameters['pk'], RowKey=stamp(at), payload=json.dumps(raw))
                    at += timedelta(minutes=1)

        # 92 calendar days / 132,480 minutes, not 90 days. Beta starts later.
        for resource, days in [('resource', 92), ('beta', 62)]:
            total = samples = 0
            at = start
            while at < now:
                end = min(now, at + timedelta(days=7))
                result = read_history(DenseTable(), dict(run_id=run, resource_id=resource,
                    **{'from': stamp(at), 'to': stamp(end), 'resolution_minutes': '828'}), now)
                self.assertIsNone(result['cursor'])
                total += sum(row['minutes'] for row in result['candles'])
                samples += sum(row['sample_count'] for row in result['candles'])
                self.assertTrue(all(row['open'] == 100 and row['high'] == 104 and row['low'] == 98 and row['close'] == 101
                                    for row in result['candles']))
                at = end
            self.assertEqual(total, days * 1440 - 1)
            self.assertEqual(samples, total * 60)

    def fixture(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            for seconds, sample in [(1, .6), (20, .9), (40, .1), (61, .7), (181, .4), (241, .5)]:
                session = self.step(store, session, sample, AT + timedelta(seconds=seconds))
            run = session.run_id
            raw = [tuple(r) for r in store.connection.execute('SELECT * FROM outbox')]
        self.table = HistoryTable()
        sync = HistorySync(self.database, SimpleNamespace(client=self.table, partition='resource-simulator'))
        self.assertEqual(sync.once(), 6)
        return run, sync, raw

    def test_delivery_restore_resend_and_outbox_invariants(self):
        run, sync, raw = self.fixture()
        self.assertEqual(sync.once(), 0)
        with Store(self.database) as store:
            self.assertEqual([tuple(r) for r in store.connection.execute('SELECT * FROM outbox')], raw)
            self.assertEqual(store.connection.execute('PRAGMA user_version').fetchone()[0], 3)
            store.validate_all()
        # Cache loss is safe: same immutable keys and values; no duplicate cloud rows.
        sidecar = Path(str(self.database) + '.history-delivery.sqlite3')
        sidecar.unlink()
        self.table.failed = True
        with self.assertRaises(ConnectionError):
            sync.once()
        self.table.failed = False
        self.assertEqual(sync.once(), 6)
        self.assertEqual(sum('payload' in e for e in self.table.entities.values()), 6)

    def test_ambiguous_ack_and_conflict_never_overwrite(self):
        self.fixture()
        Path(str(self.database) + '.history-delivery.sqlite3').unlink()
        # Existing immutable conflict fails without acknowledgement or replacement.
        key = next(iter(self.table.entities))
        broken = json.loads(self.table.entities[key]['payload'])
        broken['high'] += 100
        self.table.entities[key]['payload'] = json.dumps(broken)
        sync = HistorySync(self.database, SimpleNamespace(client=self.table, partition='resource-simulator'))
        with self.assertRaises(ValueError):
            sync.once()
        self.assertEqual(json.loads(self.table.entities[key]['payload'])['high'], broken['high'])
        self.table.entities.clear()
        self.table.ambiguous = True
        with self.assertRaises(ConnectionError):
            sync.once()
        self.assertEqual(sync.once(), 6)
        self.assertEqual(sum('payload' in e for e in self.table.entities.values()), 6)

    def test_api_paging_scope_gaps_beta_start_and_run_separation(self):
        run, _, _ = self.fixture()
        params = dict(run_id=run, resource_id='resource', **{'from': stamp(AT), 'to': stamp(AT + timedelta(minutes=5)), 'limit': '2'})
        now = AT + timedelta(minutes=6)
        first = read_history(self.table, params, now)
        self.assertEqual(len(first['candles']), 2)
        second = read_history(self.table, dict(params, cursor=first['cursor']), now)
        self.assertEqual(len(second['candles']), 1)
        self.assertIsNone(second['cursor'])
        rows = first['candles'] + second['candles']
        self.assertEqual([r['sample_count'] for r in rows], [3, 1, 1])
        self.assertEqual(instant(rows[-1]['minute_start']) - instant(rows[-2]['minute_start']), timedelta(minutes=2))
        self.assertEqual(read_history(self.table, dict(params, run_id=str(uuid4())), now)['candles'], [])
        for change in (dict(resource_id='beta'), dict(to=stamp(AT + timedelta(minutes=4)))):
            with self.assertRaises(ValueError):
                read_history(self.table, dict(params, cursor=first['cursor'], **change), now)
        grouped = aggregate(rows, 1440)
        self.assertEqual(len(grouped), 2)  # split at the missing minute
        self.assertEqual(grouped[0]['sample_count'], 4)
        self.assertEqual(grouped[0]['open'], rows[0]['open'])
        self.assertEqual(grouped[0]['close'], rows[1]['close'])
        self.assertEqual(grouped[0]['high'], max(r['high'] for r in rows[:2]))
        self.assertEqual(grouped[0]['low'], min(r['low'] for r in rows[:2]))

    def test_calendar_boundary_half_open_and_invalid_requests(self):
        run, _, _ = self.fixture()
        now = instant('2026-05-31T15:30:00Z')  # June 1 JST
        self.assertEqual(months_before(now, 3).isoformat(), '2026-03-01T00:30:00+09:00')
        self.assertEqual(months_before(instant('2026-05-31T00:00:00Z'), 3).day, 28)
        earliest = months_before(now, 3).replace(second=0, microsecond=0)
        params = dict(run_id=run, resource_id='resource', **{'from': stamp(earliest), 'to': stamp(earliest + timedelta(days=7))})
        self.assertEqual(read_history(self.table, params, now)['candles'], [])
        # Stable anchor survives a minute rollover at the oldest boundary.
        anchored = dict(params, as_of=stamp(now))
        self.assertEqual(read_history(self.table, anchored, now + timedelta(minutes=1))['candles'], [])
        with self.assertRaises(ValueError):
            read_history(self.table, anchored, now + timedelta(minutes=6))
        with self.assertRaises(OutsideHistory):
            read_history(self.table, dict(params, **{'from': stamp(earliest - timedelta(minutes=1)), 'to': stamp(earliest + timedelta(minutes=1))}), now)
        for change in ({'to': stamp(earliest + timedelta(days=7, seconds=1))}, {'from': params['to']}, {'resource_id': 'bad'}, {'cursor': 'bad'}, {'limit': '10081'}, {'from': '2026-01-01'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                read_history(self.table, dict(params, **change), now)

    def test_beta_before_attachment_is_not_created(self):
        run, _, _ = self.fixture()
        params = dict(run_id=run, resource_id='beta', **{'from': stamp(AT - timedelta(days=1)), 'to': stamp(AT)})
        self.assertEqual(read_history(self.table, params, AT + timedelta(minutes=6))['candles'], [])

    def test_manifest_is_bounded_and_changes_only_for_delivered_days(self):
        run, sync, _ = self.fixture()
        params = dict(run_id=run, resource_id='resource', mode='manifest',
                      **{'from': stamp(AT - timedelta(days=7)), 'to': stamp(AT + timedelta(minutes=6))})
        result = read_history(self.table, params, AT + timedelta(minutes=7))
        self.assertEqual(len(result['revisions']), 1)
        before = result['revisions']
        self.assertEqual(sync.once(), 0)
        self.assertEqual(read_history(self.table, params, AT + timedelta(minutes=7))['revisions'], before)

    def test_history_offline_does_not_block_commits_and_destination_change_resends(self):
        run, sync, _ = self.fixture()
        self.table.failed = True
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT + timedelta(minutes=5))
            session = self.step(store, session, .6, AT + timedelta(minutes=5, seconds=1))
            with self.assertRaises(ConnectionError):
                sync.once()
            session = self.step(store, session, .7, AT + timedelta(minutes=6))
            self.assertEqual(session.state.step, 8)
        self.table.failed = False
        self.assertEqual(sync.once(), 4)
        other = HistoryTable(); other.url += '-other'
        self.assertEqual(HistorySync(self.database, SimpleNamespace(client=other, partition='resource-simulator')).once(), 10)
