-- Frozen schema from codex/stage1-local-worker 576595f, not a downgraded v3 DB.
PRAGMA foreign_keys=ON;
CREATE TABLE simulation_runs (
 run_id TEXT PRIMARY KEY, config_json TEXT NOT NULL,
 rules_hash TEXT NOT NULL, config_version TEXT NOT NULL, started_at TEXT NOT NULL);
CREATE TABLE active_run (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 run_id TEXT NOT NULL REFERENCES simulation_runs(run_id));
CREATE TABLE checkpoints (run_id TEXT PRIMARY KEY REFERENCES simulation_runs(run_id), state_json TEXT NOT NULL);
CREATE TABLE open_candles (run_id TEXT PRIMARY KEY REFERENCES simulation_runs(run_id), candle_json TEXT NOT NULL);
CREATE TABLE candles (run_id TEXT NOT NULL REFERENCES simulation_runs(run_id), resource_id TEXT NOT NULL,
 minute_start TEXT NOT NULL, candle_json TEXT NOT NULL, PRIMARY KEY(run_id,resource_id,minute_start));
CREATE TABLE outbox (run_id TEXT NOT NULL, resource_id TEXT NOT NULL, minute_start TEXT NOT NULL,
 kind TEXT NOT NULL DEFAULT 'ohlc' CHECK(kind='ohlc'), attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TEXT, PRIMARY KEY(run_id,resource_id,minute_start),
 FOREIGN KEY(run_id,resource_id,minute_start) REFERENCES candles(run_id,resource_id,minute_start));
PRAGMA user_version=1;
