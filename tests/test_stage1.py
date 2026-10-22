from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch

from resource_simulator.config import ModelConfig, ValidationError, load_config
from resource_simulator.engine import advance, initial
from resource_simulator.locking import AlreadyRunning, ProcessLock
from resource_simulator.persistence import Store, status
from resource_simulator.worker import run


AT = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".cache" / "tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "worker.sqlite3"

    def step(self, store, session, sample, at):
        return store.commit_step(session, advance(session.state, session.config, sample, at))

    def child(self, *args):
        process = subprocess.Popen([sys.executable, *args], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=10)
        self.addCleanup(cleanup)
        return process

    def line(self, process, timeout=10):
        messages = queue.Queue()
        Thread(target=lambda: messages.put(process.stdout.readline()), daemon=True).start()
        return messages.get(timeout=timeout)


class EngineTests(unittest.TestCase):
    def test_ac02_fixed_input_sequence(self):
        config = ModelConfig()
        state = initial(config, AT)
        for index, (sample, parameter, resource) in enumerate(
                ((0.25, -0.5, 95), (0.9, 0.8, 103), (0, -1, 93)), 1):
            state = advance(state, config, sample, AT + timedelta(seconds=index))
            self.assertAlmostEqual(state.parameter, parameter)
            self.assertAlmostEqual(state.resource, resource)
            self.assertEqual(state.simulation_elapsed_seconds, index)

    def test_ac03_lower_bound_has_no_carry(self):
        config = ModelConfig(initial_resource=3)
        state = advance(initial(config, AT), config, 0.1, AT)
        self.assertEqual((state.resource, state.requested_delta, state.actual_delta), (0, -8, -3))
        state = advance(state, config, 0.75, AT)
        self.assertEqual(state.resource, 5)

    def test_ac15_invalid_configuration(self):
        invalid = ({"initial_resource": -1}, {"initial_resource": True},
                   {"coefficient": -1}, {"coefficient": "10"}, {"interval_seconds": 2},
                   {"parameter_min": 1, "parameter_max": -1}, {"initial_parameter": 1},
                   {"parameter_min": -1e308, "parameter_max": 1e308},
                   {"initial_resource": 10**1000})
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ModelConfig(**changes)
        for field in ModelConfig.__dataclass_fields__:
            for value in (math.nan, math.inf, -math.inf):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    ModelConfig(**{field: value})

    def test_ac15_nonfinite_inputs_and_arithmetic(self):
        config = ModelConfig()
        state = initial(config, AT)
        for sample in (math.nan, math.inf, -0.1, 1, True):
            with self.subTest(sample=sample), self.assertRaises(ValidationError):
                advance(state, config, sample, AT)
        config = ModelConfig(initial_resource=1e308, coefficient=1e308)
        with self.assertRaises(ValidationError):
            advance(initial(config, AT), config, 0.99, AT)
        config = ModelConfig(parameter_min=-2, parameter_max=2, coefficient=1e308)
        with self.assertRaises(ValidationError):
            advance(initial(config, AT), config, 0, AT)

    def test_excluded_upper_endpoint_and_no_rounding(self):
        config = ModelConfig()
        state = advance(initial(config, AT), config, math.nextafter(1, 0), AT)
        self.assertLess(state.parameter, 1)
        state = advance(initial(config, AT), config, 0.5123456789, AT)
        self.assertNotEqual(state.resource, round(state.resource, 2))

    def test_backward_and_naive_time_rejected(self):
        state = initial(ModelConfig(), AT)
        for at in (AT - timedelta(microseconds=1), AT.replace(tzinfo=None)):
            with self.assertRaises(ValidationError):
                advance(state, ModelConfig(), 0.5, at)


