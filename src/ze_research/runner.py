"""Run a reviewed experiment using external build/output directories."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .manifest import (
    capture_source_info,
    collect_artifacts,
    sha256_file,
    utc_now,
    verify_manifest,
    write_json_atomic,
)
from .paths import validate_external_data_dir
from .registry import definition_sha256


class RunnerError(RuntimeError):
    """Raised when a run cannot safely start or complete."""


def _safe_relative_output(value: str) -> str:
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts:
        raise RunnerError(f"Expected output harus berupa path relatif yang aman: {value!r}")
    return candidate.as_posix()


def _expand(value: str, variables: dict[str, str]) -> str:
    result = value
    for key, replacement in variables.items():
        result = result.replace("{" + key + "}", replacement)
    if "{" in result or "}" in result:
        raise RunnerError(f"Placeholder command tidak dikenal atau tidak lengkap: {value!r}")
    return result


def _expand_command(command: Any, variables: dict[str, str], name: str) -> list[str]:
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        raise RunnerError(f"{name} harus berupa array argumen string nonkosong.")
    return [_expand(part, variables) for part in command]


def _run_command(
    command: list[str], *, cwd: Path, run_dir: Path, sequence: int, purpose: str
) -> dict[str, Any]:
    started = utc_now()
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_relative = f"logs/{sequence:02d}-{purpose}.stdout.log"
    stderr_relative = f"logs/{sequence:02d}-{purpose}.stderr.log"
    stdout_path = run_dir / stdout_relative
    stderr_path = run_dir / stderr_relative
    return_code: int | None = None
    error: str | None = None
    try:
        with stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
            process = subprocess.run(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=stdout_stream,
                stderr=stderr_stream,
                shell=False,
                check=False,
                env=os.environ.copy(),
            )
            return_code = process.returncode
    except (OSError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        stderr_path.write_text(error + "\n", encoding="utf-8")
    finished = utc_now()
    status = "COMPLETED" if return_code == 0 else "FAILED"
    return {
        "sequence_number": sequence,
        "purpose": purpose,
        "command": command,
        "cwd": str(cwd),
        "started_at": started,
        "finished_at": finished,
        "status": status,
        "exit_code": return_code,
        "error": error,
        "stdout_path": stdout_relative,
        "stderr_path": stderr_relative,
    }


def run_experiment(
    document: dict[str, Any],
    *,
    source_dir: Path,
    data_dir: Path,
    registry_path: Path,
) -> Path:
    run_started_at = utc_now()
    experiment = document["experiment"]
    experiment_id = experiment["id"]
    execution_definition = experiment["execution"]
    require_clean = bool(execution_definition.get("require_clean_worktree", True))

    validate_external_data_dir(data_dir, source_dir=source_dir)
    source_info = capture_source_info(source_dir)
    if require_clean and not source_info["worktree_clean"]:
        raise RunnerError(
            "Run formal memerlukan working tree bersih. "
            "Commit atau simpan perubahan dengan strategi snapshot yang eksplisit terlebih dahulu."
        )

    run_id = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:12]}"
    run_dir = data_dir / "runs" / run_id
    build_dir = data_dir / "builds" / run_id
    validate_external_data_dir(run_dir, source_dir=source_dir)
    validate_external_data_dir(build_dir, source_dir=source_dir)
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "raw").mkdir()
    (run_dir / "logs").mkdir()
    (run_dir / "metadata").mkdir()
    build_dir.mkdir(parents=True, exist_ok=False)

    snapshot = {
        "schema_version": document.get("schema_version"),
        "experiment": experiment,
    }
    fingerprint = definition_sha256(snapshot)
    executable_template = execution_definition.get("executable_path")
    variables = {
        "source_dir": str(source_dir.resolve()),
        "build_dir": str(build_dir.resolve()),
        "run_dir": str(run_dir.resolve()),
        "data_dir": str(data_dir.resolve()),
        "experiment_id": experiment_id,
        "run_id": run_id,
    }
    if executable_template:
        executable_path = Path(_expand(str(executable_template), variables)).expanduser()
        if not executable_path.is_absolute():
            executable_path = (source_dir / executable_path).resolve()
        variables["benchmark_executable"] = str(executable_path)
    else:
        executable_path = None
        variables["benchmark_executable"] = ""

    expected_outputs = [
        _safe_relative_output(output) for output in execution_definition["expected_outputs"]
    ]

    executions: list[dict[str, Any]] = []
    commands = (
        ("configure", execution_definition["configure_command"], source_dir),
        ("build", execution_definition["build_command"], source_dir),
    )
    sequence = 1
    for purpose, command_template, working_directory in commands:
        command = _expand_command(command_template, variables, f"execution.{purpose}_command")
        record = _run_command(
            command, cwd=working_directory, run_dir=run_dir, sequence=sequence, purpose=purpose
        )
        executions.append(record)
        sequence += 1
        if record["exit_code"] != 0:
            break

    if executions and executions[-1]["exit_code"] == 0:
        if executable_path is not None and not executable_path.is_file():
            windows_candidate = Path(str(executable_path) + ".exe")
            if windows_candidate.is_file():
                executable_path = windows_candidate
                variables["benchmark_executable"] = str(executable_path)
        if executable_path is not None:
            variables["benchmark_executable"] = str(executable_path.resolve())
        run_command = _expand_command(
            execution_definition["run_command"], variables, "execution.run_command"
        )
        executions.append(
            _run_command(
                run_command,
                cwd=source_dir,
                run_dir=run_dir,
                sequence=sequence,
                purpose="run",
            )
        )

    finished_at = utc_now()
    missing_expected = [
        relative for relative in expected_outputs if not (run_dir / Path(*PurePosixPath(relative).parts)).is_file()
    ]
    all_commands_succeeded = len(executions) == 3 and all(
        record["exit_code"] == 0 for record in executions
    )
    status = "COMPLETED" if all_commands_succeeded and not missing_expected else "FAILED"

    build_record: dict[str, Any] = {
        "build_id": run_id,
        "build_dir": str(build_dir.resolve()),
        "preset": execution_definition.get("preset"),
        "configure_command": executions[0]["command"] if executions else [],
        "build_command": executions[1]["command"] if len(executions) > 1 else [],
    }
    if executable_path is not None and executable_path.is_file():
        build_record.update({
            "executable_path": str(executable_path.resolve()),
            "binary_sha256": sha256_file(executable_path),
            "binary_size_bytes": executable_path.stat().st_size,
        })

    manifest: dict[str, Any] = {
        "manifest_schema_version": "1.0",
        "run_id": run_id,
        "experiment_id": experiment_id,
        "registry_path": str(registry_path.resolve()),
        "registry_file_sha256": sha256_file(registry_path),
        "definition_snapshot": snapshot,
        "definition_sha256": fingerprint,
        "started_at": run_started_at,
        "finished_at": finished_at,
        "status": status,
        "verification_status": "PENDING",
        "source": source_info,
        "environment": {
            "platform": platform.platform(),
            "python_version": sys.version,
            "python_executable": sys.executable,
            "machine": platform.machine(),
            "processor": platform.processor(),
            "logical_cpu_count": os.cpu_count(),
        },
        "build": build_record,
        "execution_records": executions,
        "expected_outputs": expected_outputs,
        "missing_expected_outputs": missing_expected,
        "artifacts": [],
        "notes": [
            "VERIFIED means mechanical checks passed, not that the scientific conclusion is true.",
            "A dirty-source snapshot is not a fully portable source archive; formal runs default to a clean working tree.",
        ],
    }
    manifest["artifacts"] = collect_artifacts(run_dir)
    manifest_path = run_dir / "manifest.json"
    write_json_atomic(manifest_path, manifest)
    verification = verify_manifest(manifest_path)
    manifest["verification_status"] = verification["verification_status"]
    manifest["verification_errors"] = verification["errors"]
    write_json_atomic(manifest_path, manifest)
    return manifest_path
