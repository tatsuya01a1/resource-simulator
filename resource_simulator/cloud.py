"""Publish only the latest committed checkpoint on an independent IO thread."""

from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import random
import sqlite3
from threading import Event, Thread
import tomllib
from urllib.parse import urlparse

from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
from azure.data.tables import TableClient, UpdateMode
from azure.identity import CertificateCredential

from api.current_contract import public_state, should_replace
from .engine import stamp
from .persistence import SCHEMA_VERSION, decode_state


def committed_snapshot(database):
    connection = sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True,
                                 isolation_level=None, timeout=1)
    try:
        connection.execute("BEGIN")
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("unsupported local schema")
        row = connection.execute("SELECT r.rowid,r.run_id,r.config_json,c.state_json "
                                 "FROM active_run a JOIN simulation_runs r USING(run_id) "
                                 "JOIN checkpoints c USING(run_id) WHERE a.singleton=1").fetchone()
        if row is None:
            return None
        state = decode_state(row[3])
        return public_state({"schema_version": 1, "run_generation": row[0], "run_id": row[1],
                             **asdict(state), "calculated_at": stamp(state.calculated_at),
                             "model": json.loads(row[2])})
    finally:
        connection.close()


class CurrentPublisher:
    def __init__(self, client, partition="resource-simulator", now=lambda: datetime.now(timezone.utc)):
        self.client, self.partition, self.now = client, partition, now

    def publish(self, snapshot):
        snapshot = public_state(snapshot)
        for _ in range(3):
            try:
                current = self.client.get_entity(self.partition, "current")
            except ResourceNotFoundError:
                current = None
            if current is not None:
                previous = public_state(json.loads(current["payload"]))
                if not should_replace(snapshot, previous):
                    return False
            entity = {"PartitionKey": self.partition, "RowKey": "current",
                      "payload": json.dumps(snapshot, separators=(",", ":"), allow_nan=False),
                      "published_at": stamp(self.now())}
            if len(entity["payload"].encode("utf-16-le")) > 48 * 1024:
                raise ValueError("public payload exceeds design limit")
            try:
                if current is None:
                    self.client.create_entity(entity)
                else:
                    self.client.update_entity(entity, mode=UpdateMode.REPLACE,
                                              etag=current.metadata["etag"],
                                              match_condition=MatchConditions.IfNotModified)
                return True
            except HttpResponseError as exc:
                if exc.status_code not in (409, 412):
                    raise
        raise RuntimeError("concurrent publication; retry latest snapshot")


class LatestSync:
    def __init__(self, database, publisher):
        self.database, self.publisher = database, publisher
        self.stop = Event()
        self.thread = Thread(target=self._run, name="latest-state-sync", daemon=True)

    def once(self):
        snapshot = committed_snapshot(self.database)
        if snapshot is not None:
            return self.publisher.publish(snapshot)
        return False

    def _run(self):
        failures = 0
        while not self.stop.is_set():
            try:
                self.once()
                if failures:
                    logging.info("latest_sync_recovered")
                failures = 0
                delay = 1
            except Exception as exc:
                # Exception text may contain URLs or credential details. Never log it.
                if failures == 0:
                    logging.warning("latest_sync_unavailable type=%s", type(exc).__name__)
                failures += 1
                delay = min(30, 2 ** min(failures - 1, 5)) + random.random() * 0.25
            self.stop.wait(delay)
        try:
            self.once()  # Best-effort final committed checkpoint on normal stop.
        except Exception:
            pass

    def start(self):
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=12)


def configured_publisher(path):
    with Path(path).open("rb") as stream:
        config = tomllib.load(stream)["cloud"]
    endpoint = urlparse(config["endpoint"])
    if endpoint.scheme != "https" or not endpoint.hostname.endswith(".table.core.windows.net") or endpoint.query:
        raise ValueError("invalid Table endpoint")
    certificate = Path(config["certificate_path"])
    if not certificate.is_absolute():
        certificate = Path(path).resolve().parent / certificate
    credential = CertificateCredential(config["tenant_id"], config["client_id"], str(certificate),
                                       connection_timeout=3, read_timeout=5, retry_total=0)
    client = TableClient(config["endpoint"], config["table"], credential=credential,
                         connection_timeout=3, read_timeout=5, retry_total=0, logging_enable=False)
    return CurrentPublisher(client, config.get("partition", "resource-simulator"))
