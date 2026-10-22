"""Live ETag races on an isolated entity, plus stopped PC->Storage->API comparison."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import requests
from resource_simulator.cloud import CurrentPublisher, committed_snapshot, configured_publisher


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--compare-only', action='store_true')
    args = parser.parse_args()
    publisher = configured_publisher(ROOT / '.secrets/cloud.toml')
    snapshot = committed_snapshot(ROOT / '.secrets/worker.sqlite3')
    if args.compare_only:
        remote = json.loads(publisher.client.get_entity('resource-simulator', 'current')['payload'])
        manifest = json.loads((ROOT / '.secrets/deployment.json').read_text())
        api = requests.get(manifest['url'] + '/api/current', timeout=20).json()
        keys = ['run_id', 'run_generation', 'step', 'resource', 'parameter', 'actual_delta', 'calculated_at']
        assert all(snapshot[k] == remote[k] == api[k] for k in keys)
        print(json.dumps({'pc_storage_api_equal': True, 'step': snapshot['step'], 'keys': keys}))
        return
    partition = 'verification-' + str(uuid4())
    client = publisher.client
    actual = CurrentPublisher(client, partition)
    class RacingClient:
        def __init__(self, callback): self.callback = callback
        def get_entity(self, *args): return client.get_entity(*args)
        def update_entity(self, *args, **kwargs):
            if self.callback:
                callback, self.callback = self.callback, None
                callback()
            return client.update_entity(*args, **kwargs)
    try:
        first = deepcopy(snapshot)
        first['step'] = 1
        actual.publish(first)
        delayed = CurrentPublisher(RacingClient(lambda: actual.publish({**first, 'step': 3})), partition)
        assert delayed.publish({**first, 'step': 2}) is False
        second = {**first, 'run_generation': first['run_generation'] + 1, 'run_id': str(uuid4()), 'step': 0}
        delayed = CurrentPublisher(RacingClient(lambda: actual.publish(second)), partition)
        assert delayed.publish({**first, 'step': 100}) is False
        final = json.loads(client.get_entity(partition, 'current')['payload'])
        assert final['run_id'] == second['run_id']
        print(json.dumps({'live_etag_step_race': True, 'live_etag_run_race': True}))
    finally:
        client.delete_entity(partition, 'current')


if __name__ == '__main__':
    main()
