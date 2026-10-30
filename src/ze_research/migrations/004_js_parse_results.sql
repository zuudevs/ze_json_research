-- JavaScript JSON.parse baseline results.
--
-- These rows are deliberately not stored in benchmark_results. Their timing is
-- a median across rounds in microseconds per operation, already aggregated by
-- the harness, whereas benchmark_results holds nanosecond iteration rows from
-- Google Benchmark. Mixing the two lets a reader average a JS median with C++
-- iteration rows and produce a number that means nothing.

CREATE TABLE IF NOT EXISTS js_parse_results (
    js_parse_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL REFERENCES executions(execution_id),
    source_artifact_id TEXT REFERENCES artifacts(artifact_id),
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    payload_id TEXT NOT NULL,
    parser TEXT NOT NULL,
    operation TEXT NOT NULL,
    measurement_contract_id TEXT,
    root_type TEXT,
    payload_sha256 TEXT,
    payload_bytes_utf8 INTEGER,
    sample_manifest_sha256 TEXT,
    snapshot_id TEXT,
    min_us_per_op REAL,
    median_us_per_op REAL,
    mean_us_per_op REAL,
    standard_deviation_us_per_op REAL,
    max_us_per_op REAL,
    coefficient_of_variation_percent REAL,
    ops_per_second REAL,
    bytes_per_second REAL,
    mib_per_second REAL,
    sample_count INTEGER,
    iterations_per_sample INTEGER,
    warmup_iterations INTEGER,
    time_unit TEXT NOT NULL DEFAULT 'us',
    raw_row_json TEXT NOT NULL,
    UNIQUE (execution_id, source_artifact_id, payload_id)
);

CREATE INDEX IF NOT EXISTS idx_js_parse_payload ON js_parse_results(payload_id);
CREATE INDEX IF NOT EXISTS idx_js_parse_run ON js_parse_results(run_id);
