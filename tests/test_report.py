"""Reports must only claim what the stored evidence supports."""

import json
import sqlite3
from pathlib import Path

import pytest

from ze_research.database import connect, migrate
from ze_research.ingest import ingest_manifest
from ze_research.manifest import sha256_file, write_json_atomic
from ze_research.registry import definition_sha256, load_yaml
from ze_research.report import ReportError, build_report, export_payload, record_analysis


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = ROOT / "research" / "experiments" / "scanner-width.yaml"


def _snapshot(document: dict) -> dict:
    return {
        "schema_version": document.get("schema_version"),
        "experiment": document["experiment"],
    }


def _seed_run(
    tmp_path: Path,
    *,
    benchmark_rows: list[dict],
    gate_run_id: str | None = None,
    experiment_id: str = "scanner-width-comparison",
    run_id: str | None = None,
) -> Path:
    """Build a finished run directory that ingest will accept."""
    document = load_yaml(EXPERIMENT_PATH)
    experiment = document["experiment"]
    experiment["id"] = experiment_id
    resolved_run_id = run_id or "seed-run-0001"
    if gate_run_id is None:
        experiment.pop("correctness_gate", None)
    else:
        experiment["correctness_gate"]["matched_run_id"] = gate_run_id
    # One workload, one mapping token: the imported rows map exactly.
    experiment["workloads"] = [
        {
            "id": "github-events",
            "name": "GitHub Events JSON dataset",
            "benchmark_token": "GithubEvents",
            "dataset_path": "samples/commons/github_events.json",
            "dataset_sha256": "a" * 64,
            "parameters": {},
        }
    ]
    experiment["variants"] = [
        {
            "id": "scanner8-library",
            "name": "Scanner8 (library)",
            "description": "baseline variant",
            "comparison_role": "baseline",
            "lifecycle_status": "approved",
            "measurement_contract_id": "structural-scan-v1",
            "benchmark_name_pattern": "BM_Scanner8_{dataset}",
        },
        {
            "id": "scanner64-library",
            "name": "Scanner64 (library)",
            "description": "candidate variant",
            "comparison_role": "candidate",
            "lifecycle_status": "approved",
            "measurement_contract_id": "structural-scan-v1",
            "benchmark_name_pattern": "BM_Scanner64_{dataset}",
        },
    ]

    run_dir = tmp_path / "runs" / resolved_run_id
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    (raw_dir / "scanner_benchmark.json").write_text(
        json.dumps({"benchmarks": benchmark_rows}), encoding="utf-8"
    )
    logs = run_dir / "logs"
    logs.mkdir()
    (logs / "03-run.stdout.log").write_text("ok\n", encoding="utf-8")

    snapshot = _snapshot(document)
    manifest = {
        "manifest_schema_version": "1.0",
        "run_id": resolved_run_id,
        "experiment_id": experiment_id,
        "registry_path": str(EXPERIMENT_PATH),
        "registry_file_sha256": sha256_file(EXPERIMENT_PATH),
        "definition_snapshot": snapshot,
        "definition_sha256": definition_sha256(snapshot),
        "started_at": "2026-01-01T00:00:00.000Z",
        "finished_at": "2026-01-01T00:01:00.000Z",
        "status": "COMPLETED",
        "verification_status": "PENDING",
        "source": {"git_commit": "b" * 40, "worktree_clean": True, "repository_root": "."},
        "environment": {"platform": "test"},
        "execution": {"mode": "native", "preset": "native-release", "env_passthrough": {}},
        "inputs": [
            {
                "workload_id": "github-events",
                "declared_path": "samples/commons/github_events.json",
                "resolved_path": "samples/commons/github_events.json",
                "sha256": "a" * 64,
                "size_bytes": 323229,
                "matches_definition": True,
            }
        ],
        "gates": [],
        "build": {},
        "execution_records": [
            {
                "sequence_number": 3,
                "purpose": "run",
                "command": ["scanner_benchmark"],
                "cwd": ".",
                "started_at": "2026-01-01T00:00:30.000Z",
                "finished_at": "2026-01-01T00:01:00.000Z",
                "status": "COMPLETED",
                "exit_code": 0,
                "error": None,
                "stdout_path": "logs/03-run.stdout.log",
                "stderr_path": "logs/03-run.stderr.log",
                "produced_outputs": ["raw/scanner_benchmark.json"],
                "required": True,
            }
        ],
        "expected_outputs": ["raw/scanner_benchmark.json"],
        "missing_expected_outputs": [],
        "artifacts": [],
        "notes": [],
    }
    (logs / "03-run.stderr.log").write_text("", encoding="utf-8")
    from ze_research.manifest import collect_artifacts

    manifest["artifacts"] = collect_artifacts(run_dir)
    manifest_path = run_dir / "manifest.json"
    write_json_atomic(manifest_path, manifest)
    return manifest_path


def _rows(name: str, times: list[float], unit: str = "ns") -> list[dict]:
    return [
        {
            "name": name,
            "run_type": "iteration",
            "iterations": 1000,
            "real_time": value,
            "cpu_time": value,
            "time_unit": unit,
            "bytes_per_second": 1.0 / (value / 1e9) if value else None,
            "error_occurred": False,
        }
        for value in times
    ]


