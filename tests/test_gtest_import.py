"""Google Test JSON import must handle the layout gtest actually writes.

Modern gtest puts the cases in ``testsuites[].testsuite[]`` and keeps ``tests``
as a count. Reading ``tests`` as the case list silently imports zero results,
which would make a correctness experiment look valid and empty.
"""

import json
from pathlib import Path

from ze_research.database import connect
from ze_research.ingest import _test_status, _walk_gtest_cases, ingest_manifest
from ze_research.manifest import sha256_file, write_json_atomic
from ze_research.registry import definition_sha256, load_yaml


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = ROOT / "research" / "experiments" / "correctness" / "gtest-suite.yaml"

# Payload shaped like a real release build: two suites, one disabled case,
# one failing case with a failure entry.
GTEST_PAYLOAD = {
    "tests": 4,
    "failures": 1,
    "disabled": 1,
    "errors": 0,
    "timestamp": "2026-01-01T00:00:00Z",
    "time": "0.01s",
    "name": "probe_test",
    "testsuites": [
        {
            "name": "ScannerTest",
            "tests": 4,
            "failures": 1,
            "disabled": 1,
            "testsuite": [
                {
                    "name": "StandardJsonPayload",
                    "classname": "ScannerTest",
                    "file": "main.cpp",
                    "line": 49,
                    "status": "RUN",
                    "result": "COMPLETED",
                    "time": "0.001s",
                },
                {
                    "name": "RejectsInvalidUtf8",
                    "classname": "ScannerTest",
                    "status": "RUN",
                    "time": "0.002s",
                    "failures": [
                        {"failure": "expected rejection, got acceptance", "type": ""}
                    ],
                },
                {
                    "name": "LegacyFixtureCase",
                    "classname": "LegacySuite",
                    "status": "NOTRUN",
                    "time": "0s",
                },
                {
                    "name": "DisabledCase",
                    "classname": "ScannerTest",
                    "status": "RUN",
                    "time": "0s",
                    "result": "DISABLED",
                },
            ],
        }
    ],
}


def test_walk_gtest_cases_reads_the_real_layout():
    records = _walk_gtest_cases(GTEST_PAYLOAD)
    assert [case["name"] for _, case in records] == [
        "StandardJsonPayload",
        "RejectsInvalidUtf8",
        "LegacyFixtureCase",
        "DisabledCase",
    ]
    assert all(suite == "ScannerTest" for suite, _ in records)


def test_test_status_classification():
    by_name = {case["name"]: case for _, case in _walk_gtest_cases(GTEST_PAYLOAD)}
    assert _test_status(by_name["StandardJsonPayload"])[0] == "PASSED"
    failed, text = _test_status(by_name["RejectsInvalidUtf8"])
    assert failed == "FAILED"
    assert "expected rejection" in text
    # NOTRUN means the case never executed, so it is skipped, not passed.
    assert _test_status(by_name["LegacyFixtureCase"])[0] == "SKIPPED"
    assert _test_status(by_name["DisabledCase"])[0] == "DISABLED"


