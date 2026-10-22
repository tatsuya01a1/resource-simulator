from contextlib import closing
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from threading import Event
import unittest
from unittest.mock import patch

from resource_simulator.beta import (JST, MAX_OBSERVATION_GAP, BetaState, Schedule,
                                     advance_beta, initial_beta, schedule_at)
from resource_simulator.config import ModelConfig, ValidationError
from resource_simulator.engine import State, advance, initial, stamp
from resource_simulator.persistence import Store, status, encode
from resource_simulator.migration import migrate, fingerprint
from resource_simulator.cloud import committed_snapshot
from resource_simulator.worker import run
from tests.test_stage1 import WorkspaceTest, ROOT


def at(day=22, hour=4, minute=0, second=0, microsecond=0):
    return datetime(2026, 10, day, hour, minute, second, microsecond, tzinfo=JST)


def alpha(step, time, delta=0):
    return State(step, 100, 0, delta, delta, time)


def night_schedule(morning=100, evening=130):
    schedule = schedule_at(None, at(hour=3, minute=59, second=59), at(), morning, continuous=True)
    return schedule_at(schedule, at(hour=17, minute=59, second=59), at(hour=18), evening, continuous=True)


def legacy_database(path):
    connection = sqlite3.connect(path)
    connection.executescript((ROOT / 'tests/fixtures/schema1.sql').read_text())
    config = ModelConfig()
    raw = json.loads(config.to_json())
    del raw['initial_beta']
    raw = json.dumps(raw, sort_keys=True, separators=(',', ':'))
    for index, step in ((1, 0), (2, 3)):
        run_id = f'00000000-0000-0000-0000-{index:012d}'
        connection.execute('INSERT INTO simulation_runs VALUES (?,?,?,?,?)',
                           (run_id, raw, config.rules_hash, hashlib.sha256(raw.encode()).hexdigest(), stamp(at(hour=3, minute=58))))
        state = initial(config, at(hour=3, minute=58)) if step == 0 else State(3, 101, -.2, -2, -2, at(hour=3, minute=59, second=59))
        connection.execute('INSERT INTO checkpoints VALUES (?,?)', (run_id, encode(state)))
    from resource_simulator.aggregation import Candle
    closed = Candle.start(95, at(hour=3, minute=58, second=1)).add(103, at(hour=3, minute=58, second=2))
    connection.execute('INSERT INTO candles VALUES (?,?,?,?)', (run_id, 'resource', stamp(closed.minute_start), encode(closed)))
    connection.execute('INSERT INTO outbox VALUES (?,?,?,?,?,?)',
                       (run_id, 'resource', stamp(closed.minute_start), 'ohlc', 7, stamp(at(hour=5))))
    opened = Candle.start(101, state.calculated_at)
    connection.execute('INSERT INTO open_candles VALUES (?,?)', (run_id, encode(opened)))
    connection.execute('INSERT INTO active_run VALUES (1,?)', (run_id,))
    connection.commit()
    connection.close()


