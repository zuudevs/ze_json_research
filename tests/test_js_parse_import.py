"""The JavaScript JSON.parse baseline must be imported without mixing units.

Its rows are microsecond medians produced by the harness, not nanosecond
iteration rows. Keeping them in their own table is what stops a reader from
averaging the two.
"""

import json
from pathlib import Path

import pytest

from ze_research.database import connect, migrate
from ze_research.ingest import IngestError, ingest_manifest
from ze_research.manifest import (
    collect_artifacts,
    sha256_file,
    write_json_atomic,
)
from ze_research.registry import definition_sha256, load_yaml


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = (
    ROOT / "research" / "experiments" / "baseline" / "js-parse-saq.yaml"
)

# One line per payload, shaped like the real harness output.
JSONL_ROWS = [
    {
        "schema_version": 1,
        "experiment_id": "js-parse-saq",
        "run_id": "js-run-0001",
        "timestamp_utc": "2026-10-30T12:00:00.000Z",
        "payload_id": "assistants",
        "snapshot_id": "saq-report-dummy-20261030T033021810Z",
        "sample_manifest_sha256": "d" * 64,
        "payload_sha256": "7de667957e4849c731b86a6c5b2e442b70557aa78b5cfe46d90d9334ed804d5a",
        "payload_bytes_utf8": 140,
        "payload_chars_utf16": 140,
        "parser": "JSON.parse",
        "operation": "parse_only",
        "measurement_contract_id": "json-parse-only-v1",
        "root_type": "array",
        "min_us_per_op": 0.9,
        "median_us_per_op": 1.1,
        "mean_us_per_op": 1.2,
        "standard_deviation_us_per_op": 0.2,
        "max_us_per_op": 1.9,
        "coefficient_of_variation_percent": 16.6,
        "ops_per_second": 909090.0,
        "bytes_per_second": 127272640.0,
        "sample_count": 3,
        "iterations_per_sample": 20,
        "warmup_iterations": 5,
        "sample_us_per_op": [1.0, 1.1, 1.3],
    },
    {
        "schema_version": 1,
        "experiment_id": "js-parse-saq",
        "run_id": "js-run-0001",
        "timestamp_utc": "2026-10-30T12:00:01.000Z",
        "payload_id": "reports",
        "snapshot_id": "saq-report-dummy-20261030T033021810Z",
        "sample_manifest_sha256": "e" * 64,
        "payload_sha256": "2c06b2dfd8d6048a3645d1da62b3e3992369ee20dc5ad5a03723f1e7abe8bcae",
        "payload_bytes_utf8": 428,
        "parser": "JSON.parse",
        "operation": "parse_only",
        "measurement_contract_id": "json-parse-only-v1",
        "root_type": "array",
        "min_us_per_op": 2.1,
        "median_us_per_op": 2.4,
        "mean_us_per_op": 2.45,
        "standard_deviation_us_per_op": 0.3,
        "max_us_per_op": 3.0,
        "coefficient_of_variation_percent": 12.2,
        "ops_per_second": 416666.0,
        "bytes_per_second": 178328320.0,
        "sample_count": 3,
        "iterations_per_sample": 20,
        "warmup_iterations": 5,
        "sample_us_per_op": [2.2, 2.4, 2.7],
    },
]


