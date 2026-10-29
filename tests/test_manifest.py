from pathlib import Path

from ze_research.manifest import collect_artifacts, sha256_file, verify_manifest, write_json_atomic
from ze_research.registry import definition_sha256


def test_manifest_round_trip_and_hash_verification(tmp_path):
    run_dir = tmp_path / "run"
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    raw_path = raw_dir / "result.json"
    raw_path.write_text('{"ok": true}', encoding="utf-8")

    snapshot = {"schema_version": 1, "experiment": {"id": "test"}}
    manifest = {
        "run_id": "test-run",
        "experiment_id": "test",
        "definition_snapshot": snapshot,
        "definition_sha256": definition_sha256(snapshot),
        "status": "COMPLETED",
        "expected_outputs": ["raw/result.json"],
        "artifacts": collect_artifacts(run_dir),
    }
    manifest_path = run_dir / "manifest.json"
    write_json_atomic(manifest_path, manifest)

    assert sha256_file(raw_path) == manifest["artifacts"][0]["sha256"]
    result = verify_manifest(manifest_path)
    assert result["valid"] is True
    assert result["verification_status"] == "VERIFIED"


def test_manifest_rejects_traversal_path(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    snapshot = {"schema_version": 1, "experiment": {"id": "test"}}
    manifest_path = run_dir / "manifest.json"
    write_json_atomic(manifest_path, {
        "run_id": "bad-path",
        "experiment_id": "test",
        "definition_snapshot": snapshot,
        "definition_sha256": definition_sha256(snapshot),
        "expected_outputs": [],
        "artifacts": [{"path": "../escape.json", "sha256": "0" * 64, "size_bytes": 1}],
    })
    result = verify_manifest(manifest_path)
    assert result["valid"] is False
    assert any("tidak aman" in error for error in result["errors"])