class BetaEngineTests(unittest.TestCase):
    def test_day_five_times_actual_alpha_delta_and_both_lower_bounds(self):
        config = ModelConfig(initial_resource=3, initial_beta=10)
        first = initial(config, at())
        beta = initial_beta(config.initial_beta, first)
        next_alpha = advance(first, config, .1, at(second=1))
        beta = advance_beta(beta, next_alpha, continuous=True)
        self.assertEqual((next_alpha.requested_delta, next_alpha.actual_delta), (-8, -3))
        self.assertEqual((beta.requested_delta, beta.actual_delta, beta.resource), (-15, -10, 0))
        next_alpha = advance(next_alpha, config, .6, at(second=2))
        beta = advance_beta(beta, next_alpha, continuous=True)
        self.assertAlmostEqual(beta.requested_delta, 10)
        self.assertAlmostEqual(beta.resource, 10)

    def test_absolute_night_difference_is_fixed_and_zero_is_stationary(self):
        for evening, x in ((130, 30), (70, 30), (100, 0)):
            schedule = night_schedule(evening=evening)
            beta = BetaState(3, evening, 0, 0, at(hour=18), schedule)
            result = advance_beta(beta, alpha(4, at(hour=19), -2), continuous=True)
            self.assertEqual(result.schedule.multiplier, x)
            self.assertEqual(result.requested_delta, -2 * x)
            self.assertEqual(result.resource, max(0, evening - 2 * x))
            self.assertEqual(result.actual_delta, result.resource - evening)
            # Crossing midnight neither resets x nor pairs a new day's baselines.
            result = advance_beta(result, alpha(5, at(day=23, hour=0), 2), continuous=False)
            self.assertEqual(result.schedule, schedule)

    def test_exact_and_inexact_boundaries_take_pre_step_value(self):
        for microseconds in (0, 400000):
            morning = schedule_at(None, at(hour=3, minute=59, second=59),
                                  at(microsecond=microseconds), 100, continuous=True)
            self.assertEqual(morning.morning_value, 100)
            evening = schedule_at(morning, at(hour=17, minute=59, second=59),
                                  at(hour=18, microsecond=microseconds), 130, continuous=True)
            self.assertEqual((evening.multiplier, evening.night_multiplier), (30, 30))
            dawn = schedule_at(evening, at(day=23, hour=3, minute=59, second=59),
                               at(day=23, microsecond=microseconds), 42, continuous=True)
            self.assertEqual((dawn.multiplier, dawn.mode, dawn.morning_value), (5, 'day', 42))
            self.assertIsNone(dawn.evening_value)

    def test_evening_crossing_applies_new_x_on_same_step(self):
        schedule = schedule_at(None, at(hour=3, minute=59, second=59), at(), 100, continuous=True)
        beta = BetaState(2, 130, 0, 0, at(hour=17, minute=59, second=59), schedule)
        result = advance_beta(beta, alpha(3, at(hour=18), -2), continuous=True)
        self.assertEqual((result.resource, result.requested_delta), (70, -60))
        self.assertEqual(result.schedule.evening_value, 130)

    def test_first_start_does_not_invent_a_baseline_even_at_exact_boundary(self):
        for time in (at(), at(hour=18), at(hour=22), at(hour=0)):
            beta = initial_beta(100, alpha(0, time))
            self.assertIsNone(beta.schedule.morning_value)
            self.assertIsNone(beta.schedule.evening_value)
            self.assertEqual(beta.schedule.multiplier, 5)
            self.assertEqual(beta.schedule.acquisition, 'pending' if time.hour == 4 else 'missing')

    def test_gap_allowance_and_restart_are_distinct_from_crossing(self):
        for seconds, continuous, acquired in ((2.5, True, True), (2.500001, True, False),
                                               (1, False, False), (3600, True, False)):
            now = at(microsecond=100000)
            schedule = schedule_at(None, now - timedelta(seconds=seconds), now, 100, continuous=continuous)
            self.assertEqual(schedule.morning_value, 100 if acquired else None)
        schedule = night_schedule()
        missed = schedule_at(schedule, at(hour=23), at(day=23, hour=18), 150, continuous=False)
        self.assertEqual(missed.acquisition, 'missing')
        self.assertIsNone(missed.night_multiplier)
        self.assertIsNone(missed.morning_value)
        self.assertIsNone(missed.evening_value)
        self.assertEqual(missed.multiplier, 5)

    def test_nonfinite_beta_or_multiplier_is_rejected(self):
        beta = initial_beta(1e308, alpha(0, at()))
        with self.assertRaises(ValidationError):
            advance_beta(beta, alpha(1, at(second=1), 1e308), continuous=True)