def test_report_pair_comparison_uses_recorded_inputs(tmp_path):
    database = tmp_path / "research.sqlite"
    manifest_path = _seed_run(
        tmp_path,
        benchmark_rows=_rows("BM_Scanner8_GithubEvents", [20.0, 22.0, 24.0])
        + _rows("BM_Scanner64_GithubEvents", [10.0, 12.0, 14.0]),
    )
    result = ingest_manifest(manifest_path, database)
    assert result["status"] == "IMPORTED"
    assert result["result_counts"]["benchmark_results"] == 6

    report = build_report(
        database_path=database,
        experiment_id="scanner-width-comparison",
        pair=("scanner8-library", "scanner64-library"),
    )
    assert report["observation_count"] == 2
    compared = [item for item in report["comparisons"] if item["status"] == "COMPARED"]
    assert len(compared) == 1
    comparison = compared[0]
    assert comparison["baseline_median_real_time"] == 22.0
    assert comparison["candidate_median_real_time"] == 12.0
    assert comparison["ratio_candidate_over_baseline"] == pytest.approx(12.0 / 22.0)
    assert comparison["candidate_is_faster"] is True
    # The dataset digested at run time, not recomputed at import time.
    assert comparison["dataset_sha256"] == "a" * 64


def test_report_refuses_incomplete_pair(tmp_path):
    database = tmp_path / "research.sqlite"
    manifest_path = _seed_run(
        tmp_path,
        benchmark_rows=_rows("BM_Scanner8_GithubEvents", [20.0, 22.0]),
    )
    ingest_manifest(manifest_path, database)
    report = build_report(
        database_path=database,
        experiment_id="scanner-width-comparison",
        pair=("scanner8-library", "scanner64-library"),
    )
    assert [item["status"] for item in report["comparisons"]] == ["INCOMPLETE_PAIR"]


def test_report_refuses_unit_mismatch(tmp_path):
    database = tmp_path / "research.sqlite"
    manifest_path = _seed_run(
        tmp_path,
        benchmark_rows=_rows("BM_Scanner8_GithubEvents", [20000.0, 22000.0], unit="ns")
        + _rows("BM_Scanner64_GithubEvents", [12.0, 14.0], unit="us"),
    )
    ingest_manifest(manifest_path, database)
    report = build_report(
        database_path=database,
        experiment_id="scanner-width-comparison",
        pair=("scanner8-library", "scanner64-library"),
    )
    assert [item["status"] for item in report["comparisons"]] == ["UNIT_MISMATCH"]


def test_report_surfaces_unmapped_rows(tmp_path):
    database = tmp_path / "research.sqlite"
    # A name that does not match either declared pattern must stay visible.
    manifest_path = _seed_run(
        tmp_path,
        benchmark_rows=_rows("BM_Scanner8_UnknownDataset", [20.0, 22.0]),
    )
    ingest_manifest(manifest_path, database)
    report = build_report(database_path=database, experiment_id="scanner-width-comparison")
    assert report["unmapped_rows"]
    assert report["unmapped_rows"][0]["mapping_status"] in {"UNMAPPED", "AMBIGUOUS"}


def test_export_carries_provenance(tmp_path):
    database = tmp_path / "research.sqlite"
    manifest_path = _seed_run(
        tmp_path,
        benchmark_rows=_rows("BM_Scanner8_GithubEvents", [20.0]),
    )
    ingest_manifest(manifest_path, database)
    payload = export_payload(database_path=database, experiment_id="scanner-width-comparison")
    assert payload["experiment_count"] == 1
    observation = payload["experiments"][0]["observations"][0]
    assert observation["manifest_sha256"]
    assert observation["definition_sha256"]
    assert observation["dataset_sha256"] == "a" * 64


def test_record_analysis_is_idempotent(tmp_path):
    database = tmp_path / "research.sqlite"
    manifest_path = _seed_run(
        tmp_path,
        benchmark_rows=_rows("BM_Scanner8_GithubEvents", [20.0, 22.0])
        + _rows("BM_Scanner64_GithubEvents", [10.0, 12.0]),
    )
    ingest_manifest(manifest_path, database)
    report = build_report(
        database_path=database,
        experiment_id="scanner-width-comparison",
        pair=("scanner8-library", "scanner64-library"),
    )
    output = tmp_path / "report.json"
    first = record_analysis(database_path=database, report=report, output_path=output)
    second = record_analysis(database_path=database, report=report, output_path=output)
    assert first["status"] == "RECORDED"
    assert second["status"] == "ALREADY_RECORDED"
    with connect(database) as connection:
        rows = connection.execute("SELECT COUNT(*) FROM analysis_inputs").fetchone()[0]
    assert rows == 1


