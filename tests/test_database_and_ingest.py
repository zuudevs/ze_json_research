import json
from pathlib import Path

from ze_research.database import (
    MIGRATIONS_DIR,
    connect,
    database_status,
    migrate,
    persist_definition,
)
from ze_research.ingest import ingest_manifest
from ze_research.manifest import collect_artifacts, sha256_file, verify_manifest, write_json_atomic
from ze_research.registry import definition_sha256, load_yaml


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = ROOT / "research" / "experiments" / "scanner-width.yaml"


def _expected_migrations() -> list[int]:
    return sorted(
        int(path.name.split("_", 1)[0])
        for path in MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")
    )


def test_migration_is_idempotent_and_database_is_healthy(tmp_path):
    database = tmp_path / "database" / "research.sqlite"
    expected = _expected_migrations()
    assert expected, "setidaknya satu migrasi harus tersedia"
    assert migrate(database) == expected
    assert migrate(database) == []
    status = database_status(database)
    assert status["integrity_check"] == "ok"
    assert status["foreign_key_violations"] == []
    assert [entry["version"] for entry in status["migrations"]] == expected


def _create_manifest(tmp_path: Path) -> Path:
    run_dir = tmp_path / "runs" / "run-test-001"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    raw_result = {
        "context": {"host_name": "test-host", "library_build_type": "Release"},
        "benchmarks": [
            {
                "name": "BM_Scanner8_GithubEvents",
                "run_type": "iteration",
                "iterations": 5,
                "real_time": 120.0,
                "cpu_time": 118.0,
                "time_unit": "ns",
                "bytes_per_second": 100000.0,
                "items_per_second": None,
            }
        ],
    }
    raw_path = raw_dir / "scanner.json"
    raw_path.write_text(json.dumps(raw_result), encoding="utf-8")
    _, definition = (EXPERIMENT_PATH, load_yaml(EXPERIMENT_PATH))
    snapshot = {
        "schema_version": definition["schema_version"],
        "experiment": definition["experiment"],
    }
    manifest = {
        "manifest_schema_version": "1.0",
        "run_id": "run-test-001",
        "experiment_id": "scanner-width-comparison",
        "definition_snapshot": snapshot,
        "definition_sha256": definition_sha256(snapshot),
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:00:01Z",
        "status": "COMPLETED",
        "source": {"repository_root": str(ROOT), "git_commit": "a" * 40, "worktree_clean": True},
        "environment": {"platform": "test"},
        "build": {},
        "execution_records": [
            {
                "sequence_number": 1,
                "purpose": "run",
                "command": ["scanner_benchmark"],
                "cwd": str(ROOT),
                "started_at": "2026-01-01T00:00:00Z",
                "finished_at": "2026-01-01T00:00:01Z",
                "status": "COMPLETED",
                "exit_code": 0,
                "stdout_path": None,
                "stderr_path": None,
            }
        ],
        "expected_outputs": ["raw/scanner.json"],
        "missing_expected_outputs": [],
    }
    manifest["artifacts"] = collect_artifacts(run_dir)
    manifest_path = run_dir / "manifest.json"
    write_json_atomic(manifest_path, manifest)
    return manifest_path


def test_manifest_verification_detects_artifact_tampering(tmp_path):
    manifest_path = _create_manifest(tmp_path)
    assert verify_manifest(manifest_path)["valid"] is True

    raw_path = manifest_path.parent / "raw" / "scanner.json"
    raw_path.write_text(raw_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    result = verify_manifest(manifest_path)
    assert result["valid"] is False
    assert any("SHA-256 tidak cocok" in error for error in result["errors"])


def test_import_is_idempotent_and_maps_benchmark(tmp_path):
    manifest_path = _create_manifest(tmp_path)
    database = tmp_path / "database" / "research.sqlite"

    first = ingest_manifest(manifest_path, database)
    assert first["status"] == "IMPORTED"
    assert first["result_counts"]["benchmark_results"] == 1

    second = ingest_manifest(manifest_path, database)
    assert second["status"] == "ALREADY_IMPORTED"

    with connect(database) as connection:
        run_count = connection.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
        result = connection.execute(
            "SELECT benchmark_name_raw, mapping_status, benchmark_spec_id FROM benchmark_results"
        ).fetchone()
    assert run_count == 1
    assert result["benchmark_name_raw"] == "BM_Scanner8_GithubEvents"
    assert result["mapping_status"] == "MAPPED"
    assert result["benchmark_spec_id"]


def test_import_refuses_tampered_artifact(tmp_path):
    manifest_path = _create_manifest(tmp_path)
    raw_path = manifest_path.parent / "raw" / "scanner.json"
    raw_path.write_text("{}", encoding="utf-8")
    database = tmp_path / "database" / "research.sqlite"

    try:
        ingest_manifest(manifest_path, database)
        assert False, "Tampered artifact must be rejected."
    except RuntimeError as exc:
        assert "gagal verifikasi" in str(exc)


def test_restricted_workloads_produce_one_pattern_per_variant_and_workload(tmp_path):
    """Two workloads sharing a token must not collapse into one ambiguous spec.

    The scanner benchmark really does use the Synth name slot for two different
    payloads. Restricting each variant to the workloads it implements keeps
    every stored pattern unambiguous.
    """
    database = tmp_path / "research.sqlite"
    migrate(database)
    document = load_yaml(EXPERIMENT_PATH)
    experiment = document["experiment"]
    experiment["variants"][0]["workloads"] = [
        "synthetic-256k", "replica-jeopardy-via-synth-slot",
    ]
    experiment["variants"][1]["workloads"] = []

    snapshot = {"schema_version": document.get("schema_version"), "experiment": experiment}
    with connect(database) as connection:
        version_id = persist_definition(connection, snapshot)

    with connect(database) as connection:
        rows = connection.execute(
            "SELECT workload_id, name_pattern, algorithm_id FROM benchmark_specs "
            "WHERE experiment_version_id=? AND algorithm_id='scanner8-library' "
            "ORDER BY workload_id", (version_id,)
        ).fetchall()
    # Both specs exist, one per workload, so a row maps to exactly one target.
    assert [(row["workload_id"], row["name_pattern"]) for row in rows] == [
        ("replica-jeopardy-via-synth-slot", "BM_Scanner8_Synth"),
        ("synthetic-256k", "BM_Scanner8_Synth"),
    ]
