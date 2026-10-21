"""Child-process fixture: killed by the parent during a real SQLite transaction."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import time

from resource_simulator.config import ModelConfig
from resource_simulator.engine import advance
from resource_simulator.locking import ProcessLock
from resource_simulator.persistence import Store


def main():
    database = Path(sys.argv[1])
    at = datetime(2026, 10, 21, 12, 0, 57, tzinfo=timezone.utc)
    with ProcessLock(database), Store(database) as store:
        session = store.resume(ModelConfig(), at)
        for sample, seconds in ((0.25, 1), (0.9, 2)):
            session = store.commit_step(session, advance(session.state, session.config, sample,
                                                        at + timedelta(seconds=seconds)))

        def pause():
            print("transaction-uncommitted", flush=True)
            time.sleep(30)
            return 0

        store.connection.create_function("pause", 0, pause)
        store.connection.execute("CREATE TEMP TRIGGER pause_checkpoint AFTER UPDATE ON checkpoints "
                                 "BEGIN SELECT pause(); END")
        # At this point the previous candle, outbox, new open candle, and checkpoint
        # have all been written, but COMMIT has not run.
        store.commit_step(session, advance(session.state, session.config, 0,
                                           at + timedelta(seconds=3)))


if __name__ == "__main__":
    main()
