from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import handler


class HistoryHandlerTests(unittest.TestCase):
    def request(self, **changes):
        now = datetime.now(timezone.utc)
        params = dict(run_id='00000000-0000-0000-0000-000000000001',
                                          resource_id='beta', **{'from': (now - timedelta(minutes=2)).isoformat(),
                                                                 'to': (now - timedelta(minutes=1)).isoformat()})
        params.update(changes)
        return SimpleNamespace(params=params)

    def test_empty_is_success_and_headers_are_not_cached(self):
        with patch.dict(handler.os.environ, {'TABLE_ENDPOINT': 'https://unit.table.core.windows.net', 'HISTORY_READ_SAS': 'unit'}), patch.object(handler, 'TableClient') as client:
            client.return_value.__enter__.return_value.query_entities.return_value = []
            response = handler.ohlc(self.request())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.get_body())['candles'], [])
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_configuration_or_network_failure_is_503(self):
        with patch.dict(handler.os.environ, {}, clear=True):
            self.assertEqual(handler.ohlc(self.request()).status_code, 503)
        with patch.dict(handler.os.environ, {'TABLE_ENDPOINT': 'unit', 'HISTORY_READ_SAS': 'unit'}), patch.object(handler, 'TableClient', side_effect=ConnectionError('secret')):
            response = handler.ohlc(self.request())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('secret', response.get_body().decode())

    def test_bad_params_outside_window_and_corrupt_cloud_are_distinct(self):
        with patch.dict(handler.os.environ, {'TABLE_ENDPOINT': 'unit', 'HISTORY_READ_SAS': 'unit'}), patch.object(handler, 'TableClient') as client:
            instance = client.return_value.__enter__.return_value
            self.assertEqual(handler.ohlc(SimpleNamespace(params={})).status_code, 400)
            self.assertEqual(handler.ohlc(self.request(resource_id='invalid')).status_code, 400)
            old = self.request(); old.params['from'] = '2020-01-01T00:00:00Z'; old.params['to'] = '2020-01-02T00:00:00Z'
            self.assertEqual(handler.ohlc(old).status_code, 416)
            instance.query_entities.return_value = [dict(payload='{}')]
            self.assertEqual(handler.ohlc(self.request()).status_code, 503)
