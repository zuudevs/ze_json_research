"""Run a reviewed experiment using external build/output directories."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .gates import GateError, evaluate_gates
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


_PLACEHOLDER_PATTERN = re.compile(r"\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(value: str, variables: dict[str, str]) -> str:
    """Expand declared placeholders without breaking literal braces.

    ``{{`` and ``}}`` produce a literal ``{`` and ``}`` so a command may carry
    a JSON or Python payload. An unknown ``{name}`` is still an error: that is
    how a mistyped placeholder is caught instead of silently passed through.
    """

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "{{":
            return "{"
        if token == "}}":
            return "}"
        name = match.group(1)
        if name not in variables:
            raise RunnerError(
                f"Placeholder command tidak dikenal atau tidak lengkap: {{{name}}}"
            )
        return variables[name]

    result = _PLACEHOLDER_PATTERN.sub(replace, value)
    # Braces that remain came from an escape, so the only real defect left is
    # an unbalanced one, for example a mistyped "{run_dir" that never closed.
    if result.count("{") != result.count("}"):
        raise RunnerError(f"Placeholder command tidak dikenal atau tidak lengkap: {value!r}")
    return result


def _expand_command(command: Any, variables: dict[str, str], name: str) -> list[str]:
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        raise RunnerError(f"{name} harus berupa array argumen string nonkosong.")
    return [_expand(part, variables) for part in command]


def _resolve_workdir(value: Any, *, source_dir: Path, run_dir: Path, name: str) -> Path:
    if value in (None, "source_dir", "source"):
        return source_dir
    if value in ("run_dir", "run"):
        return run_dir
    raise RunnerError(f"{name}.cwd harus 'source_dir' atau 'run_dir', ditemukan {value!r}.")


def _normalize_steps(
    execution_definition: dict[str, Any],
    variables: dict[str, str],
    *,
    source_dir: Path,
    run_dir: Path,
) -> list[dict[str, Any]]:
    """Return the ordered execution plan.

    ``execution.steps`` is the explicit form. When it is absent the historical
    ``configure_command``/``build_command``/``run_command`` arrays are used so
    existing definitions and tests keep working unchanged.
    """
    declared = execution_definition.get("steps")
    if declared is None:
        declared = [
            {"id": "configure", "command": execution_definition["configure_command"]},
            {"id": "build", "command": execution_definition["build_command"]},
            {"id": "run", "command": execution_definition["run_command"], "cwd": "source_dir"},
        ]

    if not isinstance(declared, list) or not declared:
        raise RunnerError("execution.steps harus berupa array nonkosong bila disediakan.")

    steps: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, step in enumerate(declared, start=1):
        if not isinstance(step, dict):
            raise RunnerError(f"execution.steps[{index}] harus berupa object/map.")
        step_id = step.get("id") or step.get("purpose") or f"step{index}"
        if not isinstance(step_id, str) or not step_id.strip():
            raise RunnerError(f"execution.steps[{index}].id wajib berupa string nonkosong.")
        if step_id in seen_ids:
            raise RunnerError(f"execution.steps memiliki id duplikat: {step_id!r}.")
        seen_ids.add(step_id)
        steps.append(
            {
                "id": step_id,
                "required": bool(step.get("required", True)),
                "command": _expand_command(
                    step.get("command"), variables, f"execution.steps[{step_id}].command"
                ),
                "cwd": _resolve_workdir(
                    step.get("cwd", "source_dir"),
                    source_dir=source_dir,
                    run_dir=run_dir,
                    name=f"execution.steps[{step_id}]",
                ),
            }
        )
    return steps


def _hash_tree(root: Path, source_dir: Path) -> dict[str, Any]:
    """Digest a directory as a unit so corpus workloads can be pinned.

    Names are recorded relative to the source root, which is the same
    convention as scripts/research/dataset-digests.py, so a directory digest
    computed here can be compared directly against the generated manifest.

    Symlinks are skipped: a linked file can point outside the checkout and
    would make the recorded digest meaningless.
    """
    checkout = source_dir.resolve()
    entries: list[tuple[str, str, int]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        entries.append(
            (path.resolve().relative_to(checkout).as_posix(),
             sha256_file(path), path.stat().st_size)
        )
    formula = "\n".join(f"{name}\t{digest}\t{size}" for name, digest, size in entries)
    return {
        "sha256": hashlib.sha256(formula.encode("utf-8")).hexdigest(),
        "size_bytes": sum(size for _, _, size in entries),
        "file_count": len(entries),
        "kind": "directory",
    }


def _collect_input_snapshots(
    document: dict[str, Any], *, source_dir: Path
) -> list[dict[str, Any]]:
    """Hash every declared workload dataset at run time.

    The registry may pin ``dataset_sha256``. A mismatch means the checkout no
    longer matches the definition that was reviewed, which must abort the run
    instead of silently measuring different bytes.
    """
    experiment = document["experiment"]
    snapshots: list[dict[str, Any]] = []
    for workload in experiment.get("workloads", []):
        if not isinstance(workload, dict):
            continue
        dataset_path = workload.get("dataset_path")
        if not isinstance(dataset_path, str) or not dataset_path:
            continue
        candidate = Path(dataset_path)
        resolved = candidate if candidate.is_absolute() else (source_dir / candidate)
        resolved = resolved.resolve()
        try:
            resolved.relative_to(source_dir.resolve())
        except ValueError:
            raise RunnerError(
                f"Dataset workload {workload.get('id')!r} berada di luar source checkout: {resolved}"
            ) from None
        if not resolved.exists():
            raise RunnerError(
                f"Dataset workload {workload.get('id')!r} tidak ditemukan: {resolved}"
            )
        declared = workload.get("dataset_sha256")
        if resolved.is_dir():
            tree = _hash_tree(resolved, source_dir)
            actual: str | None = tree["sha256"]
            size: int | None = int(tree["size_bytes"])
            extra = {"file_count": tree["file_count"], "kind": "directory"}
        else:
            actual = sha256_file(resolved)
            size = resolved.stat().st_size
            extra = {"kind": "file"}
        if declared and actual and declared != actual:
            raise RunnerError(
                f"Dataset workload {workload.get('id')!r} berubah sejak definisi disetujui: "
                f"tercatat {declared}, ditemukan {actual}."
            )
        snapshots.append(
            {
                "workload_id": workload.get("id"),
                "declared_path": dataset_path,
                "resolved_path": str(resolved),
                "sha256": actual,
                "size_bytes": size,
                "matches_definition": declared is None or declared == actual,
                **extra,
            }
        )
    return snapshots


def _declared_commands(execution_definition: dict[str, Any]) -> list[list[str]]:
    """All raw command arrays declared by the definition (pre-expansion)."""
    commands: list[list[str]] = []
    steps = execution_definition.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict) and isinstance(step.get("command"), list):
                commands.append([part for part in step["command"] if isinstance(part, str)])
    for field in ("configure_command", "build_command", "run_command"):
        command = execution_definition.get(field)
        if isinstance(command, list):
            commands.append([part for part in command if isinstance(part, str)])
    return commands


def _build_variables(
    execution_definition: dict[str, Any],
    variables: dict[str, str],
    *,
    source_dir: Path,
) -> dict[str, str]:
    """Add environment-dependent placeholders and refuse silent emptiness.

    A missing VCPKG_ROOT would otherwise expand to an empty toolchain path and
    produce a configuration that does not reproduce the project preset.
    """
    commands = _declared_commands(execution_definition)
    uses_vcpkg = any("{vcpkg_root}" in part for command in commands for part in command)
    if uses_vcpkg:
        vcpkg_root = os.environ.get("VCPKG_ROOT")
        if not vcpkg_root:
            raise RunnerError(
                "Definisi memakai placeholder {vcpkg_root} tetapi VCPKG_ROOT tidak diset. "
                "Set VCPKG_ROOT ke checkout vcpkg yang terpasang."
            )
        variables["vcpkg_root"] = vcpkg_root
    return variables


def _recorded_env(execution_definition: dict[str, Any]) -> dict[str, str | None]:
    """Record the declared environment variables that can affect the measurement."""
    names = execution_definition.get("env_passthrough", [])
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise RunnerError("execution.env_passthrough harus berupa array string.")
    return {name: os.environ.get(name) for name in names}


def _probe_version(command: list[str], *, run_dir: Path, sequence: int) -> dict[str, Any]:
    started = utc_now()
    stdout_relative = f"logs/{sequence:02d}-version.stdout.log"
    stderr_relative = f"logs/{sequence:02d}-version.stderr.log"
    stdout_path = run_dir / stdout_relative
    stderr_path = run_dir / stderr_relative
    version_text: str | None = None
    error: str | None = None
    exit_code: int | None = None
    try:
        process = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=os.environ.copy(),
        )
        exit_code = process.returncode
        stdout_path.write_text(process.stdout or "", encoding="utf-8")
        stderr_path.write_text(process.stderr or "", encoding="utf-8")
        version_text = (process.stdout or process.stderr or "").strip().splitlines()
        version_text = version_text[0] if version_text else None
    except (OSError, ValueError) as exc:
        error = f"{type(exc).__name__}: {exc}"
        stderr_path.write_text(error + "\n", encoding="utf-8")
    return {
        "sequence_number": sequence,
        "purpose": "version_probe",
        "command": command,
        "cwd": str(run_dir),
        "started_at": started,
        "finished_at": utc_now(),
        "status": "COMPLETED" if exit_code == 0 else "FAILED",
        "exit_code": exit_code,
        "error": error,
        "stdout_path": stdout_relative,
        "stderr_path": stderr_relative,
        "reported_version": version_text,
        "required": False,
    }


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
        "required": True,
    }


def run_experiment(
    document: dict[str, Any],
    *,
    source_dir: Path,
    data_dir: Path,
    registry_path: Path,
    database_path: Path | None = None,
) -> Path:
    run_started_at = utc_now()
    experiment = document["experiment"]
    experiment_id = experiment["id"]
    execution_definition = experiment["execution"]
    mode = execution_definition.get("mode", "native")
    if mode not in {"native", "script"}:
        raise RunnerError("execution.mode harus 'native' atau 'script'.")
    require_clean = bool(execution_definition.get("require_clean_worktree", True))

    validate_external_data_dir(data_dir, source_dir=source_dir)
    source_info = capture_source_info(source_dir)
    if require_clean and not source_info["worktree_clean"]:
        raise RunnerError(
            "Run formal memerlukan working tree bersih. "
            "Commit atau simpan perubahan dengan strategi snapshot yang eksplisit terlebih dahulu."
        )

    if database_path is None:
        database_path = data_dir / "database" / "ze_json_research.sqlite"
    gate_records = evaluate_gates(
        experiment,
        source_info=source_info,
        database_path=database_path.expanduser().resolve(),
        run_started_at=run_started_at,
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
    multi_templates = execution_definition.get("executable_paths")
    if isinstance(multi_templates, list):
        executable_templates = [str(template) for template in multi_templates]
    elif executable_template:
        executable_templates = [str(executable_template)]
    else:
        executable_templates = []

    variables = {
        "source_dir": str(source_dir.resolve()),
        "build_dir": str(build_dir.resolve()),
        "run_dir": str(run_dir.resolve()),
        "data_dir": str(data_dir.resolve()),
        "experiment_id": experiment_id,
        "run_id": run_id,
    }
    variables = _build_variables(execution_definition, variables, source_dir=source_dir)
    if executable_templates:
        primary = Path(_expand(executable_templates[0], variables)).expanduser()
        if not primary.is_absolute():
            primary = (source_dir / primary).resolve()
        variables["benchmark_executable"] = str(primary)
    else:
        variables["benchmark_executable"] = ""

    expected_outputs = [
        _safe_relative_output(output) for output in execution_definition["expected_outputs"]
    ]
    input_snapshots = _collect_input_snapshots(document, source_dir=source_dir)
    recorded_env = _recorded_env(execution_definition)
    write_json_atomic(run_dir / "metadata" / "inputs.json", {"inputs": input_snapshots})

    steps = _normalize_steps(
        execution_definition, variables, source_dir=source_dir, run_dir=run_dir
    )
    executions: list[dict[str, Any]] = []
    sequence = 1
    outputs_pending = {
        relative: not (run_dir / Path(*PurePosixPath(relative).parts)).is_file()
        for relative in expected_outputs
    }
    for step in steps:
        executions.append(
            _run_command(
                step["command"],
                cwd=step["cwd"],
                run_dir=run_dir,
                sequence=sequence,
                purpose=step["id"],
            )
        )
        executions[-1]["required"] = step["required"]
        # Attribute each expected output to the step that actually produced it
        # so a multi-step run can tell which execution generated which result.
        executions[-1]["produced_outputs"] = []
        for relative, was_pending in outputs_pending.items():
            if was_pending and (run_dir / Path(*PurePosixPath(relative).parts)).is_file():
                outputs_pending[relative] = False
                executions[-1]["produced_outputs"].append(relative)
        sequence += 1
        if step["required"] and executions[-1]["exit_code"] != 0:
            break

    resolved_executables: list[dict[str, Any]] = []
    if mode == "native":
        last_required_ok = bool(executions) and all(
            record["exit_code"] == 0 for record in executions if record["required"]
        )
        if not last_required_ok:
            executable_templates = []
        for template in executable_templates:
            candidate_path = Path(_expand(template, variables)).expanduser()
            if not candidate_path.is_absolute():
                candidate_path = (source_dir / candidate_path).resolve()
            if not candidate_path.is_file():
                windows_candidate = Path(str(candidate_path) + ".exe")
                if windows_candidate.is_file():
                    candidate_path = windows_candidate
            if candidate_path.is_file():
                resolved_executables.append(
                    {
                        "declared_template": template,
                        "path": str(candidate_path.resolve()),
                        "sha256": sha256_file(candidate_path),
                        "size_bytes": candidate_path.stat().st_size,
                    }
                )
        if resolved_executables:
            variables["benchmark_executable"] = resolved_executables[0]["path"]

    run_steps = [step for step in steps if step["id"] in {"run", "benchmark", "execute"}]
    if mode == "script" and run_steps:
        interpreter = run_steps[0]["command"][0]
        probe_command = [interpreter, "--version"]
        probe = _probe_version(probe_command, run_dir=run_dir, sequence=sequence)
        executions.append(probe)
        sequence += 1

    finished_at = utc_now()
    missing_expected = [
        relative for relative in expected_outputs if not (run_dir / Path(*PurePosixPath(relative).parts)).is_file()
    ]
    required_failures = [
        record for record in executions if record["required"] and record["exit_code"] != 0
    ]
    status = (
        "COMPLETED"
        if executions
        and not required_failures
        and not missing_expected
        else "FAILED"
    )

    build_record: dict[str, Any] = {
        "build_id": run_id,
        "build_dir": str(build_dir.resolve()),
        "preset": execution_definition.get("preset"),
        "mode": mode,
        "configure_command": executions[0]["command"] if executions else [],
        "build_command": executions[1]["command"] if len(executions) > 1 else [],
    }
    if mode == "script":
        probe_record = next(
            (record for record in executions if record["purpose"] == "version_probe"), None
        )
        build_record.update({
            "interpreter": run_steps[0]["command"][0] if run_steps else None,
            "interpreter_version": probe_record.get("reported_version") if probe_record else None,
        })
    if resolved_executables:
        build_record["executables"] = resolved_executables
        build_record.update({
            "executable_path": resolved_executables[0]["path"],
            "binary_sha256": resolved_executables[0]["sha256"],
            "binary_size_bytes": resolved_executables[0]["size_bytes"],
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
        "execution": {
            "mode": mode,
            "preset": execution_definition.get("preset"),
            "env_passthrough": recorded_env,
        },
        "inputs": input_snapshots,
        "gates": gate_records,
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
