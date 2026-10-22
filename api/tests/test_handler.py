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


if __name__ == '__main__':
    unittest.main()