def _manifest_path(tmp_path: Path) -> Path:
    document = load_yaml(EXPERIMENT_PATH)
    experiment = document["experiment"]
    experiment["correctness_gate"] = None
    experiment["status"] = "approved_for_run"
    experiment["workloads"] = [
        {
            "id": "assistants",
            "name": "assistants.json",
            "benchmark_token": "assistants",
            "dataset_path": "samples/saq-report/assistants.json",
            "dataset_sha256": "7de667957e4849c731b86a6c5b2e442b70557aa78b5cfe46d90d9334ed804d5a",
            "parameters": {},
        },
        {
            "id": "reports",
            "name": "reports.json",
            "benchmark_token": "reports",
            "dataset_path": "samples/saq-report/reports.json",
            "dataset_sha256": "2c06b2dfd8d6048a3645d1da62b3e3992369ee20dc5ad5a03723f1e7abe8bcae",
            "parameters": {},
        },
    ]
    experiment["variants"] = [
        {
            "id": "assistants",
            "name": "assistants",
            "description": "payload",
            "comparison_role": "other",
            "lifecycle_status": "experimental",
            "measurement_contract_id": "json-parse-only-v1",
            "benchmark_name_pattern": "JSON.parse/{dataset}",
            "workloads": ["assistants"],
        },
        {
            "id": "reports",
            "name": "reports",
            "description": "payload",
            "comparison_role": "other",
            "lifecycle_status": "experimental",
            "measurement_contract_id": "json-parse-only-v1",
            "benchmark_name_pattern": "JSON.parse/{dataset}",
            "workloads": ["reports"],
        },
    ]

    run_dir = tmp_path / "runs" / "js-run-0001"
    (run_dir / "raw").mkdir(parents=True)
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "inputs" / "saq-report").mkdir(parents=True)
    (run_dir / "baseline-server").mkdir(parents=True)
    (run_dir / "metadata").mkdir(parents=True)

    (run_dir / "raw" / "benchmark_results.jsonl").write_text(
        "\n".join(json.dumps(row) for row in JSONL_ROWS) + "\n", encoding="utf-8"
    )
    (run_dir / "baseline-server" / "manifest.json").write_text(
        json.dumps({"status": "COMPLETED"}), encoding="utf-8"
    )
    (run_dir / "metadata" / "saq-report-sample-manifest.json").write_text(
        json.dumps({"snapshot_id": "saq-report-dummy-20261030T033021810Z"}),
        encoding="utf-8",
    )
    for payload in ("assistants", "reports"):
        (run_dir / "inputs" / "saq-report" / f"{payload}.json").write_text("[]\n", encoding="utf-8")
    (run_dir / "inputs" / "saq-report" / "manifest.json").write_text(
        json.dumps({"snapshot_id": "saq-report-dummy-20261030T033021810Z"}),
        encoding="utf-8",
    )
    (run_dir / "logs" / "01-run.stdout.log").write_text("ok\n", encoding="utf-8")
    (run_dir / "logs" / "01-run.stderr.log").write_text("", encoding="utf-8")

    snapshot = {"schema_version": 1, "experiment": experiment}
    manifest = {
        "manifest_schema_version": "1.0",
        "run_id": "js-run-0001",
        "experiment_id": experiment["id"],
        "registry_path": str(EXPERIMENT_PATH),
        "registry_file_sha256": sha256_file(EXPERIMENT_PATH),
        "definition_snapshot": snapshot,
        "definition_sha256": definition_sha256(snapshot),
        "started_at": "2026-10-30T12:00:00.000Z",
        "finished_at": "2026-10-30T12:00:02.000Z",
        "status": "COMPLETED",
        "verification_status": "PENDING",
        "source": {"git_commit": "f" * 40, "worktree_clean": True, "repository_root": "."},
        "environment": {"platform": "test"},
        "execution": {"mode": "script", "preset": None, "env_passthrough": {}},
        "inputs": [
            {
                "workload_id": payload,
                "declared_path": f"samples/saq-report/{payload}.json",
                "resolved_path": f"samples/saq-report/{payload}.json",
                "sha256": "0" * 64,
                "size_bytes": 2,
                "matches_definition": False,
            }
            for payload in ("assistants", "reports")
        ],
        "gates": [],
        "build": {"mode": "script", "interpreter": "node", "interpreter_version": "v22.0.0"},
        "execution_records": [
            {
                "sequence_number": 1,
                "purpose": "run",
                "command": ["node", "run-baseline.js"],
                "cwd": ".",
                "started_at": "2026-10-30T12:00:00.500Z",
                "finished_at": "2026-10-30T12:00:02.000Z",
                "status": "COMPLETED",
                "exit_code": 0,
                "error": None,
                "stdout_path": "logs/01-run.stdout.log",
                "stderr_path": "logs/01-run.stderr.log",
                "produced_outputs": ["raw/benchmark_results.jsonl"],
            },
            {
                "sequence_number": 2,
                "purpose": "version_probe",
                "command": ["node", "--version"],
                "cwd": ".",
                "started_at": "2026-10-30T12:00:02.010Z",
                "finished_at": "2026-10-30T12:00:02.020Z",
                "status": "COMPLETED",
                "exit_code": 0,
                "error": None,
                "stdout_path": "logs/02-version.stdout.log",
                "stderr_path": "logs/02-version.stderr.log",
                "reported_version": "v22.0.0",
                "required": False,
            },
        ],
        "expected_outputs": [
            "raw/benchmark_results.jsonl",
            "baseline-server/manifest.json",
            "metadata/saq-report-sample-manifest.json",
            "inputs/saq-report/manifest.json",
            "inputs/saq-report/assistants.json",
            "inputs/saq-report/reports.json",
        ],
        "missing_expected_outputs": [],
        "artifacts": [],
        "notes": [],
    }
    (run_dir / "logs" / "02-version.stdout.log").write_text("v22.0.0\n", encoding="utf-8")
    (run_dir / "logs" / "02-version.stderr.log").write_text("", encoding="utf-8")
    manifest["artifacts"] = collect_artifacts(run_dir)
    manifest_path = run_dir / "manifest.json"
    write_json_atomic(manifest_path, manifest)
    return manifest_path


