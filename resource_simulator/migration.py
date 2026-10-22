"""Explicit, offline v1 -> v3 migration. Version 2's ledger is not implemented."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from uuid import uuid4

from .config import ModelConfig, ValidationError
from .persistence import Store, SCHEMA_VERSION

LEGACY_TABLES = ("simulation_runs", "active_run", "checkpoints", "open_candles", "candles", "outbox")


def fingerprint(connection):
    """Include rowids (run generations), original JSON bytes and outbox retry data."""
    rows = {table: [tuple(row) for row in connection.execute(f'SELECT rowid,* FROM {table} ORDER BY rowid')]
            for table in LEGACY_TABLES}
    return {"counts": {key: len(value) for key, value in rows.items()},
            "sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, allow_nan=False).encode()).hexdigest()}


def _checker(connection):
    checker = Store.__new__(Store)
    checker.connection = connection
    return checker


def migrate(path: Path, config: ModelConfig, *, checkpoint=lambda phase: None) -> dict:
    """Caller must hold the same ProcessLock used by run/new-run. Never initializes a DB."""
    path = path.resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)
    connection.row_factory = sqlite3.Row
    backup = path.with_name(path.name + f".v1-backup-{uuid4().hex}.sqlite3")
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            _checker(connection).validate_all()
            return {"schema_version": SCHEMA_VERSION, "already_migrated": True}
        if version != 1:
            raise ValidationError("migration supports only schema 1")
        checker = _checker(connection)
        checker.validate_all(legacy=True)
        before = fingerprint(connection)
        # Exclusive creation avoids replacing any previous backup, even after an interrupted run.
        with backup.open("xb"):
            pass
        with closing(sqlite3.connect(backup)) as target:
            connection.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
        with closing(sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)) as verify:
            verify.row_factory = sqlite3.Row
            _checker(verify).validate_all(legacy=True)
            if verify.execute("PRAGMA user_version").fetchone()[0] != 1 or fingerprint(verify) != before:
                raise ValidationError("backup verification failed")
        with backup.open("rb+") as stream:
            os.fsync(stream.fileno())
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest = {"source_schema": 1, "target_schema": SCHEMA_VERSION,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "backup": backup.name, "backup_sha256": digest, "logical": before,
                    "verified": True}
        manifest_path = backup.with_suffix(backup.suffix + ".manifest.json")
        with manifest_path.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        checkpoint("backup_verified")
        connection.execute("PRAGMA synchronous=FULL")
        with checker.transaction():
            if fingerprint(connection) != before:
                raise ValidationError("source changed after backup")
            checker._create_beta_tables()
            checkpoint("tables_created")
            active = connection.execute("SELECT run_id FROM active_run WHERE singleton=1").fetchone()
            for row in connection.execute("SELECT run_id FROM simulation_runs").fetchall():
                session = checker._read_session(row[0], legacy=True)
                connection.execute("INSERT INTO resource_starts VALUES (?, 'resource', 0, ?)",
                                   (row[0], session.config.initial_resource))
                if active and row[0] == active[0]:
                    checker._attach_beta(row[0], session.state, config.initial_beta)
            checkpoint("beta_added")
            if fingerprint(connection) != before:
                raise ValidationError("legacy data changed during migration")
            checker.validate_all()
            checkpoint("validated")
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            checkpoint("version_set")
        # Validate on a separate connection after COMMIT; never roll back by replacing the source.
        with Store(path):
            pass
        return {"schema_version": SCHEMA_VERSION, "backup": str(backup),
                "manifest": str(manifest_path), "preserved": before}
    finally:
        connection.close()
