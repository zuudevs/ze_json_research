import json
import subprocess
import sys
from pathlib import Path

from ze_research.manifest import verify_manifest
from ze_research.registry import load_yaml
from ze_research.runner import run_experiment


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
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "initial source")
    data_dir = tmp_path / "research-data"
    _, document = (EXPERIMENT_PATH, load_yaml(EXPERIMENT_PATH))
    document["experiment"]["execution"]["executable_path"] = None
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
