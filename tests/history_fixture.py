"""Browser fixture from actual temporary SQLite OHLC -> sync -> read API service."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

from api.current_service import read_current
from api.history_service import read_history
from resource_simulator.cloud import committed_snapshot, CurrentPublisher
from resource_simulator.config import ModelConfig
from resource_simulator.engine import advance
from resource_simulator.history_sync import HistorySync
from resource_simulator.persistence import Store
from tests.test_cloud import MemoryTable
from tests.test_history import HistoryTable


def main():
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / 'fixture.sqlite3'
        now = datetime(2026, 10, 23, 3, 0, tzinfo=timezone.utc)
        start = now - timedelta(hours=3)
        with Store(database) as store:
            session = store.resume(ModelConfig(), start)
            for minute in range(180):
                if 74 <= minute < 90:  # actual absent observations, not fabricated flat candles
                    continue
                for second in (1, 20, 40):
                    at = start + timedelta(minutes=minute, seconds=second)
                    sample = .75 if minute % 30 < 14 else .30
                    session = store.commit_step(session, advance(session.state, session.config, sample, at))
        current_table = MemoryTable()
        CurrentPublisher(current_table, now=lambda: now).publish(committed_snapshot(database))
        current = read_current(current_table, now=lambda: now)
        current['build_commit'] = 'local-history-test'
        table = HistoryTable()
        sync = HistorySync(database, SimpleNamespace(client=table, partition='resource-simulator'))
        while sync.once():
            pass
        history = {}
        for resource in ('resource', 'beta'):
            history[resource] = read_history(table, dict(run_id=session.run_id, resource_id=resource,
                                                        **{'from': (start - timedelta(hours=1)).isoformat(), 'to': now.isoformat()}), now)
        print(json.dumps(dict(current=current, history=history)))


if __name__ == '__main__':
    main()
