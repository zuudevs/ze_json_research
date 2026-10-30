import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ze_research.manifest import verify_manifest
from ze_research.registry import load_yaml
from ze_research.runner import RunnerError, run_experiment


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_PATH = ROOT / "research" / "experiments" / "scanner-width.yaml"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_runner_builds_and_writes_all_output_outside_repositories(tmp_path):
    source = tmp_path / "ze_json"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.name", "Research Test")
    _git(source, "config", "user.email", "research-test@example.invalid")
    (source / "README.md").write_text("test source\n", encoding="utf-8")
    # The runner hashes every declared workload dataset before executing, so the
    # fixture must provide the bytes the definition promises.
    dataset = source / "samples" / "commons"
    dataset.mkdir(parents=True)
    dataset_payload = b'{"events":[]}\n'
    (dataset / "github_events.json").write_bytes(dataset_payload)
    _git(source, "add", "-A")
    _git(source, "commit", "-m", "initial source")
    data_dir = tmp_path / "research-data"
    _, document = (EXPERIMENT_PATH, load_yaml(EXPERIMENT_PATH))
    document["experiment"]["correctness_gate"] = None
    document["experiment"]["execution"]["executable_path"] = None
    # Keep the fixture to one dataset-backed workload: this test exercises the
    # runner mechanics, not the full dataset catalog.
    document["experiment"]["workloads"] = [
        {
            "id": "github-events",
            "name": "GitHub Events JSON dataset",
            "benchmark_token": "GithubEvents",
            "dataset_path": "samples/commons/github_events.json",
            "dataset_sha256": hashlib.sha256(dataset_payload).hexdigest(),
            "parameters": {},
        }
    ]
    document["experiment"]["execution"]["configure_command"] = [
        sys.executable, "-c", "print('configure')"
    ]
    document["experiment"]["execution"]["build_command"] = [
        sys.executable, "-c", "print('build')"
    ]
    document["experiment"]["execution"]["run_command"] = [
        sys.executable, "-c",
        "import json; from pathlib import Path; p=Path(r'{run_dir}/raw/scanner_benchmark.json'); "
        "p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(dict(benchmarks=[])), encoding='utf-8')",
    ]

    manifest_path = run_experiment(
        document, source_dir=source, data_dir=data_dir, registry_path=EXPERIMENT_PATH
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "COMPLETED"
    assert manifest_path.is_relative_to(data_dir)
    assert manifest["build"]["build_dir"].startswith(str(data_dir))
    assert verify_manifest(manifest_path)["valid"] is True
    assert not (source / "out").exists()
    assert not (ROOT / "out").exists()
    # The run captured the dataset itself, not a recomputation at import time.
    assert manifest["inputs"][0]["sha256"] == hashlib.sha256(dataset_payload).hexdigest()
    assert manifest["inputs"][0]["size_bytes"] == len(dataset_payload)
    assert (manifest_path.parent / "metadata" / "inputs.json").is_file()


def test_directory_workload_digest_matches_the_generator_convention(tmp_path):
    """A corpus digest must be comparable with dataset-digests.json.

    The generator names files relative to the repository root. Hashing the same
    tree with folder-relative names produces a different digest, which would
    make a reviewed definition un-runnable for no real reason.
    """
    source = _prepare_source(tmp_path, dataset=b"{}\n")
    corpus = source / "samples" / "corpus"
    corpus.mkdir()
    (corpus / "b.json").write_text("[]\n", encoding="utf-8")
    (corpus / "a.json").write_text("{}\n", encoding="utf-8")

    from ze_research.runner import _hash_tree

    tree = _hash_tree(corpus, source)
    assert tree["file_count"] == 2
    assert tree["kind"] == "directory"

    import hashlib as _hashlib

    a_payload, b_payload = b"payload-a\n", b"payload-b\n"
    (corpus / "a.json").write_bytes(a_payload)
    (corpus / "b.json").write_bytes(b_payload)

    tree = _hash_tree(corpus, source)
    assert tree["size_bytes"] == len(a_payload) + len(b_payload)
    formula = (
        "samples/corpus/a.json\t" + _hashlib.sha256(a_payload).hexdigest() + "\t%d\n" % len(a_payload)
        + "samples/corpus/b.json\t" + _hashlib.sha256(b_payload).hexdigest() + "\t%d" % len(b_payload)
    )
    assert tree["sha256"] == _hashlib.sha256(formula.encode("utf-8")).hexdigest()


def _prepare_source(tmp_path: Path, *, dataset: bytes) -> Path:
    source = tmp_path / "ze_json"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.name", "Research Test")
    _git(source, "config", "user.email", "research-test@example.invalid")
    (source / "README.md").write_text("test source\n", encoding="utf-8")
    commons = source / "samples" / "commons"
    commons.mkdir(parents=True)
    (commons / "github_events.json").write_bytes(dataset)
    _git(source, "add", "-A")
    _git(source, "commit", "-m", "initial source")
    return source


def _load_definition() -> dict:
    _, document = (EXPERIMENT_PATH, load_yaml(EXPERIMENT_PATH))
    document["experiment"]["correctness_gate"] = None
    # No dataset workloads by default: each test declares what it needs.
    document["experiment"]["workloads"] = []
    return document


def test_runner_rejects_dataset_drift(tmp_path):
    """A dataset that changed after review must abort the run."""
    original = b'{"events":[]}\n'
    source = _prepare_source(tmp_path, dataset=original)
    document = _load_definition()
    digest = hashlib.sha256(original).hexdigest()
    document["experiment"]["workloads"] = [
        {
            "id": "github-events",
            "name": "GitHub Events JSON dataset",
            "benchmark_token": "GithubEvents",
            "dataset_path": "samples/commons/github_events.json",
            "dataset_sha256": digest,
            "parameters": {},
        }
    ]
    (source / "samples" / "commons" / "github_events.json").write_bytes(b'{"events":[1,2,3]}\n')
    # Relax the worktree gate so this test isolates the dataset-drift check.
    document["experiment"]["execution"]["require_clean_worktree"] = False

    with pytest.raises(RunnerError) as excinfo:
        run_experiment(
            document,
            source_dir=source,
            data_dir=tmp_path / "research-data",
            registry_path=EXPERIMENT_PATH,
        )
    assert "berubah sejak definisi disetujui" in str(excinfo.value)


def test_runner_rejects_missing_dataset(tmp_path):
    source = _prepare_source(tmp_path, dataset=b"{}\n")
    document = _load_definition()
    document["experiment"]["workloads"] = [
        {
            "id": "github-events",
            "name": "GitHub Events JSON dataset",
            "benchmark_token": "GithubEvents",
            "dataset_path": "samples/commons/absent.json",
            "dataset_sha256": None,
            "parameters": {},
        }
    ]
    with pytest.raises(RunnerError) as excinfo:
        run_experiment(
            document,
            source_dir=source,
            data_dir=tmp_path / "research-data",
            registry_path=EXPERIMENT_PATH,
        )
    assert "tidak ditemukan" in str(excinfo.value)


def test_runner_executes_declared_steps_and_attributes_outputs(tmp_path):
    source = _prepare_source(tmp_path, dataset=b"{}\n")
    document = _load_definition()
    execution = document["experiment"]["execution"]
    # Explicit multi-step plan: no legacy configure/build/run arrays.
    execution.pop("configure_command", None)
    execution.pop("build_command", None)
    execution.pop("run_command", None)
    execution.pop("executable_path", None)
    execution["steps"] = [
        {
            "id": "prepare",
            "command": [sys.executable, "-c", "print('prepare')"],
        },
        {
            "id": "collect",
            "cwd": "run_dir",
            "command": [
                sys.executable, "-c",
                "import json; from pathlib import Path; "
                "p=Path(r'{run_dir}/raw/scanner_benchmark.json'); "
                "p.parent.mkdir(parents=True, exist_ok=True); "
                "p.write_text(json.dumps(dict(benchmarks=[])), encoding='utf-8')",
            ],
        },
    ]
    execution["expected_outputs"] = ["raw/scanner_benchmark.json"]

    manifest_path = run_experiment(
        document,
        source_dir=source,
        data_dir=tmp_path / "research-data",
        registry_path=EXPERIMENT_PATH,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "COMPLETED"
    purposes = [record["purpose"] for record in manifest["execution_records"]]
    assert purposes == ["prepare", "collect"]
    # The artifact is attributed to the step that actually produced it.
    producer = [
        record for record in manifest["execution_records"]
        if "raw/scanner_benchmark.json" in record.get("produced_outputs", [])
    ]
    assert len(producer) == 1
    assert producer[0]["purpose"] == "collect"


def test_runner_script_mode_records_interpreter_version(tmp_path):
    source = _prepare_source(tmp_path, dataset=b"{}\n")
    document = _load_definition()
    execution = document["experiment"]["execution"]
    execution.pop("configure_command", None)
    execution.pop("build_command", None)
    execution["mode"] = "script"
    execution["steps"] = [
        {
            "id": "run",
            "command": [
                sys.executable, "-c",
                "import json; from pathlib import Path; "
                "p=Path(r'{run_dir}/raw/result.jsonl'); "
                "p.parent.mkdir(parents=True, exist_ok=True); "
                "p.write_text(json.dumps(dict(benchmarks=[])), encoding='utf-8')",
            ],
        }
    ]
    execution["expected_outputs"] = ["raw/result.jsonl"]

    manifest_path = run_experiment(
        document,
        source_dir=source,
        data_dir=tmp_path / "research-data",
        registry_path=EXPERIMENT_PATH,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "COMPLETED"
    assert manifest["execution"]["mode"] == "script"
    assert manifest["build"]["interpreter_version"]
    assert manifest["build"]["interpreter_version"].startswith("Python")

