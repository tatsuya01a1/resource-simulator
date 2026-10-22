from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import handler
from azure.core.exceptions import ResourceNotFoundError


class HandlerTests(unittest.TestCase):
    def test_success_is_public_and_not_cached(self):
        payload = {'schema_version': 1, 'run_id': '00000000-0000-0000-0000-000000000001',
                   'run_generation': 1, 'step': 1, 'resource': 100, 'parameter': 0,
                   'requested_delta': 0, 'actual_delta': 0,
                   'calculated_at': datetime.now(timezone.utc).isoformat(),
                   'model': dict(initial_resource=100, initial_parameter=0, parameter_min=-1,
                                 parameter_max=1, coefficient=10, interval_seconds=1),
                   'private_field': 'do not expose'}
        with patch.dict(handler.os.environ, {'TABLE_ENDPOINT': 'https://unit.table.core.windows.net',
                                             'TABLE_READ_SAS': 'unit-placeholder'}), patch.object(handler, 'TableClient') as client:
            client.return_value.__enter__.return_value.get_entity.return_value = {
                'payload': json.dumps(payload), 'published_at': payload['calculated_at']}
            response = handler.current(None)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        body = json.loads(response.get_body())
        self.assertNotIn('private_field', body)
        self.assertFalse(body['history_available'])

    def test_missing_entity_is_distinct_from_failure(self):
        with patch.dict(handler.os.environ, {'TABLE_ENDPOINT': 'https://unit.table.core.windows.net',
                                             'TABLE_READ_SAS': 'unit-placeholder'}), patch.object(handler, 'TableClient') as client:
            client.return_value.__enter__.return_value.get_entity.side_effect = ResourceNotFoundError()
            response = handler.current(None)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(json.loads(response.get_body())['error'], 'not_initialized')

    def test_configuration_failure_does_not_expose_details(self):
        with patch.dict(handler.os.environ, {}, clear=True):
            response = handler.current(None)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(json.loads(response.get_body()), {'error': 'temporarily_unavailable'})

class CompatibilityHandlerTests(unittest.TestCase):
    def test_v2_nested_whitelist_and_shared_snapshot(self):
        payload = {'schema_version': 2, 'run_id': '00000000-0000-0000-0000-000000000001',
                   'run_generation': 2, 'step': 10,
                   'calculated_at': '2026-10-22T13:00:00+00:00',
                   'model': dict(initial_resource=100, initial_parameter=0, parameter_min=-1,
                                 parameter_max=1, coefficient=10, interval_seconds=1, secret='hidden'),
                   'resources': [
                       dict(id='resource', available=True, resource=102, parameter=.2,
                            requested_delta=2, actual_delta=2, start_step=0),
                       dict(id='beta', available=True, resource=110, requested_delta=10, actual_delta=10,
                            start_step=9, secret='hidden', schedule=dict(target_date='2026-10-22',
                            morning_value=None, evening_value=None, night_multiplier=None,
                            night_start=None, valid_until=None, mode='night', multiplier=5,
                            acquisition='missing', secret='hidden'))]}
        with patch.dict(handler.os.environ, {'TABLE_ENDPOINT': 'https://unit.table.core.windows.net',
                                             'TABLE_READ_SAS': 'unit-placeholder'}), patch.object(handler, 'TableClient') as client:
            client.return_value.__enter__.return_value.get_entity.return_value = {
                'payload': json.dumps(payload), 'published_at': payload['calculated_at']}
            response = handler.current(None)
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.get_body())
        self.assertEqual(body['schema_version'], 2)
        self.assertEqual(body['step'], 10)
        self.assertEqual(body['resources'][1]['resource'], 110)
        self.assertNotIn('secret', json.dumps(body))
        self.assertEqual(body['resource'], body['resources'][0]['resource'])

    def test_v1_upgrades_to_v2_without_fabricating_beta_value(self):
        from current_contract import upgrade_public
        old = dict(schema_version=1, run_id='0'*36, run_generation=1, step=1, resource=99,
                   parameter=-.1, requested_delta=-1, actual_delta=-1,
                   calculated_at=datetime.now(timezone.utc).isoformat(),
                   model=dict(initial_resource=100, initial_parameter=0, parameter_min=-1,
                              parameter_max=1, coefficient=10, interval_seconds=1))
        current = upgrade_public(old)
        self.assertEqual(current['resources'][1], dict(id='beta', name='Beta', available=False))

    def test_incomplete_v2_pair_is_rejected(self):
        from current_contract import public_state
        value = dict(schema_version=2, run_id='0'*36, run_generation=1, step=1,
                     calculated_at=datetime.now(timezone.utc).isoformat(),
                     model=dict(initial_resource=100, initial_parameter=0, parameter_min=-1,
                                parameter_max=1, coefficient=10, interval_seconds=1), resources=[])
        with self.assertRaises(ValueError):
            public_state(value)

class ScheduleContractTests(unittest.TestCase):
    def test_invalid_baseline_pair_or_expired_night_is_rejected(self):
        from current_contract import public_schedule
        schedule = dict(target_date='2026-10-22', morning_value=100, evening_value=130,
                        night_multiplier=30, night_start='2026-10-22T09:00:00+00:00',
                        valid_until='2026-10-22T19:00:00+00:00', mode='night',
                        multiplier=30, acquisition='acquired')
        self.assertEqual(public_schedule(schedule, '2026-10-22T15:00:00+00:00'), schedule)
        for change in (dict(multiplier=0), dict(morning_value=None), dict(target_date='2026-10-21'),
                       dict(valid_until='2026-10-23T19:00:00+00:00')):
            with self.subTest(change=change), self.assertRaises(ValueError):
                public_schedule({**schedule, **change}, '2026-10-22T15:00:00+00:00')
        with self.assertRaises(ValueError):
            public_schedule(schedule, '2026-10-22T19:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