class BetaPersistenceTests(WorkspaceTest):
    def prepare_day(self, store):
        session = store.resume(ModelConfig(), at(hour=3, minute=59, second=58))
        session = self.step(store, session, .5, at(hour=3, minute=59, second=59))
        session = self.step(store, session, .5, at())
        self.assertEqual(session.beta.schedule.morning_value, 100)
        return self.step(store, session, .6, at(hour=17, minute=59, second=59))

    def test_restored_night_retains_x_until_dawn_but_not_next_evening(self):
        with Store(self.database) as store:
            session = self.prepare_day(store)
            session = self.step(store, session, .5, at(hour=18))
            x = session.beta.schedule.night_multiplier
            self.assertAlmostEqual(x, 10)
        with Store(self.database) as store:
            session = store.resume(ModelConfig(initial_beta=999), at(day=23, hour=0))
            session = self.step(store, session, .6, at(day=23, hour=0, second=1))
            self.assertEqual(session.beta.schedule.multiplier, x)
            session = self.step(store, session, .5, at(day=23, hour=18))
            self.assertIsNone(session.beta.schedule.night_multiplier)
            self.assertEqual(session.beta.schedule.acquisition, 'missing')
            self.assertEqual(session.beta.schedule.multiplier, 5)

    def test_restart_on_boundary_never_acquires_or_repeats_it(self):
        with Store(self.database) as store:
            session = self.prepare_day(store)
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), at(hour=18))
            session = self.step(store, session, .6, at(hour=18, second=1))
            self.assertIsNone(session.beta.schedule.evening_value)
            self.assertEqual(session.beta.schedule.acquisition, 'missing')
        # Acquire on another fresh DB, restart, then ensure the acquired value is immutable.
        self.database = Path(self.directory.name) / 'continuous.sqlite3'
        with Store(self.database) as store:
            session = self.prepare_day(store)
            session = self.step(store, session, .6, at(hour=18))
            schedule = session.beta.schedule
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), at(hour=18))
            session = self.step(store, session, .6, at(hour=18, second=1))
            self.assertEqual(session.beta.schedule, schedule)

    def test_baselines_and_two_candles_roll_back_together(self):
        with Store(self.database) as store:
            session = self.prepare_day(store)
            before = status(self.database)
            before_public = committed_snapshot(self.database)
            store.connection.execute("CREATE TEMP TRIGGER fail BEFORE UPDATE ON checkpoints "
                                     "BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
            with self.assertRaises(sqlite3.Error):
                self.step(store, session, .6, at(hour=18))
            self.assertEqual(status(self.database), before)
            self.assertEqual(committed_snapshot(self.database), before_public)
            store.validate_all()
            store.connection.execute('DROP TRIGGER fail')
            session = self.step(store, session, .6, at(hour=18))
            store.validate_all()
            self.assertIsNotNone(session.beta.schedule.night_multiplier)
            for resource in ('resource', 'beta'):
                rows = store.connection.execute('SELECT COUNT(*) FROM candles WHERE resource_id=?', (resource,)).fetchone()[0]
                pending = store.connection.execute('SELECT COUNT(*) FROM outbox WHERE resource_id=?', (resource,)).fetchone()[0]
                self.assertEqual(rows, pending)
                self.assertEqual(rows, 3)
            self.assertEqual(session.beta_candle.sample_count, 1)

    def test_sleep_across_boundary_does_one_step_and_has_no_baseline(self):
        before = at(hour=3, minute=59, second=58)
        clock = [0.0]
        def wait(delay):
            clock[0] += delay + (3600 if clock[0] == 0 else 0)
            return False
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), before)
            session = self.step(store, session, .5, before)
            result = run(store, session, Event(), steps=2, sample=lambda: .5,
                         now=lambda: before + timedelta(seconds=clock[0]),
                         monotonic=lambda: clock[0], wait=wait)
            self.assertEqual(result.state.step, 3)
            self.assertIsNone(result.beta.schedule.morning_value)
            self.assertIsNone(result.beta.schedule.evening_value)
            store.validate_all()


