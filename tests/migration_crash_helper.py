from pathlib import Path
import sys
import time
from resource_simulator.config import ModelConfig
from resource_simulator.locking import ProcessLock
from resource_simulator.migration import migrate


def pause(phase):
    if phase == 'version_set':
        print('migration-uncommitted', flush=True)
        time.sleep(30)


if __name__ == '__main__':
    with ProcessLock(Path(sys.argv[1])):
        migrate(Path(sys.argv[1]), ModelConfig(), checkpoint=pause)
