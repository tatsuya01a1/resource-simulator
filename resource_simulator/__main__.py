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
from threading import Event, Thread

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
    for name in ("run", "new-run", "status", "migrate"):
        command = commands.add_parser(name)
        command.add_argument("--db", type=Path, default=default_database())
        if name != "status":
            command.add_argument("--config", type=Path, default=Path("config/simulator.toml"))
        if name == "run":
            command.add_argument("--steps", type=positive_int)
            command.add_argument("--use-saved-rules", action="store_true")
            command.add_argument("--cloud-config", type=Path,
                                 help="Enable certificate-authenticated latest-state sync")
            command.add_argument("--stop-file", type=Path, help="Local file requests graceful shutdown")
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
            if args.command == "migrate":
                from .migration import migrate
                print(json.dumps(migrate(args.db, config), indent=2))
                return 0
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
                                  "config_version": store.connection.execute(
                                      "SELECT config_version FROM simulation_runs WHERE run_id=?",
                                      (session.run_id,)).fetchone()[0]}), flush=True)
                logging.info("Worker initialized run=%s step=%s", session.run_id, session.state.step)
                if args.command == "run":
                    stop = Event()
                    if args.stop_file:
                        def watch_stop():
                            while not stop.wait(.25):
                                if args.stop_file.exists():
                                    stop.set()
                        Thread(target=watch_stop, daemon=True, name="local-stop-file").start()
                    for signum in (signal.SIGINT, signal.SIGTERM):
                        previous_handlers[signum] = signal.signal(signum, lambda *_: stop.set())
                    sync = None
                    if args.cloud_config:
                        from .cloud import LatestSync, configured_publisher
                        # Credential/network failures remain in the IO thread.
                        try:
                            sync = LatestSync(args.db, configured_publisher(args.cloud_config))
                            sync.start()
                        except Exception as exc:
                            logging.warning("latest_sync_configuration_failed type=%s", type(exc).__name__)
                    try:
                        run(store, session, stop, steps=args.steps)
                    finally:
                        stop.set()
                        if sync:
                            sync.close()
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
