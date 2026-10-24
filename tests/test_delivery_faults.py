from contextlib import closing
from datetime import timedelta
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from resource_simulator.history_sync import HistorySync
from resource_simulator.config import ModelConfig
from resource_simulator.persistence import Store
from tests.test_history import HistoryTable
from tests.test_stage1 import AT, WorkspaceTest


class DeliveryFaultTests(WorkspaceTest):
    def fixture(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            session = self.step(store, session, .6, AT + timedelta(seconds=1))
            session = self.step(store, session, .7, AT + timedelta(minutes=1))
        self.table = HistoryTable()
        self.publisher = SimpleNamespace(client=self.table, partition='resource-simulator')
        self.sync = HistorySync(self.database, self.publisher)
        self.sidecar = Path(str(self.database) + '.history-delivery.sqlite3')

    def test_cloud_success_before_ack_failure_replays(self):
        self.fixture()
        with patch.object(self.sync, '_before_receipts', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.sync.once()
        with closing(sqlite3.connect(self.sidecar)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM receipts_v2').fetchone()[0], 0)
        self.assertEqual(self.sync.once(), 2)
        self.assertEqual(self.sync.once(), 0)
        self.assertEqual(sum('payload' in e for e in self.table.entities.values()), 2)

    def test_ack_lock_rejects_and_recovers_without_deleting_source(self):
        self.fixture()
        self.sync.once()
        with closing(sqlite3.connect(self.sidecar)) as lock:
            lock.execute('DELETE FROM receipts_v2'); lock.commit()
            lock.execute('BEGIN EXCLUSIVE')
            with self.assertRaises(sqlite3.OperationalError):
                self.sync.once()
            lock.rollback()
        self.assertEqual(self.sync.once(), 2)
        with Store(self.database) as store:
            store.validate_all()
            self.assertEqual(store.connection.execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 2)

    def test_corrupt_missing_and_unusable_delivery_database(self):
        self.fixture()
        self.sync.once()
        self.sidecar.write_bytes(b'not a SQLite database')
        with self.assertRaises(sqlite3.DatabaseError):
            self.sync.once()
        self.sidecar.rename(str(self.sidecar) + '.preserved-corrupt')
        self.assertEqual(self.sync.once(), 2)
        self.sidecar.unlink(); self.sidecar.mkdir()
        with self.assertRaises(sqlite3.OperationalError):
            self.sync.once()
        self.sidecar.rmdir()
        self.assertEqual(self.sync.once(), 2)

    def test_sidecar_from_another_path_is_not_trusted(self):
        self.fixture()
        self.sync.once()
        other = self.database.with_name('other.sqlite3')
        with closing(sqlite3.connect(self.database)) as source, closing(sqlite3.connect(other)) as target:
            source.backup(target)
        shutil.copyfile(self.sidecar, str(other) + '.history-delivery.sqlite3')
        self.assertEqual(HistorySync(other, self.publisher).once(), 2)

    def test_restore_same_path_changed_payload_does_not_reuse_old_ack(self):
        self.fixture()
        self.sync.once()
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute('SELECT rowid,candle_json FROM candles LIMIT 1').fetchone()
            value = json.loads(row[1]); value['high'] += 100
            db.execute('UPDATE candles SET candle_json=? WHERE rowid=?', (json.dumps(value), row[0])); db.commit()
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.sync.once()
        # Existing immutable cloud value remains unchanged; main source never pruned.
        self.assertEqual(sum('payload' in e for e in self.table.entities.values()), 2)