def test_export_skips_experiments_without_benchmark_rows(tmp_path):
    """'export everything' must not crash on a correctness experiment."""
    database = tmp_path / "research.sqlite"
    ingest_manifest(
        _seed_run(
            tmp_path,
            benchmark_rows=_rows("BM_Scanner8_GithubEvents", [20.0]),
        ),
        database,
    )
    # A correctness experiment legitimately has no benchmark rows at all.
    ingest_manifest(
        _seed_run(
            tmp_path,
            benchmark_rows=[],
            experiment_id="correctness-gtest-suite",
            run_id="seed-run-0002",
        ),
        database,
    )

    payload = export_payload(database_path=database)
    exported = {section["experiment_id"] for section in payload["experiments"]}
    assert "scanner-width-comparison" in exported
    assert "correctness-gtest-suite" not in exported
    skipped = {entry["experiment_id"] for entry in payload["skipped_experiments"]}
    assert "correctness-gtest-suite" in skipped
    assert payload["experiment_count"] == len(exported)


def test_report_can_be_restricted_to_specific_runs(tmp_path):
    database = tmp_path / "research.sqlite"
    manifest_path = _seed_run(
        tmp_path,
        benchmark_rows=_rows("BM_Scanner8_GithubEvents", [20.0])
        + _rows("BM_Scanner64_GithubEvents", [10.0]),
    )
    ingest_manifest(manifest_path, database)

    full = build_report(database_path=database, experiment_id="scanner-width-comparison")
    assert full["run_ids"] == ["seed-run-0001"]

    report = build_report(
        database_path=database,
        experiment_id="scanner-width-comparison",
        pair=("scanner8-library", "scanner64-library"),
        run_ids=["seed-run-0001"],
    )
    assert report["run_ids"] == ["seed-run-0001"]
    assert report["definition_versions_in_report"] == 1
    assert [item["status"] for item in report["comparisons"]] == ["COMPARED"]

    with pytest.raises(ReportError):
        build_report(
            database_path=database,
            experiment_id="scanner-width-comparison",
            run_ids=["no-such-run"],
        )


def test_report_flags_mixed_definition_versions(tmp_path):
    database = tmp_path / "research.sqlite"
    manifest_path = _seed_run(
        tmp_path,
        benchmark_rows=_rows("BM_Scanner8_GithubEvents", [20.0]),
    )
    ingest_manifest(manifest_path, database)
    report = build_report(database_path=database, experiment_id="scanner-width-comparison")
    assert report["definition_sha256_by_run"]["seed-run-0001"]
    assert report["definition_versions_in_report"] == 1


def test_report_allows_a_javascript_only_experiment(tmp_path):
    """A JS experiment has no nanosecond rows and must still report."""
    database = tmp_path / "research.sqlite"
    ingest_manifest(
        _seed_run(
            tmp_path,
            benchmark_rows=[],
            experiment_id="js-parse-saq",
            run_id="seed-run-0003",
        ),
        database,
    )
    with connect(database) as connection:
        execution_id = connection.execute(
            "SELECT execution_id FROM executions WHERE run_id=?",
            ("seed-run-0003",),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO js_parse_results(
                execution_id, source_artifact_id, run_id, payload_id, parser,
                operation, median_us_per_op, time_unit, raw_row_json)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                execution_id, None, "seed-run-0003", "assistants",
                "JSON.parse", "parse_only", 1.1, "us", "{}",
            ),
        )
        connection.commit()

    report = build_report(
        database_path=database, experiment_id="js-parse-saq"
    )
    assert report["observations"] == []
    assert [row["payload_id"] for row in report["js_parse"]] == ["assistants"]
    assert report["js_parse"][0]["median_us_per_op"] == 1.1


def test_report_run_filter_applies_to_javascript_rows(tmp_path):
    """--run-ids must validate and filter JS rows too, not just nanosecond ones."""
    database = tmp_path / "research.sqlite"
    ingest_manifest(
        _seed_run(
            tmp_path,
            benchmark_rows=[],
            experiment_id="js-parse-saq",
            run_id="seed-run-0004",
        ),
        database,
    )
    with connect(database) as connection:
        execution_id = connection.execute(
            "SELECT execution_id FROM executions WHERE run_id=?",
            ("seed-run-0004",),
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO js_parse_results(
                execution_id, source_artifact_id, run_id, payload_id, parser,
                operation, median_us_per_op, time_unit, raw_row_json)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (execution_id, None, "seed-run-0004", "reports",
             "JSON.parse", "parse_only", 2.4, "us", "{}"),
        )
        connection.commit()

    report = build_report(
        database_path=database,
        experiment_id="js-parse-saq",
        run_ids=["seed-run-0004"],
    )
    assert [row["payload_id"] for row in report["js_parse"]] == ["reports"]
    assert report["run_ids"] == ["seed-run-0004"]

    with pytest.raises(ReportError):
        build_report(
            database_path=database,
            experiment_id="js-parse-saq",
            run_ids=["absent-run"],
        )


def test_report_requires_ingested_rows(tmp_path):
    database = tmp_path / "research.sqlite"
    from ze_research.database import migrate

    migrate(database)
    with pytest.raises(ReportError):
        build_report(
            database_path=database, experiment_id="scanner-width-comparison"
        )
