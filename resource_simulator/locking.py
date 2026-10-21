"""OS-held lock; process termination releases it without deleting a lock file."""

from pathlib import Path
import os


class AlreadyRunning(RuntimeError):
    pass


class ProcessLock:
    def __init__(self, database: Path):
        self.path = Path(str(database.resolve()) + ".lock")
        self.stream = None

    def __enter__(self) -> "ProcessLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            raise AlreadyRunning("another Worker holds this database lock") from exc
        self.stream = stream
        return self

    def __exit__(self, *args) -> None:
        if self.stream is not None:
            # close releases the OS lock, also after exceptions. Never unlink it:
            # deleting/recreating a lock file would allow two different lock objects.
            self.stream.close()
            self.stream = None