class MigrationTests(WorkspaceTest):
    def setUp(self):
        super().setUp()
        legacy_database(self.database)

    def legacy_fingerprint(self):
        with closing(sqlite3.connect(self.database)) as connection, connection:
            return fingerprint(connection)

    def test_existing_alpha_exact_preservation_and_beta_begins_at_current_step(self):
        before = self.legacy_fingerprint()
        result = migrate(self.database, ModelConfig(initial_beta=123))
        self.assertEqual(self.legacy_fingerprint(), before)
        backup = Path(result['backup'])
        manifest = json.loads(Path(result['manifest']).read_text())
        self.assertTrue(manifest['verified'])
        self.assertEqual(hashlib.sha256(backup.read_bytes()).hexdigest(), manifest['backup_sha256'])
        with closing(sqlite3.connect(backup)) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(fingerprint(connection), before)
        with Store(self.database) as store:
            session = store.resume(ModelConfig(initial_beta=999), at())
            self.assertEqual(session.state.step, 3)
            self.assertEqual(session.state.resource, 101)
            self.assertEqual(session.beta.resource, 123)
            self.assertEqual(session.beta_start_step, 3)
            self.assertEqual(store.connection.execute("SELECT COUNT(*) FROM candles WHERE resource_id='beta'").fetchone()[0], 0)
            self.assertIsNone(session.beta.schedule.morning_value)
            session = self.step(store, session, .6, at(second=1))
            self.assertEqual(session.beta_candle.sample_count, 1)
            self.assertEqual(session.beta.step, 4)
            store.validate_all()
        self.assertTrue(migrate(self.database, ModelConfig())['already_migrated'])
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), at(second=2))
            self.assertEqual(session.beta_start_step, 3)
        self.assertEqual(len(list(self.database.parent.glob('*.manifest.json'))), 1)

    def test_failure_at_each_migration_phase_preserves_original_schema_and_data(self):
        before = self.legacy_fingerprint()
        for phase in ('backup_verified', 'tables_created', 'beta_added', 'validated', 'version_set'):
            def fail(current):
                if current == phase:
                    raise OSError('injected migration failure')
            with self.subTest(phase=phase), self.assertRaises(OSError):
                migrate(self.database, ModelConfig(), checkpoint=fail)
            self.assertEqual(self.legacy_fingerprint(), before)
            with closing(sqlite3.connect(self.database)) as connection, connection:
                self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 1)
                self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='beta_checkpoints'").fetchone())
        migrate(self.database, ModelConfig())

    def test_backup_failure_or_corrupt_old_history_never_starts_conversion(self):
        before = self.legacy_fingerprint()
        with patch.object(Path, 'open', side_effect=OSError('no space')), self.assertRaises(OSError):
            migrate(self.database, ModelConfig())
        self.assertEqual(self.legacy_fingerprint(), before)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute('DELETE FROM outbox')
        broken = self.legacy_fingerprint()
        with self.assertRaises(ValidationError):
            migrate(self.database, ModelConfig())
        self.assertEqual(self.legacy_fingerprint(), broken)
        self.assertEqual(len(list(self.database.parent.glob('*.manifest.json'))), 0)

    def test_old_schema_requires_explicit_migration(self):
        before = self.legacy_fingerprint()
        with self.assertRaises(ValidationError):
            Store(self.database)
        self.assertEqual(self.legacy_fingerprint(), before)

    def test_kill_during_migration_recovers_v1_and_verified_backup(self):
        before = self.legacy_fingerprint()
        process = self.child('-m', 'tests.migration_crash_helper', str(self.database))
        self.assertEqual(self.line(process).strip(), 'migration-uncommitted')
        process.kill()
        process.communicate(timeout=10)
        self.assertEqual(self.legacy_fingerprint(), before)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 1)
        self.assertEqual(len(list(self.database.parent.glob('*.manifest.json'))), 1)
        migrate(self.database, ModelConfig())

