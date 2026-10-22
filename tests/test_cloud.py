from copy import deepcopy
from datetime import timedelta
import json
from threading import Event
import unittest

from azure.core.exceptions import HttpResponseError, ResourceNotFoundError

from api.current_service import read_current
from resource_simulator.cloud import CurrentPublisher, LatestSync, committed_snapshot
from resource_simulator.config import ModelConfig
from resource_simulator.persistence import Store
from resource_simulator.worker import run
from tests.test_stage1 import AT, WorkspaceTest


class Entity(dict):
    def __init__(self, value, etag):
        super().__init__(value)
        self.metadata = {"etag": str(etag)}


class MemoryTable:
    def __init__(self):
        self.entity = None
        self.version = 0
        self.failed = False
        self.before_update = None

    def get_entity(self, *_):
        if self.failed:
            raise ConnectionError("offline")
        if self.entity is None:
            raise ResourceNotFoundError()
        return Entity(deepcopy(self.entity), self.version)

    def create_entity(self, value):
        if self.entity is not None:
            error = HttpResponseError()
            error.status_code = 409
            raise error
        self.entity = deepcopy(value)
        self.version += 1

    def update_entity(self, value, *, etag, **kwargs):
        if self.before_update:
            callback, self.before_update = self.before_update, None
            callback()
        if str(self.version) != etag:
            error = HttpResponseError()
            error.status_code = 412
            raise error
        self.entity = deepcopy(value)
        self.version += 1


class CloudFixture(WorkspaceTest):
    def setUp(self):
        super().setUp()
        self.table = MemoryTable()
        self.publisher = CurrentPublisher(self.table, now=lambda: AT)

    def snapshot(self, step=0):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            for _ in range(step):
                session = self.step(store, session, .75, AT)
        return committed_snapshot(self.database)



class CloudTests(CloudFixture):
    def test_reads_only_committed_snapshot_and_keeps_outbox(self):
        self.snapshot()
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            session = self.step(store, session, .5, AT)
            session = self.step(store, session, .5, AT + timedelta(minutes=1))
            with store.transaction():
                store.connection.execute("UPDATE checkpoints SET state_json=json_set(state_json,'$.step',99)")
                self.assertEqual(committed_snapshot(self.database)["step"], 2)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 2)

    def test_failure_does_not_stop_calculation_and_recovery_publishes_latest(self):
        self.snapshot()
        sync = LatestSync(self.database, self.publisher)
        self.table.failed = True
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            def wait(_):
                with self.assertRaises(ConnectionError):
                    sync.once()
                return False
            result = run(store, session, Event(), steps=4, sample=lambda: .75,
                         now=lambda: AT, monotonic=lambda: 0, wait=wait)
            self.assertEqual(result.state.step, 4)
        self.table.failed = False
        sync.once()
        self.assertEqual(json.loads(self.table.entity['payload'])['step'], 4)
        self.assertFalse(hasattr(sync, 'queue'))

    def test_delayed_step_cannot_overwrite_newer_step(self):
        snapshot = self.snapshot(1)
        self.publisher.publish(snapshot)
        second = {**snapshot, 'step': 2}
        third = {**snapshot, 'step': 3}
        self.table.before_update = lambda: self.publisher.publish(third)
        self.assertFalse(self.publisher.publish(second))
        self.assertEqual(json.loads(self.table.entity['payload'])['step'], 3)

    def test_delayed_old_run_cannot_replace_new_run(self):
        first = self.snapshot(1)
        self.publisher.publish(first)
        with Store(self.database) as store:
            store.resume(ModelConfig(), AT, new_run=True)
        second = committed_snapshot(self.database)
        self.table.before_update = lambda: self.publisher.publish(second)
        self.assertFalse(self.publisher.publish({**first, 'step': 500}))
        self.assertEqual(json.loads(self.table.entity['payload'])['run_id'], second['run_id'])

    def test_equal_generation_different_uuid_is_rejected(self):
        first = self.snapshot()
        self.publisher.publish(first)
        self.assertFalse(self.publisher.publish({**first, 'run_id': 'x' * 36, 'step': 50}))

    def test_same_step_does_not_refresh_publication_time(self):
        snapshot = self.snapshot(1)
        self.publisher.publish(snapshot)
        self.assertFalse(self.publisher.publish(snapshot))
        response = read_current(self.table, now=lambda: AT + timedelta(seconds=12))
        self.assertTrue(response['stale'])
        self.assertEqual(response['step'], 1)
        self.assertFalse(response['history_available'])

    def test_api_whitelists_and_uses_table_commit_time(self):
        snapshot = self.snapshot()
        self.publisher.publish(snapshot)
        payload = json.loads(self.table.entity['payload'])
        payload['secret'] = 'must never be public'
        self.table.entity['payload'] = json.dumps(payload)
        response = read_current(self.table, now=lambda: AT)
        self.assertNotIn('secret', response)
        self.assertNotIn('PartitionKey', response)
        self.assertEqual(response['simulation_elapsed_seconds'], 0)

