"""Correctness gates must fail closed: silence is not acceptance."""

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from ze_research.database import connect, migrate
from ze_research.gates import GateError, evaluate_gates, find_gate_run
from ze_research.registry import load_yaml
from ze_research.runner import run_experiment


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = ROOT / "research" / "experiments" / "scanner-width.yaml"
CORRECTNESS_PATH = ROOT / "research" / "experiments" / "correctness" / "gtest-suite.yaml"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "ze_json"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.name", "Research Test")
    _git(source, "config", "user.email", "research-test@example.invalid")
    (source / "README.md").write_text("test source\n", encoding="utf-8")
    commons = source / "samples" / "commons"
    commons.mkdir(parents=True)
    (commons / "github_events.json").write_bytes(b'{"events":[]}\n')
    _git(source, "add", "-A")
    _git(source, "commit", "-m", "initial source")
    return source


def _stub_execution(document: dict) -> None:
    execution = document["experiment"]["execution"]
    execution.pop("configure_command", None)
    execution.pop("build_command", None)
    execution.pop("executable_path", None)
    execution["steps"] = [
        {
            "id": "run",
            "command": [
                sys.executable, "-c",
                "import json; from pathlib import Path; "
                "p=Path(r'{run_dir}/raw/scanner_benchmark.json'); "
                "p.parent.mkdir(parents=True, exist_ok=True); "
                "p.write_text(json.dumps(dict(benchmarks=[])), encoding='utf-8')",
            ],
        }
    ]


def _commit(source: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source, capture_output=True, text=True, check=True
    ).stdout.strip()


def _seed_gate_run(
    *, database_path: Path, experiment_id: str, commit: str, verification_status: str
) -> str:
    """Insert a research run row the way ingest would, without re-running it."""
    migrate(database_path)
    version_id = f"{experiment_id}@seed0000000000"
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """INSERT OR IGNORE INTO experiments(experiment_id, title, status, categories_json)
               VALUES(?,?,?,?)""",
            (experiment_id, "seeded", "approved_for_run", "[]"),
        )
        connection.execute(
            """INSERT OR IGNORE INTO experiment_versions(
                experiment_version_id, experiment_id, definition_sha256, schema_version,
                purpose, motivation, research_question, hypotheses_json, assumptions_json,
                limitations_json, protocol_json, definition_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                version_id, experiment_id, "0" * 64, "1", "seed", None, "seed?",
                "[]", "[]", "[]", '{"design":"seed"}', "{}",
            ),
        )
        run_id = f"seed-{experiment_id}-{verification_status.lower()}"
        connection.execute(
            """INSERT OR IGNORE INTO research_runs(
                run_id, experiment_version_id, started_at, finished_at, status,
                verification_status, manifest_path, manifest_sha256, source_json,
                environment_json, build_json, definition_sha256, gates_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, version_id, "2026-01-01T00:00:00.000Z", "2026-01-01T00:00:01.000Z",
                "COMPLETED", verification_status, "manifest.json", "0" * 64,
                json.dumps({"git_commit": commit}), "{}", "{}", "0" * 64, "[]",
            ),
        )
        connection.commit()
    return run_id


def test_gate_blocks_run_without_verified_correctness_run(tmp_path):
    source = _source(tmp_path)
    data_dir = tmp_path / "research-data"
    (data_dir / "database").mkdir(parents=True)
    database_path = data_dir / "database" / "ze_json_research.sqlite"

    document = load_yaml(EXPERIMENT_PATH)
    document["experiment"]["execution"]["require_clean_worktree"] = False
    document["experiment"]["workloads"] = []
    _stub_execution(document)
    declaration = document["experiment"]["correctness_gate"]

    # No database rows at all: the run must be refused, not waved through.
    with pytest.raises(GateError) as excinfo:
        evaluate_gates(
            document["experiment"],
            source_info={"git_commit": _commit(source), "worktree_clean": False},
            database_path=database_path,
            run_started_at="2026-01-01T00:00:00.000Z",
        )
    assert "Tidak ada run" in str(excinfo.value)
    assert declaration["experiment_id"] in str(excinfo.value)


def test_gate_accepts_verified_run_on_same_commit(tmp_path):
    source = _source(tmp_path)
    data_dir = tmp_path / "research-data"
    database_path = data_dir / "database" / "ze_json_research.sqlite"
    commit = _commit(source)
    run_id = _seed_gate_run(
        database_path=database_path,
        experiment_id="correctness-gtest-suite",
        commit=commit,
        verification_status="VERIFIED",
    )

    document = load_yaml(EXPERIMENT_PATH)
    records = evaluate_gates(
        document["experiment"],
        source_info={"git_commit": commit, "worktree_clean": True},
        database_path=database_path,
        run_started_at="2026-01-01T00:00:00.000Z",
    )
    assert len(records) == 1
    record = records[0]
    assert record["satisfied"] is True
    assert record["matched_run"]["run_id"] == run_id
    assert record["matched_run"]["git_commit"] == commit
    assert record["required_commit"] == commit


def test_gate_rejects_verified_run_on_different_commit(tmp_path):
    source = _source(tmp_path)
    data_dir = tmp_path / "research-data"
    database_path = data_dir / "database" / "ze_json_research.sqlite"
    _seed_gate_run(
        database_path=database_path,
        experiment_id="correctness-gtest-suite",
        commit="0" * 40,
        verification_status="VERIFIED",
    )

    document = load_yaml(EXPERIMENT_PATH)
    with pytest.raises(GateError):
        evaluate_gates(
            document["experiment"],
            source_info={"git_commit": _commit(source), "worktree_clean": True},
            database_path=database_path,
            run_started_at="2026-01-01T00:00:00.000Z",
        )


def test_gate_rejects_unverified_correctness_run(tmp_path):
    source = _source(tmp_path)
    data_dir = tmp_path / "research-data"
    database_path = data_dir / "database" / "ze_json_research.sqlite"
    _seed_gate_run(
        database_path=database_path,
        experiment_id="correctness-gtest-suite",
        commit=_commit(source),
        verification_status="FAILED",
    )

    document = load_yaml(EXPERIMENT_PATH)
    with pytest.raises(GateError):
        evaluate_gates(
            document["experiment"],
            source_info={"git_commit": _commit(source), "worktree_clean": True},
            database_path=database_path,
            run_started_at="2026-01-01T00:00:00.000Z",
        )


def test_find_gate_run_returns_none_when_database_missing(tmp_path):
    assert find_gate_run(
        database_path=tmp_path / "absent.sqlite",
        experiment_id="correctness-gtest-suite",
        required_verification_status="VERIFIED",
        required_commit="0" * 40,
    ) is None


def test_gate_is_recorded_in_the_run_manifest(tmp_path):
    source = _source(tmp_path)
    data_dir = tmp_path / "research-data"
    database_path = data_dir / "database" / "ze_json_research.sqlite"
    commit = _commit(source)
    gate_run_id = _seed_gate_run(
        database_path=database_path,
        experiment_id="correctness-gtest-suite",
        commit=commit,
        verification_status="VERIFIED",
    )

    document = load_yaml(EXPERIMENT_PATH)
    document["experiment"]["execution"]["require_clean_worktree"] = False
    document["experiment"]["workloads"] = []
    _stub_execution(document)

    manifest_path = run_experiment(
        document,
        source_dir=source,
        data_dir=data_dir,
        registry_path=EXPERIMENT_PATH,
        database_path=database_path,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "COMPLETED"
    assert manifest["gates"][0]["matched_run"]["run_id"] == gate_run_id
