"""Idempotent importer for run manifests and supported JSON result formats."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .database import connect, migrate, persist_definition
from .manifest import sha256_file, verify_manifest
from .registry import definition_sha256


class IngestError(RuntimeError):
    """Raised when an artifact package cannot be safely ingested."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _duration_seconds(value: Any) -> float | None:
    if not isinstance(value, (str, int, float)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(ns|us|µs|ms|s)?\s*", value)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2) or "s"
    return number / {"ns": 1e9, "us": 1e6, "µs": 1e6, "ms": 1e3, "s": 1}[unit]


def _normalize_benchmark_name(benchmark_name: str) -> str:
    """Reduce a reported benchmark name to the registered name.

    Two different slash conventions meet here:

    - Google Benchmark appends option and argument groups to the name, for
      example ``.../min_time:15.000`` or ``BM_SpanIso_Scanner64_FillWS/8``. Those
      are not part of the registered name.
    - A gtest-style fixture uses the slash as a separator, for example
      ``ParserFixture/DocumentInitialization``. That slash *is* the name.

    Only groups that look like an argument are removed: a ``Name:value`` pair or
    a plain number. A bare identifier stays, so a fixture case name survives.
    """
    normalized = re.sub(
        r"/(?:min_time|min_warmup_time|repeats|iterations|threads):[^/]+", "", benchmark_name
    )
    while True:
        match = re.search(r"/([^/]*:[^/]*|\d+)$", normalized)
        if match is None:
            break
        normalized = normalized[: match.start()]
    return normalized


def _benchmark_argument_suffix(benchmark_name: str) -> str:
    """The parameter part of a reported name, kept for row-level reporting.

    Option groups such as ``/min_time:15.000`` are not parameters and must not
    split a row, while a real argument such as ``/KiB:0`` must. Both are removed
    before comparing against the normalized name.
    """
    without_options = re.sub(
        r"/(?:min_time|min_warmup_time|repeats|iterations|threads):[^/]+", "", benchmark_name
    )
    normalized = _normalize_benchmark_name(benchmark_name)
    if without_options.startswith(normalized):
        return without_options[len(normalized):]
    return ""


def _benchmark_spec_match(
    benchmark_name: str, specs: list[sqlite3.Row]
) -> tuple[str | None, str]:
    normalized = _normalize_benchmark_name(benchmark_name)
    matches: list[str] = []
    for spec in specs:
        pattern = re.escape(spec["name_pattern"])
        pattern = pattern.replace(re.escape("{dataset}"), r".+")
        pattern = pattern.replace(re.escape("{case}"), r".+")
        if re.fullmatch(pattern, normalized):
            matches.append(spec["benchmark_spec_id"])
    if len(matches) == 1:
        return matches[0], "MAPPED"
    if len(matches) > 1:
        return None, "AMBIGUOUS"
    return None, "UNMAPPED"


def _import_benchmark_json(
    file_path: Path,
    execution_id: str,
    artifact_id: str,
    specs: list[sqlite3.Row],
    connection: sqlite3.Connection,
) -> int:
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IngestError(f"JSON benchmark tidak dapat dibaca: {file_path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("benchmarks"), list):
        return 0

    count = 0
    for row in payload["benchmarks"]:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        spec_id, mapping_status = _benchmark_spec_match(row["name"], specs)
        run_type = row.get("run_type")
        iterations = row.get("iterations")
        # Preserve the parameter (for example /KiB:0) so a report can keep a
        # size sweep apart instead of averaging across its points.
        argument_suffix = _benchmark_argument_suffix(row["name"])
        normalized_name = _normalize_benchmark_name(row["name"])
        connection.execute(
            """INSERT OR IGNORE INTO benchmark_results(
                execution_id, source_artifact_id, benchmark_name_raw, benchmark_spec_id,
                mapping_status, run_type, iterations, real_time, cpu_time, time_unit,
                bytes_per_second, items_per_second, error_occurred, error_message,
                benchmark_name_normalized, benchmark_argument, raw_row_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                execution_id, artifact_id, row["name"], spec_id, mapping_status, run_type,
                iterations if isinstance(iterations, int) else None,
                row.get("real_time"), row.get("cpu_time"), row.get("time_unit"),
                row.get("bytes_per_second"), row.get("items_per_second"),
                int(bool(row.get("error_occurred"))), row.get("error_message"),
                normalized_name, argument_suffix, _json(row),
            ),
        )
        if connection.execute("SELECT changes()").fetchone()[0]:
            count += 1
    return count


def _test_status(case: dict[str, Any]) -> tuple[str, str | None]:
    failures = case.get("failures", [])
    failure_text: str | None = None
    if isinstance(failures, list) and failures:
        failure_text = "\n".join(
            str(failure.get("failure", failure.get("message", failure)))
            if isinstance(failure, dict) else str(failure)
            for failure in failures
        )
        return "FAILED", failure_text
    result = str(case.get("result", "")).upper()
    status = str(case.get("status", "")).upper()
    name = str(case.get("name", "")).upper()
    if "DISABLED" in result or "DISABLED" in status or "DISABLED" in name:
        return "DISABLED", None
    if "SKIPPED" in result or "SKIPPED" in status or "NOTRUN" in status:
        return "SKIPPED", None
    # Google Test JSON marks a case that executed as status "RUN"; success is
    # the empty failures list handled above. Older formats use result/status.
    if status == "RUN" or result in {"COMPLETED", "PASSED", "SUCCESS"}:
        return "PASSED", None
    if status in {"PASSED", "SUCCESS"}:
        return "PASSED", None
    return "UNKNOWN", None


def _walk_gtest_cases(payload: dict[str, Any]) -> list[tuple[str | None, dict[str, Any]]]:
    """Collect (suite, case) pairs from any Google Test JSON layout.

    Modern layout is ``testsuites[].testsuite[]``; older ones used
    ``testsuites[].tests[]`` or nested ``testsuites``. ``tests`` is a count in
    the modern format, not a list, so it must never be treated as cases.
    """
    records: list[tuple[str | None, dict[str, Any]]] = []

    def visit(suite: Any, inherited_name: str | None = None) -> None:
        if not isinstance(suite, dict):
            return
        suite_name = suite.get("name") if isinstance(suite.get("name"), str) else inherited_name
        for key in ("testsuite", "tests"):
            cases = suite.get(key)
            if isinstance(cases, list):
                for case in cases:
                    if isinstance(case, dict) and isinstance(case.get("name"), str):
                        records.append((suite_name, case))
        nested = suite.get("testsuites")
        if isinstance(nested, list):
            for item in nested:
                visit(item, suite_name)

    top = payload.get("testsuites")
    if isinstance(top, list):
        for suite in top:
            visit(suite)
    top_tests = payload.get("tests")
    if isinstance(top_tests, list):
        for suite in top_tests:
            visit(suite)
    return records


def _import_gtest_json(
    file_path: Path,
    execution_id: str,
    artifact_id: str,
    connection: sqlite3.Connection,
) -> int:
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IngestError(f"JSON test tidak dapat dibaca: {file_path}: {exc}") from exc
    if not isinstance(payload, dict):
        return 0
    records = _walk_gtest_cases(payload)
    count = 0
    for suite_name, case in records:
        status, failure_text = _test_status(case)
        connection.execute(
            """INSERT OR IGNORE INTO test_results(
                execution_id, source_artifact_id, suite_name, test_name, status,
                duration_seconds, failure_text, raw_case_json)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                execution_id, artifact_id, suite_name, case["name"], status,
                _duration_seconds(case.get("time")), failure_text, _json(case),
            ),
        )
        if connection.execute("SELECT changes()").fetchone()[0]:
            count += 1
    return count


def _number(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    return float(value) if isinstance(value, (int, float)) else None


def _integer(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _text(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _import_js_parse_jsonl(
    file_path: Path,
    *,
    execution_id: str,
    artifact_id: str,
    run_id: str,
    connection: sqlite3.Connection,
) -> int:
    """Import the JavaScript JSON.parse baseline JSON Lines file.

    Each line is one fully summarized payload sample. The payload digest and
    sample-manifest digest written by the harness are kept, so a JS number can
    name the exact bytes it measured.
    """
    count = 0
    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise IngestError(f"JSONL baseline tidak dapat dibaca: {file_path}: {exc}") from exc

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IngestError(f"Baris JSONL tidak valid di {file_path}: {exc}") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("payload_id"), str):
            raise IngestError(f"Baris JSONL tanpa payload_id di {file_path}")
        cursor = connection.execute(
            """INSERT OR IGNORE INTO js_parse_results(
                execution_id, source_artifact_id, run_id, payload_id, parser, operation,
                measurement_contract_id, root_type, payload_sha256, payload_bytes_utf8,
                sample_manifest_sha256, snapshot_id, min_us_per_op, median_us_per_op,
                mean_us_per_op, standard_deviation_us_per_op, max_us_per_op,
                coefficient_of_variation_percent, ops_per_second, bytes_per_second,
                mib_per_second, sample_count, iterations_per_sample, warmup_iterations,
                time_unit, raw_row_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                execution_id, artifact_id, run_id, payload["payload_id"],
                _text(payload, "parser") or "unknown",
                _text(payload, "operation") or "unknown",
                _text(payload, "measurement_contract_id"), _text(payload, "root_type"),
                _text(payload, "payload_sha256"), _integer(payload, "payload_bytes_utf8"),
                _text(payload, "sample_manifest_sha256"), _text(payload, "snapshot_id"),
                _number(payload, "min_us_per_op"), _number(payload, "median_us_per_op"),
                _number(payload, "mean_us_per_op"),
                _number(payload, "standard_deviation_us_per_op"),
                _number(payload, "max_us_per_op"),
                _number(payload, "coefficient_of_variation_percent"),
                _number(payload, "ops_per_second"), _number(payload, "bytes_per_second"),
                _number(payload, "mib_per_second"), _integer(payload, "sample_count"),
                _integer(payload, "iterations_per_sample"),
                _integer(payload, "warmup_iterations"),
                "us", _json(payload),
            ),
        )
        if cursor.rowcount and connection.execute("SELECT changes()").fetchone()[0]:
            count += 1
    return count


def ingest_manifest(
    manifest_path: Path, database_path: Path, *, verify: bool = True
) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IngestError(f"Manifest tidak dapat dibaca: {exc}") from exc
    if not isinstance(manifest, dict):
        raise IngestError("Root manifest harus berupa object.")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise IngestError("manifest.run_id wajib tersedia.")

    verification = verify_manifest(manifest_path) if verify else {
        "valid": False, "verification_status": "NOT_CHECKED",
        "errors": ["Verifikasi artefak dilewati atas permintaan eksplisit."],
    }
    if verify and not verification["valid"]:
        raise IngestError(
            "Manifest/artefak gagal verifikasi:\n- " + "\n- ".join(verification["errors"])
        )
    if manifest.get("status") != "COMPLETED":
        raise IngestError(
            f"Run berstatus {manifest.get('status')!r}; run yang tidak selesai tidak diingest sebagai run lengkap."
        )

    snapshot = manifest.get("definition_snapshot")
    if not isinstance(snapshot, dict):
        raise IngestError("definition_snapshot tidak tersedia.")
    actual_definition_hash = definition_sha256(snapshot)
    if actual_definition_hash != manifest.get("definition_sha256"):
        raise IngestError("Fingerprint definition_snapshot tidak cocok.")

    run_dir = manifest_path.parent.resolve()
    manifest_hash = sha256_file(manifest_path)
    database_path = database_path.expanduser().resolve()
    migrate(database_path)

    result_counts = {"benchmark_results": 0, "test_results": 0, "js_parse_results": 0}
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT manifest_sha256 FROM research_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        if existing:
            if existing["manifest_sha256"] != manifest_hash:
                raise IngestError(
                    f"run_id {run_id!r} sudah ada dengan manifest berbeda; data historis tidak ditimpa."
                )
            connection.rollback()
            return {
                "run_id": run_id,
                "status": "ALREADY_IMPORTED",
                "manifest_sha256": manifest_hash,
                "result_counts": result_counts,
            }

        experiment_version_id = persist_definition(connection, snapshot)
        source = manifest.get("source", {})
        environment = manifest.get("environment", {})
        build = manifest.get("build", {})
        recorded_inputs = {
            entry.get("workload_id"): entry
            for entry in manifest.get("inputs", [])
            if isinstance(entry, dict) and entry.get("workload_id")
        }
        connection.execute(
            """INSERT INTO research_runs(
                run_id, experiment_version_id, started_at, finished_at, status,
                verification_status, manifest_path, manifest_sha256, source_json,
                environment_json, build_json, definition_sha256, gates_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, experiment_version_id, manifest.get("started_at", "unknown"),
                manifest.get("finished_at"), manifest.get("status", "UNKNOWN"),
                verification.get("verification_status", "NOT_CHECKED"),
                str(manifest_path), manifest_hash, _json(source), _json(environment),
                _json(build), actual_definition_hash, _json(manifest.get("gates", [])),
            ),
        )

        for index, workload in enumerate(snapshot["experiment"].get("workloads", [])):
            workload_id = workload["id"]
            input_path = workload.get("dataset_path")
            input_hash = workload.get("dataset_sha256")
            input_size = None
            # Prefer the digest captured while the run executed. Recomputing it
            # from the live checkout would let the working tree drift between
            # the measurement and the import without anyone noticing.
            recorded = recorded_inputs.get(workload_id)
            if recorded is not None:
                input_hash = recorded.get("sha256", input_hash)
                input_size = recorded.get("size_bytes")
                input_path = recorded.get("declared_path", input_path)
            elif input_path:
                candidate = Path(input_path)
                if not candidate.is_absolute():
                    candidate = Path(source.get("repository_root", ".")) / candidate
                if candidate.is_file():
                    input_hash = sha256_file(candidate)
                    input_size = candidate.stat().st_size
            connection.execute(
                """INSERT OR IGNORE INTO run_inputs(
                    run_id, workload_id, resolved_path, sha256, size_bytes, parameters_json)
                    VALUES(?,?,?,?,?,?)""",
                (run_id, workload_id, str(input_path) if input_path else None,
                 input_hash, input_size, _json(workload.get("parameters", {}))),
            )

        execution_id_by_purpose: dict[str, str] = {}
        execution_id_by_log: dict[str, str] = {}
        execution_id_by_output: dict[str, str] = {}
        execution_records = manifest.get("execution_records", [])
        for index, execution in enumerate(execution_records, start=1):
            sequence = execution.get("sequence_number", index)
            if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
                sequence = index
            # The ID follows the recorded sequence number so a result row can be
            # traced back to the step number seen in the manifest.
            execution_id = f"{run_id}-e{sequence:03d}"
            execution_id_by_purpose[execution.get("purpose", "unknown")] = execution_id
            connection.execute(
                """INSERT INTO executions(
                    execution_id, run_id, sequence_number, purpose, command_json, cwd,
                    started_at, finished_at, status, exit_code, stdout_artifact_path,
                    stderr_artifact_path)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    execution_id, run_id, sequence,
                    execution.get("purpose", "unknown"), _json(execution.get("command", [])),
                    execution.get("cwd", str(run_dir)), execution.get("started_at", manifest.get("started_at", "unknown")),
                    execution.get("finished_at"), execution.get("status", "UNKNOWN"),
                    execution.get("exit_code"), execution.get("stdout_path"),
                    execution.get("stderr_path"),
                ),
            )
            for log_path in (execution.get("stdout_path"), execution.get("stderr_path")):
                if log_path:
                    execution_id_by_log[log_path] = execution_id
            for output_path in execution.get("produced_outputs", []) or []:
                if isinstance(output_path, str):
                    execution_id_by_output[output_path] = execution_id

        # Multi-step runs have no single "run" purpose, so a result artifact is
        # attached to the execution that produced it, then to a declared "run"
        # step, and only as a last resort to the final execution.
        run_execution = execution_id_by_purpose.get("run")
        if run_execution is None and execution_id_by_purpose:
            run_execution = execution_id_by_purpose[execution_records[-1].get("purpose", "unknown")]
        for artifact in manifest.get("artifacts", []):
            if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
                continue
            relative = artifact["path"]
            artifact_id = "artifact-" + hashlib.sha256(
                f"{run_id}|{relative}".encode("utf-8")
            ).hexdigest()[:24]
            execution_id = (
                execution_id_by_output.get(relative)
                or execution_id_by_log.get(relative)
                or run_execution
            )
            connection.execute(
                """INSERT INTO artifacts(
                    artifact_id, run_id, execution_id, relative_path, kind, sha256,
                    size_bytes, verified)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    artifact_id, run_id, execution_id, relative, artifact.get("kind", "run_file"),
                    artifact["sha256"], int(artifact["size_bytes"]),
                    int(bool(verification.get("valid"))),
                ),
            )
            if artifact.get("kind") != "raw_result":
                continue
            raw_path = (run_dir / relative).resolve()
            if not raw_path.is_file():
                continue
            if run_execution is None:
                continue
            if raw_path.suffix.lower() == ".jsonl":
                result_counts["js_parse_results"] += _import_js_parse_jsonl(
                    raw_path,
                    execution_id=execution_id or run_execution,
                    artifact_id=artifact_id,
                    run_id=run_id,
                    connection=connection,
                )
                continue
            if raw_path.suffix.lower() != ".json":
                continue
            specs = connection.execute(
                "SELECT benchmark_spec_id, name_pattern FROM benchmark_specs "
                "WHERE experiment_version_id=?",
                (experiment_version_id,),
            ).fetchall()
            result_counts["benchmark_results"] += _import_benchmark_json(
                raw_path, run_execution, artifact_id, specs, connection
            )
            result_counts["test_results"] += _import_gtest_json(
                raw_path, run_execution, artifact_id, connection
            )

        connection.commit()

    return {
        "run_id": run_id,
        "status": "IMPORTED",
        "verification_status": verification.get("verification_status", "NOT_CHECKED"),
        "manifest_sha256": manifest_hash,
        "result_counts": result_counts,
    }
