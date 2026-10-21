"""PowerShell: uv run --locked python -m resource_simulator run."""

import argparse
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import sqlite3
import sys
from threading import Event

from .config import load_config
from .locking import AlreadyRunning, ProcessLock
from .persistence import Store, status
from .worker import run


def default_database() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
    return root / "resource-simulator" / "worker.sqlite3"


def positive_int(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local resource simulation Worker")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "new-run", "status"):
        command = commands.add_parser(name)
        command.add_argument("--db", type=Path, default=default_database())
        if name != "status":
            command.add_argument("--config", type=Path, default=Path("config/simulator.toml"))
        if name == "run":
            command.add_argument("--steps", type=positive_int)
            command.add_argument("--use-saved-rules", action="store_true")
    args = parser.parse_args(argv)
    handler = None
    previous_handlers = {}
    try:
        if args.command == "status":
            print(json.dumps(status(args.db), indent=2, allow_nan=False))
            return 0
        # Validate before creating or changing the recovery database.
        config = load_config(args.config)
        with ProcessLock(args.db):
            handler = RotatingFileHandler(args.db.resolve().parent / "worker.log",
                                          maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger = logging.getLogger()
            logger.setLevel(logging.INFO)
            logger.addHandler(handler)
            with Store(args.db) as store:
                session = store.resume(config, datetime.now(timezone.utc),
                                       new_run=args.command == "new-run",
                                       use_saved_rules=getattr(args, "use_saved_rules", False))
                print(json.dumps({"run_id": session.run_id, "step": session.state.step,
                                  "config_version": session.config.config_hash}), flush=True)
                logging.info("Worker initialized run=%s step=%s", session.run_id, session.state.step)
                if args.command == "run":
                    stop = Event()
                    for signum in (signal.SIGINT, signal.SIGTERM):
                        previous_handlers[signum] = signal.signal(signum, lambda *_: stop.set())
                    run(store, session, stop, steps=args.steps)
        return 0
    except (OSError, sqlite3.Error, ValueError, AlreadyRunning, KeyError, TypeError) as exc:
        if handler:
            logging.error("Worker stopped: %s", exc)
        print(f"Worker stopped: {exc}", file=sys.stderr)
        return 1
    finally:
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)
        if handler:
            logging.getLogger().removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
