"""Disposable SQLite -> publisher -> Table double -> real read service fixture for Web QA."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from resource_simulator.cloud import CurrentPublisher, committed_snapshot
from resource_simulator.config import ModelConfig
from resource_simulator.engine import advance
from resource_simulator.persistence import Store
from api.current_service import read_current
from tests.test_cloud import MemoryTable


def main():
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / 'fixture.sqlite3'
        now = datetime.now(timezone.utc)
        with Store(database) as store:
            session = store.resume(ModelConfig(), now - timedelta(seconds=2))
            for index in (1, 2):
                session = store.commit_step(session, advance(session.state, session.config, .6,
                                            now - timedelta(seconds=2-index)))
        table = MemoryTable()
        CurrentPublisher(table, now=lambda: now).publish(committed_snapshot(database))
        result = read_current(table, now=lambda: now)
        result['build_commit'] = 'local-test'
        print(json.dumps(result))


if __name__ == '__main__':
    main()