class AdditionalRecoveryTests(WorkspaceTest):
    def test_corruption_in_beta_history_and_inactive_run_is_rejected(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), at())
            session = self.step(store, session, .6, at(second=1))
            old_id = session.run_id
            store.resume(ModelConfig(), at(hour=5), new_run=True)
            store.connection.execute("UPDATE candles SET candle_json=json_set(candle_json,'$.sample_count',99) "
                                     "WHERE resource_id='beta' AND run_id=?", (old_id,))
        with self.assertRaises(ValidationError):
            Store(self.database)

    def test_configuration_rule_choice_and_generation_survive_migration(self):
        legacy_database(self.database)
        migrate(self.database, ModelConfig())
        with Store(self.database) as store:
            with self.assertRaises(ValidationError):
                store.resume(ModelConfig(coefficient=9), at())
            saved = store.resume(ModelConfig(coefficient=9), at(), use_saved_rules=True)
            self.assertEqual(saved.config.coefficient, 10)
            session = self.step(store, saved, .6, at(second=1))
            self.assertEqual(session.beta_start_step, 3)
            self.assertEqual(committed_snapshot(self.database)['run_generation'], 2)
            new = store.resume(ModelConfig(initial_beta=150), at(second=2), new_run=True)
            self.assertEqual(new.beta.resource, 150)
            self.assertEqual(new.beta_start_step, 0)
            self.assertEqual(committed_snapshot(self.database)['run_generation'], 3)
            store.validate_all()
            self.assertIsNone(store.connection.execute("SELECT name FROM sqlite_master WHERE name='candle_ledger'").fetchone())

    def test_atomic_failure_when_beta_outbox_write_fails(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), at())
            session = self.step(store, session, .6, at(second=1))
            before = status(self.database)
            store.connection.execute("CREATE TEMP TRIGGER fail BEFORE INSERT ON outbox "
                                     "WHEN NEW.resource_id='beta' BEGIN SELECT RAISE(ABORT,'Beta outbox failure'); END")
            with self.assertRaises(sqlite3.Error):
                self.step(store, session, .6, at(minute=1))
            self.assertEqual(status(self.database), before)
            store.validate_all()

class MissingBaselineTests(unittest.TestCase):
    def test_evening_without_morning_is_unconfirmed_not_zero(self):
        before = at(hour=17, minute=59, second=59)
        schedule = schedule_at(None, before, before, 100, continuous=False)
        schedule = schedule_at(schedule, before, at(hour=18), 130, continuous=True)
        self.assertIsNone(schedule.morning_value)
        self.assertEqual(schedule.evening_value, 130)
        self.assertIsNone(schedule.night_multiplier)
        self.assertEqual((schedule.multiplier, schedule.acquisition), (5, 'missing'))

class FirstBoundaryTests(WorkspaceTest):
    def test_fresh_worker_can_capture_committed_initial_value_at_first_crossing(self):
        with Store(self.database) as store:
            session = store.resume(ModelConfig(), at(hour=3, minute=59, second=59))
            session = self.step(store, session, .6, at())
            self.assertEqual(session.beta.schedule.morning_value, 100)
            self.assertAlmostEqual(session.beta.resource, 110)
            self.assertEqual(session.beta_candle.sample_count, 1)

    def test_cli_migration_and_one_step_run_preserve_alpha_and_beta_start(self):
        legacy_database(self.database)
        import subprocess
        import sys
        command = [sys.executable, '-m', 'resource_simulator']
        result = subprocess.run([*command, 'migrate', '--db', str(self.database)], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['schema_version'], 3)
        result = subprocess.run([*command, 'run', '--db', str(self.database), '--steps', '1'], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        state = status(self.database)
        self.assertEqual(state['state']['step'], 4)
        self.assertEqual(state['beta']['step'], 4)
        self.assertEqual(state['beta_start_step'], 3)
