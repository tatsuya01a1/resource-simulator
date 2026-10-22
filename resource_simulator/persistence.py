"""SQLite is the recovery authority. A step and all its aggregation changes commit together."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from .aggregation import Candle, minute
from .beta import BetaState, Schedule, advance_beta, initial_beta
from .config import ModelConfig, ValidationError, finite
from .engine import State, initial, stamp, utc


SCHEMA_VERSION = 3  # Version 2 is reserved for the unimplemented delivery-ledger design.
RESOURCE_ID = "resource"


def encode(value: State | Candle | BetaState) -> str:
    return json.dumps({key: stamp(item) if isinstance(item, datetime) else item
                       for key, item in asdict(value).items()}, allow_nan=False, sort_keys=True)


def decode_state(raw: str) -> State:
    values = json.loads(raw)
    values["calculated_at"] = datetime.fromisoformat(values["calculated_at"])
    return State(**values)


def decode_beta(raw: str) -> BetaState:
    values = json.loads(raw)
    values["calculated_at"] = datetime.fromisoformat(values["calculated_at"])
    values["schedule"] = Schedule(**values["schedule"])
    return BetaState(**values)


def decode_candle(raw: str) -> Candle:
    values = json.loads(raw)
    for key in ("minute_start", "first_observed_at", "last_observed_at"):
        values[key] = datetime.fromisoformat(values[key])
    return Candle(**values)


@dataclass(frozen=True)
class Session:
    run_id: str
    config: ModelConfig
    state: State
    candle: Candle | None
    beta: BetaState | None = None
    beta_candle: Candle | None = None
    beta_start_step: int | None = None
    # Runtime continuity never survives restarting a Worker.
    continuous: bool = field(default=False, compare=False)


class Store:
    def __init__(self, path: Path):
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, isolation_level=None, timeout=5)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            tables = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version != SCHEMA_VERSION and (version != 0 or tables):
                raise ValidationError("schema 1 requires explicit migrate; other schemas are unsupported")
            if self.connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValidationError("SQLite integrity check failed")
            if self.connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValidationError("SQLite foreign key check failed")
            if self.connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise ValidationError("SQLite WAL could not be enabled")
            self.connection.execute("PRAGMA synchronous=FULL")
            if version == 0:
                self._create_schema()
            self.validate_all()
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *args) -> None:
        self.connection.close()

    @contextmanager
    def transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute("ROLLBACK")
            raise

    def _create_schema(self) -> None:
        statements = [
            """CREATE TABLE simulation_runs (
                run_id TEXT PRIMARY KEY, config_json TEXT NOT NULL,
                rules_hash TEXT NOT NULL, config_version TEXT NOT NULL, started_at TEXT NOT NULL)""",
            """CREATE TABLE active_run (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                run_id TEXT NOT NULL REFERENCES simulation_runs(run_id))""",
            """CREATE TABLE checkpoints (
                run_id TEXT PRIMARY KEY REFERENCES simulation_runs(run_id), state_json TEXT NOT NULL)""",
            """CREATE TABLE open_candles (
                run_id TEXT PRIMARY KEY REFERENCES simulation_runs(run_id), candle_json TEXT NOT NULL)""",
            """CREATE TABLE candles (
                run_id TEXT NOT NULL REFERENCES simulation_runs(run_id), resource_id TEXT NOT NULL,
                minute_start TEXT NOT NULL, candle_json TEXT NOT NULL,
                PRIMARY KEY(run_id, resource_id, minute_start))""",
            """CREATE TABLE outbox (
                run_id TEXT NOT NULL, resource_id TEXT NOT NULL, minute_start TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'ohlc' CHECK(kind='ohlc'), attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT, PRIMARY KEY(run_id, resource_id, minute_start),
                FOREIGN KEY(run_id, resource_id, minute_start)
                    REFERENCES candles(run_id, resource_id, minute_start))""",
        ]
        with self.transaction():
            for sql in statements:
                self.connection.execute(sql)
            self._create_beta_tables()
            self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def _create_beta_tables(self) -> None:
        self.connection.execute("""CREATE TABLE resource_starts (
            run_id TEXT NOT NULL REFERENCES simulation_runs(run_id), resource_id TEXT NOT NULL,
            start_step INTEGER NOT NULL CHECK(start_step>=0), initial_value REAL NOT NULL CHECK(initial_value>=0),
            PRIMARY KEY(run_id, resource_id))""")
        self.connection.execute("""CREATE TABLE beta_checkpoints (
            run_id TEXT PRIMARY KEY REFERENCES simulation_runs(run_id), state_json TEXT NOT NULL)""")
        self.connection.execute("""CREATE TABLE beta_open_candles (
            run_id TEXT PRIMARY KEY REFERENCES beta_checkpoints(run_id), candle_json TEXT NOT NULL)""")

    def _attach_beta(self, run_id: str, state: State, value: float) -> BetaState:
        beta = initial_beta(value, state)
        self.connection.execute("INSERT INTO resource_starts VALUES (?, 'beta', ?, ?)",
                                (run_id, state.step, value))
        self.connection.execute("INSERT INTO beta_checkpoints VALUES (?, ?)", (run_id, encode(beta)))
        return beta

    def _finalize(self, run_id: str, candle: Candle, resource_id=RESOURCE_ID) -> None:
        key = (run_id, resource_id, stamp(candle.minute_start))
        self.connection.execute("INSERT INTO candles VALUES (?, ?, ?, ?)", (*key, encode(candle)))
        self.connection.execute(
            "INSERT INTO outbox(run_id, resource_id, minute_start) VALUES (?, ?, ?)", key)
        table = "open_candles" if resource_id == RESOURCE_ID else "beta_open_candles"
        self.connection.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))

    def validate_all(self, *, legacy=False) -> None:
        if self.connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValidationError("SQLite integrity check failed")
        if self.connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValidationError("SQLite foreign key check failed")
        runs = self.connection.execute("SELECT run_id FROM simulation_runs").fetchall()
        active = self.connection.execute("SELECT run_id FROM active_run WHERE singleton=1").fetchone()
        if bool(runs) != bool(active):
            raise ValidationError("active run reference is missing")
        for row in runs:
            session = self._read_session(row[0], legacy=legacy)
            if not legacy and active and row[0] == active[0] and session.beta is None:
                raise ValidationError("active run is missing Beta")

    def _check_history(self, run_id, resource_id, state, candle, start_step):
        total, last = 0, None
        for row in self.connection.execute(
                "SELECT * FROM candles WHERE run_id=? AND resource_id=? ORDER BY minute_start",
                (run_id, resource_id)):
            closed = decode_candle(row["candle_json"])
            if row["minute_start"] != stamp(closed.minute_start):
                raise ValidationError("candle identity mismatch")
            if last and closed.minute_start <= last.minute_start:
                raise ValidationError("candle order mismatch")
            last = closed
            total += closed.sample_count
        if candle:
            if last and candle.minute_start <= last.minute_start:
                raise ValidationError("open candle overlaps finalized history")
            last = candle
            total += candle.sample_count
        samples = state.step - start_step
        if samples < 0 or total != samples or (samples and (
                last is None or last.close != state.resource or last.last_observed_at != state.calculated_at)):
            raise ValidationError("checkpoint and OHLC are inconsistent")

    def _read_session(self, run_id: str, *, legacy=False) -> Session:
        run = self.connection.execute("SELECT * FROM simulation_runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise ValidationError("active run is missing")
        saved_config = json.loads(run["config_json"])
        if set(saved_config) not in (set(ModelConfig.__dataclass_fields__),
                                    set(ModelConfig.__dataclass_fields__) - {"initial_beta"}):
            raise ValidationError("invalid saved configuration keys")
        config = ModelConfig(**saved_config)
        # Hash legacy JSON in its original format, without introducing Beta defaults.
        raw = json.dumps(json.loads(run["config_json"]), sort_keys=True, separators=(",", ":"), allow_nan=False)
        if config.rules_hash != run["rules_hash"] or hashlib.sha256(raw.encode()).hexdigest() != run["config_version"]:
            raise ValidationError("saved configuration hash mismatch")
        row = self.connection.execute("SELECT state_json FROM checkpoints WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ValidationError("checkpoint is missing")
        state = decode_state(row[0])
        if datetime.fromisoformat(run["started_at"]) > state.calculated_at:
            raise ValidationError("checkpoint predates the run")
        if state.step == 0 and state != initial(config, state.calculated_at):
            raise ValidationError("initial checkpoint does not match saved configuration")
        if not config.parameter_min <= state.parameter < config.parameter_max:
            raise ValidationError("checkpoint parameter is outside saved model range")
        opened = self.connection.execute("SELECT candle_json FROM open_candles WHERE run_id=?", (run_id,)).fetchone()
        candle = decode_candle(opened[0]) if opened else None
        self._check_history(run_id, RESOURCE_ID, state, candle, 0)
        beta, beta_candle, start_step = None, None, None
        if not legacy:
            starts = self.connection.execute("SELECT * FROM resource_starts WHERE run_id=?", (run_id,)).fetchall()
            mapping = {row["resource_id"]: row for row in starts}
            if (RESOURCE_ID not in mapping or mapping[RESOURCE_ID]["start_step"] != 0
                    or mapping[RESOURCE_ID]["initial_value"] != config.initial_resource
                    or set(mapping) - {RESOURCE_ID, "beta"}):
                raise ValidationError("invalid resource start registry")
            row = self.connection.execute("SELECT state_json FROM beta_checkpoints WHERE run_id=?", (run_id,)).fetchone()
            if (row is not None) != ("beta" in mapping):
                raise ValidationError("Beta checkpoint/start mismatch")
            if row:
                beta = decode_beta(row[0])
                start_step = mapping["beta"]["start_step"]
                if (type(start_step) is not int or start_step < 0
                        or finite(mapping["beta"]["initial_value"], "Beta initial") < 0):
                    raise ValidationError("invalid Beta start")
                if beta.step != state.step or beta.calculated_at != state.calculated_at:
                    raise ValidationError("resources do not share one checkpoint")
                if state.step == start_step and beta != initial_beta(mapping["beta"]["initial_value"], state):
                    raise ValidationError("invalid initial Beta")
                opened = self.connection.execute("SELECT candle_json FROM beta_open_candles WHERE run_id=?", (run_id,)).fetchone()
                beta_candle = decode_candle(opened[0]) if opened else None
                self._check_history(run_id, "beta", beta, beta_candle, start_step)
        allowed = (RESOURCE_ID, "beta") if beta else (RESOURCE_ID,)
        for row in self.connection.execute("SELECT DISTINCT resource_id FROM candles WHERE run_id=?", (run_id,)):
            if row[0] not in allowed:
                raise ValidationError("unknown resource history")
        missing = self.connection.execute(
            "SELECT 1 FROM candles c LEFT JOIN outbox o USING(run_id, resource_id, minute_start) "
            "WHERE c.run_id=? AND o.run_id IS NULL LIMIT 1", (run_id,)).fetchone()
        if missing:
            raise ValidationError("finalized OHLC is missing its pending record")
        return Session(run_id, config, state, candle, beta, beta_candle, start_step)

    def resume(self, config: ModelConfig, at: datetime, *, new_run: bool = False,
               use_saved_rules: bool = False) -> Session:
        at = utc(at)
        with self.transaction():
            self.validate_all()
            active = self.connection.execute("SELECT run_id FROM active_run WHERE singleton=1").fetchone()
            if active is None and self.connection.execute("SELECT 1 FROM simulation_runs LIMIT 1").fetchone():
                raise ValidationError("active run reference is missing; refusing to initialize")
            previous = self._read_session(active[0]) if active else None
            if previous and at < previous.state.calculated_at:
                raise ValidationError("clock moved backwards; checkpoint preserved")
            if previous and not new_run:
                if config.rules_hash != previous.config.rules_hash and not use_saved_rules:
                    raise ValidationError("model rules changed; use --use-saved-rules or the new-run command")
                if previous.candle and minute(at) > previous.candle.minute_start:
                    self._finalize(previous.run_id, previous.candle)
                    previous = replace(previous, candle=None)
                if previous.beta_candle and minute(at) > previous.beta_candle.minute_start:
                    self._finalize(previous.run_id, previous.beta_candle, "beta")
                    previous = replace(previous, beta_candle=None)
                return previous
            if previous and previous.candle:
                self._finalize(previous.run_id, previous.candle)
            if previous and previous.beta_candle:
                self._finalize(previous.run_id, previous.beta_candle, "beta")
            run_id = str(uuid4())
            state = initial(config, at)
            self.connection.execute("INSERT INTO simulation_runs VALUES (?, ?, ?, ?, ?)",
                                    (run_id, config.to_json(), config.rules_hash, config.config_hash, stamp(at)))
            self.connection.execute("INSERT INTO checkpoints VALUES (?, ?)", (run_id, encode(state)))
            self.connection.execute("INSERT INTO active_run VALUES (1, ?) ON CONFLICT(singleton) "
                                    "DO UPDATE SET run_id=excluded.run_id", (run_id,))
            self.connection.execute("INSERT INTO resource_starts VALUES (?, ?, 0, ?)",
                                    (run_id, RESOURCE_ID, config.initial_resource))
            beta = self._attach_beta(run_id, state, config.initial_beta)
            return Session(run_id, config, state, None, beta, None, 0, True)

    def commit_step(self, session: Session, state: State) -> Session:
        if state.step != session.state.step + 1 or state.calculated_at < session.state.calculated_at:
            raise ValidationError("step is not the next observation")
        if session.beta is None:
            raise ValidationError("Beta is missing; migrate before calculating")
        beta = advance_beta(session.beta, state,
                            continuous=session.continuous)
        with self.transaction():
            active = self.connection.execute("SELECT run_id FROM active_run WHERE singleton=1").fetchone()
            saved = self.connection.execute("SELECT state_json FROM checkpoints WHERE run_id=?",
                                            (session.run_id,)).fetchone()
            saved_beta = self.connection.execute("SELECT state_json FROM beta_checkpoints WHERE run_id=?",
                                                 (session.run_id,)).fetchone()
            if (active is None or active[0] != session.run_id or saved is None
                    or saved[0] != encode(session.state) or saved_beta is None
                    or saved_beta[0] != encode(session.beta)):
                raise ValidationError("stale session; refusing to overwrite committed state")
            candle = self._observe(session.run_id, RESOURCE_ID, session.candle, state)
            beta_candle = self._observe(session.run_id, "beta", session.beta_candle, beta)
            self.connection.execute("UPDATE beta_checkpoints SET state_json=? WHERE run_id=?",
                                    (encode(beta), session.run_id))
            self.connection.execute("UPDATE checkpoints SET state_json=? WHERE run_id=?",
                                    (encode(state), session.run_id))
        # No in-memory publication before COMMIT has succeeded.
        return Session(session.run_id, session.config, state, candle, beta, beta_candle,
                       session.beta_start_step, True)

    def _observe(self, run_id, resource_id, candle, state):
        if candle and minute(state.calculated_at) > candle.minute_start:
            self._finalize(run_id, candle, resource_id)
            candle = None
        if candle is None:
            latest = self.connection.execute(
                "SELECT MAX(minute_start) FROM candles WHERE run_id=? AND resource_id=?",
                (run_id, resource_id)).fetchone()[0]
            if latest and stamp(minute(state.calculated_at)) <= latest:
                raise ValidationError("cannot add observations to a finalized minute")
            candle = Candle.start(state.resource, state.calculated_at)
        else:
            candle = candle.add(state.resource, state.calculated_at)
        table = "open_candles" if resource_id == RESOURCE_ID else "beta_open_candles"
        self.connection.execute(f"INSERT INTO {table} VALUES (?, ?) ON CONFLICT(run_id) "
                                "DO UPDATE SET candle_json=excluded.candle_json", (run_id, encode(candle)))
        return candle


def status(path: Path) -> dict:
    """Read one consistent snapshot. Missing databases are never created."""
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None)
    try:
        connection.execute("BEGIN")
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValidationError("unsupported database schema")
        row = connection.execute("SELECT run_id, state_json FROM checkpoints JOIN active_run USING(run_id)").fetchone()
        if row is None:
            return {"state": "not_initialized"}
        opened = connection.execute("SELECT candle_json FROM open_candles WHERE run_id=?", (row[0],)).fetchone()
        beta = connection.execute("SELECT state_json FROM beta_checkpoints WHERE run_id=?", (row[0],)).fetchone()
        beta_open = connection.execute("SELECT candle_json FROM beta_open_candles WHERE run_id=?", (row[0],)).fetchone()
        start = connection.execute("SELECT start_step FROM resource_starts WHERE run_id=? AND resource_id='beta'", (row[0],)).fetchone()
        if beta is None or start is None:
            raise ValidationError("active run is missing Beta")
        return {"run_id": row[0], "state": json.loads(row[1]), "beta": json.loads(beta[0]),
                "beta_start_step": start[0],
                "beta_open_candle": json.loads(beta_open[0]) if beta_open else None,
                "simulation_elapsed_seconds": decode_state(row[1]).simulation_elapsed_seconds,
                "open_candle": json.loads(opened[0]) if opened else None,
                "closed_candles": connection.execute("SELECT COUNT(*) FROM candles WHERE run_id=?", (row[0],)).fetchone()[0],
                "pending_ohlc": connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]}
    finally:
        connection.close()
