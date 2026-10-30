"""Correctness gates that must hold before a performance experiment may run.

A performance number and a correctness result are different kinds of evidence.
This module links them: an experiment can declare that a named correctness
experiment must already have a VERIFIED run on the same source commit before a
formal run of the performance experiment is allowed.

The gate is intentionally fail-closed. A missing gate record means the run is
refused, not tolerated. SILENCE_IS_NOT_ACCEPTANCE: if the evidence cannot be
found, the performance claim cannot be supported by it.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class GateError(RuntimeError):
    """Raised when a declared correctness gate cannot be satisfied."""


_LATEST_RUN_SQL = """
SELECT r.run_id, r.status, r.verification_status, r.started_at, r.manifest_path,
       r.manifest_sha256, r.definition_sha256, r.source_json, v.definition_json
FROM research_runs AS r
JOIN experiment_versions AS v
  ON v.experiment_version_id = r.experiment_version_id
JOIN experiments AS e
  ON e.experiment_id = v.experiment_id
WHERE e.experiment_id = ?
ORDER BY r.started_at DESC
"""


def _commit_of(source_json: str | None) -> str | None:
    if not source_json:
        return None
    try:
        payload = json.loads(source_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    commit = payload.get("git_commit")
    return commit if isinstance(commit, str) else None


def find_gate_run(
    *,
    database_path: Path,
    experiment_id: str,
    required_verification_status: str,
    required_commit: str | None,
) -> dict[str, Any] | None:
    if not database_path.is_file():
        return None
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        try:
            rows = connection.execute(_LATEST_RUN_SQL, (experiment_id,)).fetchall()
        except sqlite3.Error:
            return None
    finally:
        connection.close()

    for row in rows:
        if row["status"] != "COMPLETED":
            continue
        if required_verification_status and row["verification_status"] != required_verification_status:
            continue
        if required_commit is not None and _commit_of(row["source_json"]) != required_commit:
            continue
        return {
            "run_id": row["run_id"],
            "started_at": row["started_at"],
            "manifest_path": row["manifest_path"],
            "manifest_sha256": row["manifest_sha256"],
            "definition_sha256": row["definition_sha256"],
            "verification_status": row["verification_status"],
            "git_commit": _commit_of(row["source_json"]),
        }
    return None


def evaluate_gates(
    experiment: dict[str, Any],
    *,
    source_info: dict[str, Any],
    database_path: Path,
    run_started_at: str,
) -> list[dict[str, Any]]:
    declaration = experiment.get("correctness_gate")
    if declaration in (None, False, {}):
        return []
    if not isinstance(declaration, dict):
        raise GateError("experiment.correctness_gate harus berupa object/map.")

    gate_id = declaration.get("id", f"gate-{declaration.get('experiment_id', 'unknown')}")
    gate_experiment = declaration.get("experiment_id")
    if not isinstance(gate_experiment, str) or not gate_experiment:
        raise GateError("experiment.correctness_gate.experiment_id wajib berupa string nonkosong.")
    required_status = declaration.get("require_verification_status", "VERIFIED")
    if not isinstance(required_status, str) or not required_status:
        raise GateError("correctness_gate.require_verification_status wajib berupa string nonkosong.")
    require_same_commit = bool(declaration.get("require_same_commit", True))
    required_commit = source_info.get("git_commit") if require_same_commit else None

    matched = find_gate_run(
        database_path=database_path,
        experiment_id=gate_experiment,
        required_verification_status=required_status,
        required_commit=required_commit,
    )
    if matched is None:
        if require_same_commit:
            detail = (
                f"Tidak ada run {required_status} untuk eksperimen gate "
                f"{gate_experiment!r} pada commit {required_commit!r}. "
                "Jalankan dan ingest eksperimen korektness pada commit yang sama lebih dahulu."
            )
        else:
            detail = (
                f"Tidak ada run {required_status} untuk eksperimen gate {gate_experiment!r}."
            )
        raise GateError(detail)

    return [
        {
            "gate_id": gate_id,
            "gate_experiment_id": gate_experiment,
            "require_verification_status": required_status,
            "require_same_commit": require_same_commit,
            "required_commit": required_commit,
            "checked_at": run_started_at,
            "satisfied": True,
            "matched_run": matched,
        }
    ]