def test_js_parse_rows_are_imported_with_payload_provenance(tmp_path):
    database = tmp_path / "research.sqlite"
    manifest_path = _manifest_path(tmp_path)
    result = ingest_manifest(manifest_path, database)
    assert result["status"] == "IMPORTED"
    assert result["result_counts"]["js_parse_results"] == 2
    # The JS rows must not land in the C++ nanosecond table.
    assert result["result_counts"]["benchmark_results"] == 0

    with connect(database) as connection:
        rows = connection.execute(
            "SELECT payload_id, median_us_per_op, payload_sha256, sample_count, "
            "time_unit, iterations_per_sample, warmup_iterations "
            "FROM js_parse_results ORDER BY payload_id"
        ).fetchall()
    assert [row["payload_id"] for row in rows] == ["assistants", "reports"]
    assert rows[0]["median_us_per_op"] == 1.1
    assert rows[0]["payload_sha256"].startswith("7de66795")
    assert rows[0]["time_unit"] == "us"
    assert rows[0]["sample_count"] == 3
    assert rows[0]["warmup_iterations"] == 5


def test_js_parse_import_is_idempotent(tmp_path):
    database = tmp_path / "research.sqlite"
    manifest_path = _manifest_path(tmp_path)
    first = ingest_manifest(manifest_path, database)
    second = ingest_manifest(manifest_path, database)
    assert first["result_counts"]["js_parse_results"] == 2
    assert second["status"] == "ALREADY_IMPORTED"


def test_js_parse_rejects_a_line_without_payload_id(tmp_path):
    database = tmp_path / "research.sqlite"
    manifest_path = _manifest_path(tmp_path)
    raw = manifest_path.parent / "raw" / "benchmark_results.jsonl"
    raw.write_text(json.dumps({"parser": "JSON.parse"}) + "\n", encoding="utf-8")
    # Re-artifact the directory so verification still matches the bytes.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"] = collect_artifacts(manifest_path.parent)
    write_json_atomic(manifest_path, manifest)

    with pytest.raises(IngestError):
        ingest_manifest(manifest_path, database)


def test_js_parse_rejects_malformed_json_line(tmp_path):
    database = tmp_path / "research.sqlite"
    manifest_path = _manifest_path(tmp_path)
    raw = manifest_path.parent / "raw" / "benchmark_results.jsonl"
    raw.write_text('{"payload_id": "assistants"\n', encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"] = collect_artifacts(manifest_path.parent)
    write_json_atomic(manifest_path, manifest)

    with pytest.raises(IngestError):
        ingest_manifest(manifest_path, database)


def test_migration_four_creates_the_js_table(tmp_path):
    database = tmp_path / "research.sqlite"
    assert migrate(database) == [1, 2, 3, 4]
    with connect(database) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='js_parse_results'"
        ).fetchone()
    assert exists is not None