class PersistenceTests(WorkspaceTest):
    def test_ac01_first_start_is_step_zero_without_candle(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            self.assertEqual((session.state.step, session.state.resource, session.state.parameter), (0, 100, 0))
            self.assertIsNone(session.candle)
            self.assertEqual(store.connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(store.connection.execute("PRAGMA synchronous").fetchone()[0], 2)
        self.assertEqual(status(self.database)["closed_candles"], 0)

    def test_ac08_ohlc_and_atomic_outbox(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            for i, sample in enumerate((0.5, 0.7, 0.2, 0.7)):
                session = self.step(store, session, sample, AT + timedelta(seconds=i+1))
            candle = session.candle
            self.assertEqual((candle.open, candle.high, candle.low, candle.close, candle.sample_count),
                             (100, 104, 98, 102, 4))
            session = self.step(store, session, 0.5, AT + timedelta(minutes=1))
            closed = store.connection.execute("SELECT candle_json FROM candles").fetchone()[0]
            self.assertEqual(json.loads(closed)["sample_count"], 4)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 2)
            self.assertEqual(session.candle.sample_count, 1)

    def test_ac09_restart_within_minute_preserves_open_state(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            session = self.step(store, session, 0.25, AT + timedelta(seconds=1))
        with Store(self.database) as store:
            resumed = store.resume(ModelConfig(), AT + timedelta(seconds=5))
            self.assertEqual(resumed, session)
            resumed = self.step(store, resumed, 0.9, AT + timedelta(seconds=6))
            self.assertEqual(resumed.candle.sample_count, 2)
            self.assertEqual(resumed.candle.open, 95)
            self.assertEqual(resumed.candle.close, 103)

    def test_ac09_gap_closes_old_minute_without_synthetic_data(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            session = self.step(store, session, 0.5, AT + timedelta(seconds=1))
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT + timedelta(minutes=10))
            self.assertIsNone(session.candle)
            self.assertEqual(session.state.step, 1)
            session = self.step(store, session, 0.5, AT + timedelta(minutes=10, seconds=1))
            self.assertEqual(session.state.step, 2)
            self.assertEqual(session.candle.sample_count, 1)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM candles").fetchone()[0], 2)
        with Store(self.database) as store:
            store.resume(ModelConfig(), AT + timedelta(minutes=10, seconds=2))

    def test_ac14_initial_values_do_not_reset_saved_state(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            session = self.step(store, session, 0.25, AT)
        changed = ModelConfig(initial_resource=999, initial_parameter=0.5)
        with Store(self.database) as store:
            resumed = store.resume(changed, AT)
            self.assertEqual(resumed, session)
            new = store.resume(changed, AT, new_run=True)
            self.assertNotEqual(new.run_id, resumed.run_id)
            self.assertEqual((new.state.step, new.state.resource, new.state.parameter), (0, 999, 0.5))
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0], 2)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM candles").fetchone()[0], 2)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 2)

    def test_rule_changes_require_explicit_choice(self):
        with Store(self.database) as store:
            original = store.resume(ModelConfig(), AT)
            changed = ModelConfig(coefficient=5)
            with self.assertRaises(ValidationError):
                store.resume(changed, AT)
            self.assertEqual(store.resume(changed, AT, use_saved_rules=True), original)
            new = store.resume(changed, AT, new_run=True)
            self.assertEqual(new.config.coefficient, 5)

    def test_ac15_failure_rolls_back_checkpoint_candles_and_outbox(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            session = self.step(store, session, 0.25, AT + timedelta(seconds=1))
            before = status(self.database)
            # Failure occurs after candle+outbox and new open-candle writes.
            store.connection.execute("CREATE TEMP TRIGGER fail BEFORE UPDATE ON checkpoints "
                                     "BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END")
            with self.assertRaises(sqlite3.Error):
                self.step(store, session, 0.9, AT + timedelta(minutes=1))
            self.assertEqual(status(self.database), before)
        with Store(self.database) as store:
            self.assertEqual(store.resume(ModelConfig(), AT + timedelta(seconds=2)), session)

    def test_ac15_nonfinite_calculation_preserves_last_commit(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            before = status(self.database)
            with self.assertRaises(ValidationError):
                self.step(store, session, math.inf, AT)
            self.assertEqual(status(self.database), before)

    def test_clock_reversal_and_finalized_minute_are_rejected(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            session = self.step(store, session, 0.5, AT + timedelta(seconds=1))
            with self.assertRaises(ValidationError):
                store.resume(ModelConfig(), AT)
            session = store.resume(ModelConfig(), AT + timedelta(minutes=1))
            before = status(self.database)
            with self.assertRaises(ValidationError):
                self.step(store, session, 0.5, AT + timedelta(seconds=2))
            self.assertEqual(status(self.database), before)

    def test_stale_session_cannot_overwrite_newer_state(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            self.step(store, session, 0.25, AT)
            with self.assertRaises(ValidationError):
                self.step(store, session, 0.9, AT)
            self.assertEqual(status(self.database)["state"]["resource"], 95)

    def test_unknown_schema_and_corrupt_db_are_preserved(self):
        self.database.write_bytes(b"not a SQLite database")
        with self.assertRaises(sqlite3.Error):
            Store(self.database)
        self.assertEqual(self.database.read_bytes(), b"not a SQLite database")
        self.database.unlink()
        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA user_version=99")
        connection.close()
        with self.assertRaises(ValidationError):
            Store(self.database)

    def test_inconsistent_checkpoint_is_not_reinitialized(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            session = self.step(store, session, 0.25, AT)
            store.connection.execute("DELETE FROM open_candles")
            with self.assertRaises(ValidationError):
                store.resume(ModelConfig(), AT)
            self.assertEqual(status(self.database)["state"]["step"], 1)

    def test_missing_active_reference_does_not_create_a_new_run(self):
        with Store(self.database) as store:
            store.resume(ModelConfig(), AT)
            store.connection.execute("DELETE FROM active_run")
            with self.assertRaises(ValidationError):
                store.resume(ModelConfig(), AT)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0], 1)

    def test_missing_outbox_is_rejected_without_losing_history(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            session = self.step(store, session, 0.5, AT)
            session = self.step(store, session, 0.5, AT + timedelta(minutes=1))
            store.connection.execute("DELETE FROM outbox")
            with self.assertRaises(ValidationError):
                store.resume(ModelConfig(), AT + timedelta(minutes=1))
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM candles").fetchone()[0], 2)

    def test_jst_midnight_and_minute_boundary(self):
        jst = timezone(timedelta(hours=9))
        before = datetime(2026, 10, 21, 23, 59, 59, 999999, tzinfo=jst)
        after = datetime(2026, 10, 22, 0, 0, 0, tzinfo=jst)
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), before)
            session = self.step(store, session, 0.5, before)
            session = self.step(store, session, 0.5, after)
            self.assertEqual(session.candle.minute_start.hour, 15)
            self.assertEqual(session.candle.sample_count, 1)
            closed = json.loads(store.connection.execute("SELECT candle_json FROM candles").fetchone()[0])
            self.assertEqual(closed["minute_start"], "2026-10-21T14:59:00.000000+00:00")

    def test_worker_waits_first_second_and_does_not_catch_up(self):
        clock = [0.0]
        waits = []
        def wait(delay):
            waits.append(delay)
            clock[0] += delay
            if len(waits) == 1:
                clock[0] += 3600  # a sleeping PC resumes after one hour
            return False
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            result = run(store, session, Event(), steps=2, sample=lambda: 0.5,
                         now=lambda: AT + timedelta(seconds=clock[0]),
                         monotonic=lambda: clock[0], wait=wait)
            self.assertEqual(result.state.step, 2)
            self.assertEqual(waits, [1.0, 1.0])

    def timed_worker(self, *, storage_seconds, sleep_seconds=0, steps=3):
        clock = [0.0]
        waits = []
        starts = []

        def wait(delay):
            self.assertGreater(delay, 0, "overdue work must not cause a catch-up loop")
            waits.append(delay)
            clock[0] += delay
            if len(waits) == 1:
                clock[0] += sleep_seconds
            return False

        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            commit = store.commit_step

            def slow_commit(session, state):
                starts.append(clock[0])
                result = commit(session, state)
                clock[0] += storage_seconds
                return result

            with patch.object(store, "commit_step", side_effect=slow_commit):
                result = run(store, session, Event(), steps=steps, sample=lambda: 0.5,
                             now=lambda: AT + timedelta(seconds=clock[0]),
                             monotonic=lambda: clock[0], wait=wait)
            self.assertEqual(result.state.step, steps)
            self.assertEqual(status(self.database)["state"]["step"], steps)
        return starts, waits

    def test_worker_storage_time_does_not_shift_normal_deadlines(self):
        starts, waits = self.timed_worker(storage_seconds=0.2)
        for actual, expected in zip(starts, (1.0, 2.0, 3.0), strict=True):
            self.assertAlmostEqual(actual, expected)
        for actual, expected in zip(waits, (1.0, 0.8, 0.8), strict=True):
            self.assertAlmostEqual(actual, expected)

    def test_worker_rebases_when_storage_reaches_or_exceeds_next_deadline(self):
        # Finish exactly at the next deadline, then exceed it: both must wait.
        for duration in (1.0, 1.2):
            with self.subTest(storage_seconds=duration):
                self.database = Path(self.directory.name) / f"overrun-{duration}.sqlite3"
                starts, waits = self.timed_worker(storage_seconds=duration)
                for actual, expected in zip(starts, (1.0, 2.0 + duration, 3.0 + 2 * duration), strict=True):
                    self.assertAlmostEqual(actual, expected)
                self.assertEqual(waits, [1.0, 1.0, 1.0])

    def test_worker_sleep_with_storage_delay_does_not_catch_up(self):
        starts, waits = self.timed_worker(storage_seconds=0.2, sleep_seconds=3600, steps=2)
        self.assertAlmostEqual(starts[0], 3601.0)
        self.assertAlmostEqual(starts[1], 3602.2)
        self.assertEqual(waits, [1.0, 1.0])

    def test_normal_stop_does_not_generate_another_step(self):
        stop = Event()
        stop.set()
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), AT)
            result = run(store, session, stop)
            self.assertEqual(result, session)


class ProcessTests(WorkspaceTest):
    def test_ac04_kill_during_transaction_restores_all_committed_state(self):
        process = self.child("-m", "tests.crash_helper", str(self.database))
        self.assertEqual(self.line(process).strip(), "transaction-uncommitted")
        # Reader sees only the committed checkpoint, even while the writer is mid-transaction.
        snapshot = status(self.database)
        self.assertEqual(snapshot["state"]["step"], 2)
        self.assertEqual(snapshot["state"]["resource"], 103)
        self.assertEqual(snapshot["open_candle"]["sample_count"], 2)
        self.assertEqual(snapshot["pending_ohlc"], 0)
        process.kill()
        process.communicate(timeout=10)
        with ProcessLock(self.database), Store(self.database) as store:
            session = store.resume(ModelConfig(), AT + timedelta(seconds=59))
            self.assertEqual(session.state.step, 2)
            self.assertEqual(session.candle.sample_count, 2)
            session = self.step(store, session, 0, AT + timedelta(minutes=1))
            self.assertEqual((session.state.step, session.state.resource), (3, 93))
            snapshot = status(self.database)
            self.assertEqual(snapshot["closed_candles"], 2)
            self.assertEqual(snapshot["pending_ohlc"], 2)

    def test_ac16_two_processes_and_lock_release_after_kill(self):
        process = self.child("-m", "resource_simulator", "run", "--db", str(self.database))
        startup = json.loads(self.line(process))
        self.assertEqual(startup["step"], 0)
        with self.assertRaises(AlreadyRunning):
            with ProcessLock(self.database):
                pass
        second = subprocess.run([sys.executable, "-m", "resource_simulator", "run", "--db", str(self.database),
                                 "--steps", "1"], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(second.returncode, 1)
        self.assertIn("another Worker", second.stderr)
        process.kill()
        process.communicate(timeout=10)
        with ProcessLock(self.database):
            pass

    def test_cli_committed_steps_resume_and_readonly_status(self):
        command = [sys.executable, "-m", "resource_simulator", "run", "--db", str(self.database), "--steps", "1"]
        first = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(first.returncode, 0, first.stderr)
        first_status = status(self.database)
        self.assertEqual(first_status["state"]["step"], 1)
        second = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout)["step"], 1)
        self.assertEqual(status(self.database)["state"]["step"], 2)
        self.assertEqual(status(self.database)["run_id"], first_status["run_id"])

    def test_invalid_configuration_does_not_create_database(self):
        config = Path(self.directory.name) / "invalid.toml"
        config.write_text('[model]\ncoefficient = nan\n', encoding="utf-8")
        result = subprocess.run([sys.executable, "-m", "resource_simulator", "run", "--db", str(self.database),
                                 "--config", str(config), "--steps", "1"], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.database.exists())

    def test_status_does_not_create_database(self):
        with self.assertRaises(sqlite3.Error):
            status(self.database)
        self.assertFalse(self.database.exists())

    def test_configuration_strict_keys_and_sample_file(self):
        self.assertEqual(load_config(ROOT / "config" / "simulator.toml"), ModelConfig())
        file = Path(self.directory.name) / "invalid.toml"
        for content in ('[model]\ncoefficent = 10\n', '[model]\n', 'model = 1\n',
                        '[model]\nx = 1\n[cloud]\nsecret = "x"\n'):
            file.write_text(content, encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_config(file)


if __name__ == "__main__":
    unittest.main()