class TwoResourceCloudTests(CloudFixture):
    def old_payload(self, snapshot):
        result = {key: value for key, value in snapshot.items() if key != 'resources'}
        result['schema_version'] = 1
        return result

    def test_old_table_payload_is_upgraded_with_beta_unavailable(self):
        old = self.old_payload(self.snapshot(1))
        self.table.create_entity({'payload': json.dumps(old), 'published_at': old['calculated_at']})
        response = read_current(self.table, now=lambda: AT)
        self.assertEqual(response['schema_version'], 2)
        self.assertEqual(response['resources'][0]['name'], 'Alpha')
        self.assertEqual(response['resources'][0]['resource'], old['resource'])
        self.assertEqual(response['resources'][1], {'id': 'beta', 'name': 'Beta', 'available': False})

    def test_new_publisher_replaces_old_then_refuses_legacy_downgrade(self):
        snapshot = self.snapshot(1)
        old = self.old_payload({**snapshot, 'step': 0})
        self.publisher.publish(old)
        self.assertTrue(self.publisher.publish(snapshot))
        self.assertFalse(self.publisher.publish({**old, 'step': 99}))
        response = read_current(self.table, now=lambda: AT)
        self.assertTrue(response['resources'][1]['available'])
        self.assertEqual(response['step'], 1)

    def test_publication_contains_one_pair_without_private_nested_fields(self):
        snapshot = self.snapshot(1)
        snapshot['resources'][1]['private'] = 'secret'
        snapshot['resources'][1]['schedule']['secret'] = 'secret'
        snapshot['model']['certificate_path'] = 'private'
        self.publisher.publish(snapshot)
        response = read_current(self.table, now=lambda: AT)
        self.assertNotIn('secret', json.dumps(response))
        self.assertNotIn('private', json.dumps(response))
        self.assertEqual([r['id'] for r in response['resources']], ['resource', 'beta'])
        self.assertEqual(response['resources'][1]['requested_delta'],
                         response['resources'][1]['schedule']['multiplier'] * response['actual_delta'])

    def test_precommit_and_rollback_never_publish_half_pair(self):
        self.snapshot(1)
        with Store(self.database) as store:
            before = committed_snapshot(self.database)
            try:
                with store.transaction():
                    store.connection.execute("UPDATE beta_checkpoints SET state_json=json_set(state_json,'$.resource',999)")
                    self.assertEqual(committed_snapshot(self.database), before)
                    raise OSError('fail before Alpha')
            except OSError:
                pass
            self.assertEqual(committed_snapshot(self.database), before)
            # Corrupt committed mismatch is rejected, never assembled into a public snapshot.
            store.connection.execute("UPDATE beta_checkpoints SET state_json=json_set(state_json,'$.step',999)")
            with self.assertRaises(ValueError):
                committed_snapshot(self.database)


if __name__ == '__main__':
    unittest.main()