def _build_run_dir(tmp_path: Path) -> Path:
    document = load_yaml(EXPERIMENT_PATH)
    experiment = document["experiment"]
    experiment["correctness_gate"] = None
    experiment["workloads"] = []
    # One variant with a literal pattern and a single workload keeps the
    # benchmark spec unambiguous; test rows do not use the pattern anyway.
    experiment["status"] = "approved_for_run"

    run_dir = tmp_path / "runs" / "test-run-0001"
    (run_dir / "raw" / "tests").mkdir(parents=True)
    (run_dir / "logs").mkdir(parents=True)
    (run_dir / "executables").mkdir(parents=True)
    (run_dir / "executables" / "scanner_test").write_bytes(b"fixture-binary\n")
    (run_dir / "raw" / "tests" / "scanner_test.json").write_text(
        json.dumps(GTEST_PAYLOAD), encoding="utf-8"
    )
    (run_dir / "logs" / "03-test-scanner.stdout.log").write_text("ok\n", encoding="utf-8")
    (run_dir / "logs" / "03-test-scanner.stderr.log").write_text("", encoding="utf-8")

    snapshot = {"schema_version": document.get("schema_version"), "experiment": experiment}
    fingerprint = definition_sha256(snapshot)
    manifest = {
        "manifest_schema_version": "1.0",
        "run_id": "test-run-0001",
        "experiment_id": experiment["id"],
        "registry_path": str(EXPERIMENT_PATH),
        "registry_file_sha256": sha256_file(EXPERIMENT_PATH),
        "definition_snapshot": snapshot,
        "definition_sha256": fingerprint,
        "started_at": "2026-01-01T00:00:00.000Z",
        "finished_at": "2026-01-01T00:00:10.000Z",
        "status": "COMPLETED",
        "verification_status": "PENDING",
        "source": {"git_commit": "c" * 40, "worktree_clean": True, "repository_root": "."},
        "environment": {"platform": "test"},
        "execution": {"mode": "native", "preset": "native-release", "env_passthrough": {}},
        "inputs": [],
        "gates": [],
        "build": {
            "executables": [
                {
                    "path": str((run_dir / "executables" / "scanner_test").resolve()),
                    "sha256": sha256_file(run_dir / "executables" / "scanner_test"),
                    "size_bytes": (run_dir / "executables" / "scanner_test").stat().st_size,
                    "declared_template": "{build_dir}/tests/scanner_test",
                }
            ]
        },
        "execution_records": [
            {
                "sequence_number": 3,
                "purpose": "test-scanner",
                "command": ["scanner_test"],
                "cwd": ".",
                "started_at": "2026-01-01T00:00:01.000Z",
                "finished_at": "2026-01-01T00:00:10.000Z",
                "status": "COMPLETED",
                "exit_code": 0,
                "error": None,
                "stdout_path": "logs/03-test-scanner.stdout.log",
                "stderr_path": "logs/03-test-scanner.stderr.log",
                "produced_outputs": ["raw/tests/scanner_test.json"],
            }
        ],
        "expected_outputs": ["raw/tests/scanner_test.json"],
        "missing_expected_outputs": [],
        "artifacts": [],
        "notes": [],
    }
    from ze_research.manifest import collect_artifacts

    manifest["artifacts"] = collect_artifacts(run_dir)
    manifest_path = run_dir / "manifest.json"
    write_json_atomic(manifest_path, manifest)
    return manifest_path


def test_gtest_run_is_imported_case_by_case(tmp_path):
    database = tmp_path / "research.sqlite"
    manifest_path = _build_run_dir(tmp_path)
    result = ingest_manifest(manifest_path, database)
    assert result["status"] == "IMPORTED"
    assert result["result_counts"]["test_results"] == 4

    with connect(database) as connection:
        rows = connection.execute(
            "SELECT suite_name, test_name, status, failure_text FROM test_results "
            "ORDER BY test_result_id"
        ).fetchall()
    assert [row["test_name"] for row in rows] == [
        "StandardJsonPayload",
        "RejectsInvalidUtf8",
        "LegacyFixtureCase",
        "DisabledCase",
    ]
    assert [row["status"] for row in rows] == ["PASSED", "FAILED", "SKIPPED", "DISABLED"]
    assert all(row["suite_name"] == "ScannerTest" for row in rows)
    assert "expected rejection" in [
        row["failure_text"] for row in rows if row["status"] == "FAILED"
    ][0]

    # Every result is attributed to the step that actually produced the file.
    with connect(database) as connection:
        executions = {
            row["execution_id"]: row["purpose"]
            for row in connection.execute("SELECT execution_id, purpose FROM executions")
        }
    assert executions == {"test-run-0001-e003": "test-scanner"}
    with connect(database) as connection:
        attributed = {
            row["execution_id"]
            for row in connection.execute("SELECT execution_id FROM test_results")
        }
    assert attributed == set(executions)


def test_empty_gtest_layout_imports_nothing(tmp_path):
    """A zero-case run must import zero rows, not invent them."""
    database = tmp_path / "research.sqlite"
    manifest_path = _build_run_dir(tmp_path)
    payload = json.loads(
        (manifest_path.parent / "raw" / "tests" / "scanner_test.json").read_text("utf-8")
    )
    payload["testsuites"] = [{"name": "EmptySuite", "tests": 0}]
    (manifest_path.parent / "raw" / "tests" / "scanner_test.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    # Rehash the artifacts so the manifest stays consistent.
    from ze_research.manifest import collect_artifacts

    manifest = json.loads(manifest_path.read_text("utf-8"))
    manifest["artifacts"] = collect_artifacts(manifest_path.parent)
    write_json_atomic(manifest_path, manifest)

    result = ingest_manifest(manifest_path, database)
    assert result["result_counts"]["test_results"] == 0
