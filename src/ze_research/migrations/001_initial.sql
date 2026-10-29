CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    categories_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS experiment_versions (
    experiment_version_id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
    definition_sha256 TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    purpose TEXT NOT NULL,
    motivation TEXT,
    research_question TEXT NOT NULL,
    hypotheses_json TEXT NOT NULL,
    assumptions_json TEXT NOT NULL,
    limitations_json TEXT NOT NULL,
    protocol_json TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (experiment_id, definition_sha256)
);

CREATE TABLE IF NOT EXISTS research_references (
    reference_key TEXT PRIMARY KEY,
    experiment_version_id TEXT NOT NULL REFERENCES experiment_versions(experiment_version_id),
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    doi TEXT,
    locator TEXT,
    accessed_at TEXT,
    evidence_note TEXT NOT NULL,
    review_status TEXT NOT NULL,
    UNIQUE (experiment_version_id, source_id)
);

CREATE TABLE IF NOT EXISTS research_basis_items (
    basis_key TEXT PRIMARY KEY,
    experiment_version_id TEXT NOT NULL REFERENCES experiment_versions(experiment_version_id),
    basis_id TEXT NOT NULL,
    basis_type TEXT NOT NULL,
    statement TEXT NOT NULL,
    UNIQUE (experiment_version_id, basis_id)
);

CREATE TABLE IF NOT EXISTS research_basis_references (
    basis_key TEXT NOT NULL REFERENCES research_basis_items(basis_key),
    reference_key TEXT NOT NULL REFERENCES research_references(reference_key),
    PRIMARY KEY (basis_key, reference_key)
);

CREATE TABLE IF NOT EXISTS algorithm_variants (
    algorithm_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'experimental'
);

CREATE TABLE IF NOT EXISTS workload_cases (
    workload_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    dataset_path TEXT,
    dataset_sha256 TEXT,
    size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
    parameters_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS measurement_contracts (
    contract_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    operation TEXT NOT NULL,
    includes_json TEXT NOT NULL,
    excludes_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS benchmark_specs (
    benchmark_spec_id TEXT PRIMARY KEY,
    experiment_version_id TEXT NOT NULL REFERENCES experiment_versions(experiment_version_id),
    algorithm_id TEXT NOT NULL REFERENCES algorithm_variants(algorithm_id),
    workload_id TEXT NOT NULL REFERENCES workload_cases(workload_id),
    contract_id TEXT NOT NULL REFERENCES measurement_contracts(contract_id),
    name_pattern TEXT NOT NULL,
    comparison_role TEXT NOT NULL,
    comparison_group TEXT
);

CREATE TABLE IF NOT EXISTS research_runs (
    run_id TEXT PRIMARY KEY,
    experiment_version_id TEXT NOT NULL REFERENCES experiment_versions(experiment_version_id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    manifest_path TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    source_json TEXT NOT NULL,
    environment_json TEXT NOT NULL,
    build_json TEXT NOT NULL,
    definition_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_inputs (
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    workload_id TEXT NOT NULL REFERENCES workload_cases(workload_id),
    resolved_path TEXT,
    sha256 TEXT,
    size_bytes INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
    parameters_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, workload_id)
);

CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    sequence_number INTEGER NOT NULL,
    purpose TEXT NOT NULL,
    command_json TEXT NOT NULL,
    cwd TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    exit_code INTEGER,
    stdout_artifact_path TEXT,
    stderr_artifact_path TEXT,
    UNIQUE (run_id, sequence_number)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    execution_id TEXT REFERENCES executions(execution_id),
    relative_path TEXT NOT NULL,
    kind TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    verified INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0,1)),
    UNIQUE (run_id, relative_path)
);

CREATE TABLE IF NOT EXISTS benchmark_results (
    result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL REFERENCES executions(execution_id),
    source_artifact_id TEXT REFERENCES artifacts(artifact_id),
    benchmark_name_raw TEXT NOT NULL,
    benchmark_spec_id TEXT REFERENCES benchmark_specs(benchmark_spec_id),
    mapping_status TEXT NOT NULL CHECK (mapping_status IN ('MAPPED','UNMAPPED','AMBIGUOUS')),
    run_type TEXT,
    iterations INTEGER,
    real_time REAL,
    cpu_time REAL,
    time_unit TEXT,
    bytes_per_second REAL,
    items_per_second REAL,
    error_occurred INTEGER NOT NULL DEFAULT 0 CHECK (error_occurred IN (0,1)),
    error_message TEXT,
    raw_row_json TEXT NOT NULL,
    UNIQUE (execution_id, source_artifact_id, benchmark_name_raw, run_type, iterations, real_time, cpu_time)
);

CREATE TABLE IF NOT EXISTS test_results (
    test_result_id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL REFERENCES executions(execution_id),
    source_artifact_id TEXT REFERENCES artifacts(artifact_id),
    suite_name TEXT,
    test_name TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_seconds REAL,
    failure_text TEXT,
    raw_case_json TEXT NOT NULL,
    UNIQUE (execution_id, source_artifact_id, suite_name, test_name)
);

CREATE TABLE IF NOT EXISTS analysis_runs (
    analysis_run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    analysis_version TEXT NOT NULL,
    configuration_json TEXT NOT NULL,
    status TEXT NOT NULL,
    output_path TEXT
);

CREATE TABLE IF NOT EXISTS analysis_inputs (
    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(analysis_run_id),
    run_id TEXT NOT NULL REFERENCES research_runs(run_id),
    PRIMARY KEY (analysis_run_id, run_id)
);

CREATE TABLE IF NOT EXISTS derived_results (
    derived_result_id TEXT PRIMARY KEY,
    analysis_run_id TEXT NOT NULL REFERENCES analysis_runs(analysis_run_id),
    result_type TEXT NOT NULL,
    result_key TEXT NOT NULL,
    value_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_experiment ON research_runs(experiment_version_id, started_at);
CREATE INDEX IF NOT EXISTS idx_exec_run ON executions(run_id, sequence_number);
CREATE INDEX IF NOT EXISTS idx_run_inputs_workload ON run_inputs(workload_id, sha256);
CREATE INDEX IF NOT EXISTS idx_artifact_run ON artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_artifact_sha256 ON artifacts(sha256);
CREATE INDEX IF NOT EXISTS idx_benchmark_name ON benchmark_results(benchmark_name_raw);
CREATE INDEX IF NOT EXISTS idx_benchmark_spec ON benchmark_results(benchmark_spec_id);
CREATE INDEX IF NOT EXISTS idx_test_name ON test_results(test_name, status);
