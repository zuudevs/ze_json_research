"""Build defensible comparisons and exports from ingested research data.

Every number produced here is traceable: the module returns the run IDs,
manifest digests, definition fingerprints, benchmark specs, dataset digests,
and correctness gates that support it. A comparison that cannot name its
evidence is reported as unverifiable rather than published.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .database import connect, migrate

REPORT_VERSION = "1.0"

_ROW_SQL = """
SELECT br.run_type, br.iterations, br.real_time, br.cpu_time, br.time_unit,
       br.bytes_per_second, br.error_occurred, br.mapping_status,
       br.benchmark_name_raw, br.raw_row_json,
       ex.run_id, ex.purpose, ex.sequence_number,
       rr.started_at, rr.finished_at, rr.status, rr.verification_status,
       rr.manifest_path, rr.manifest_sha256, rr.definition_sha256, rr.gates_json,
       bs.benchmark_spec_id, bs.algorithm_id, bs.workload_id, bs.comparison_role,
       bs.comparison_group, bs.name_pattern,
       av.name AS algorithm_name, av.lifecycle_status,
       wc.name AS workload_name, wc.dataset_path, wc.dataset_sha256,
       ri.sha256 AS input_sha256, ri.size_bytes AS input_size
FROM benchmark_results AS br
JOIN executions AS ex ON ex.execution_id = br.execution_id
JOIN research_runs AS rr ON rr.run_id = ex.run_id
JOIN experiment_versions AS ev ON ev.experiment_version_id = rr.experiment_version_id
LEFT JOIN benchmark_specs AS bs ON bs.benchmark_spec_id = br.benchmark_spec_id
LEFT JOIN algorithm_variants AS av ON av.algorithm_id = bs.algorithm_id
LEFT JOIN workload_cases AS wc ON wc.workload_id = bs.workload_id
LEFT JOIN run_inputs AS ri ON ri.run_id = rr.run_id AND ri.workload_id = bs.workload_id
WHERE ev.experiment_id = ?
ORDER BY rr.started_at, ex.sequence_number
"""


class ReportError(RuntimeError):
    """Raised when a report cannot be produced from the stored data."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_rows(database_path: Path, experiment_id: str) -> list[dict[str, Any]]:
    if not database_path.is_file():
        raise ReportError(f"Database tidak ditemukan: {database_path}")
    connection = connect(database_path)
    try:
        rows = [dict(row) for row in connection.execute(_ROW_SQL, (experiment_id,)).fetchall()]
    except sqlite3.Error as exc:
        raise ReportError(f"Gagal membaca hasil benchmark: {exc}") from exc
    finally:
        connection.close()
    if not rows:
        raise ReportError(
            f"Tidak ada benchmark_results untuk eksperimen {experiment_id!r}. "
            "Ingest run-nya lebih dahulu."
        )
    return rows


def _definition_protocol(database_path: Path, experiment_id: str) -> dict[str, Any]:
    connection = connect(database_path)
    try:
        row = connection.execute(
            """SELECT ev.protocol_json, ev.definition_json, ev.definition_sha256
               FROM experiment_versions AS ev
               JOIN experiments AS e ON e.experiment_id = ev.experiment_id
               WHERE ev.experiment_id = ?
               ORDER BY ev.created_at DESC LIMIT 1""",
            (experiment_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        return {}
    return {
        "protocol": json.loads(row["protocol_json"]) if row["protocol_json"] else {},
        "definition_json": json.loads(row["definition_json"]) if row["definition_json"] else {},
        "definition_sha256": row["definition_sha256"],
    }


def _observation_key(row: dict[str, Any]) -> tuple[str, str]:
    """Stable key for one measured benchmark within one run.

    Unmapped rows have no benchmark_spec_id, so they are keyed by their raw
    benchmark name to keep them visible in the report instead of dropped.
    """
    spec_id = row["benchmark_spec_id"]
    if spec_id:
        return (row["run_id"], spec_id)
    return (row["run_id"], f"unmapped:{row['benchmark_name_raw']}")


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate iteration rows into a transparent per-benchmark summary."""
    by_spec: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row["run_type"] not in (None, "", "iteration"):
            continue
        if row["error_occurred"]:
            continue
        if not isinstance(row["real_time"], (int, float)):
            continue
        run_id, key = _observation_key(row)
        by_spec.setdefault((run_id, key, row["time_unit"] or "ns"), []).append(row)

    summaries: dict[str, Any] = {}
    for (run_id, key, unit), group in by_spec.items():
        values = [float(row["real_time"]) for row in group]
        throughput = [
            float(row["bytes_per_second"]) for row in group
            if isinstance(row["bytes_per_second"], (int, float))
        ]
        summaries.setdefault((run_id, key), {}).update(
            {
                "unit": unit,
                "iterations": len(values),
                "median_real_time": statistics.median(values),
                "mean_real_time": statistics.fmean(values),
                "min_real_time": min(values),
                "max_real_time": max(values),
                "stdev_real_time": statistics.stdev(values) if len(values) > 1 else None,
                "median_bytes_per_second": statistics.median(throughput) if throughput else None,
            }
        )
    return summaries


def build_report(
    *,
    database_path: Path,
    experiment_id: str,
    pair: tuple[str, str] | None = None,
    run_ids: list[str] | None = None,
) -> dict[str, Any]:
    rows = _load_rows(database_path, experiment_id)
    if run_ids:
        selected = set(run_ids)
        missing = selected - {row["run_id"] for row in rows}
        if missing:
            raise ReportError(
                "Run ID tidak ditemukan untuk eksperimen ini: "
                + ", ".join(sorted(missing))
            )
        rows = [row for row in rows if row["run_id"] in selected]
    protocol = _definition_protocol(database_path, experiment_id)
    summaries = _summarize(rows)

    unmapped = sorted(
        {
            (row["run_id"], row["benchmark_name_raw"], row["mapping_status"])
            for row in rows
            if row["mapping_status"] != "MAPPED"
        }
    )

    observations: list[dict[str, Any]] = []
    for (run_id, key), stats in sorted(summaries.items()):
        sample = next(
            row for row in rows
            if _observation_key(row) == (run_id, key)
        )
        observations.append(
            {
                "run_id": run_id,
                "benchmark_spec_id": sample["benchmark_spec_id"],
                "mapping_status": sample["mapping_status"],
                "name_pattern": sample["name_pattern"],
                "algorithm_id": sample["algorithm_id"],
                "algorithm_name": sample["algorithm_name"],
                "lifecycle_status": sample["lifecycle_status"],
                "comparison_role": sample["comparison_role"],
                "comparison_group": sample["comparison_group"],
                "workload_id": sample["workload_id"],
                "workload_name": sample["workload_name"],
                "dataset_path": sample["dataset_path"],
                "dataset_sha256": sample["input_sha256"] or sample["dataset_sha256"],
                "dataset_size_bytes": sample["input_size"],
                "started_at": sample["started_at"],
                "verification_status": sample["verification_status"],
                "manifest_sha256": sample["manifest_sha256"],
                "definition_sha256": sample["definition_sha256"],
                "gates": json.loads(sample["gates_json"]) if sample["gates_json"] else [],
                **stats,
            }
        )

    comparisons: list[dict[str, Any]] = []
    if pair:
        baseline_id, candidate_id = pair
        by_workload: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for observation in observations:
            if observation["algorithm_id"] in (baseline_id, candidate_id):
                by_workload.setdefault(observation["workload_id"], {}).setdefault(
                    observation["algorithm_id"], []
                ).append(observation)
        for workload_id, per_algorithm in sorted(by_workload.items()):
            baseline_rows = per_algorithm.get(baseline_id, [])
            candidate_rows = per_algorithm.get(candidate_id, [])
            if not baseline_rows or not candidate_rows:
                comparisons.append(
                    {
                        "workload_id": workload_id,
                        "status": "INCOMPLETE_PAIR",
                        "detail": "Salah satu sisi pasangan tidak memiliki hasil terukur.",
                        "baseline_run_ids": [row["run_id"] for row in baseline_rows],
                        "candidate_run_ids": [row["run_id"] for row in candidate_rows],
                    }
                )
                continue
            for baseline, candidate in zip(
                sorted(baseline_rows, key=lambda row: row["run_id"]),
                sorted(candidate_rows, key=lambda row: row["run_id"]),
            ):
                if baseline["unit"] != candidate["unit"]:
                    comparisons.append(
                        {
                            "workload_id": workload_id,
                            "status": "UNIT_MISMATCH",
                            "detail": f"{baseline['unit']} vs {candidate['unit']}; perbandingan ditolak.",
                            "baseline_run_ids": [baseline["run_id"]],
                            "candidate_run_ids": [candidate["run_id"]],
                        }
                    )
                    continue
                ratio = candidate["median_real_time"] / baseline["median_real_time"]
                comparisons.append(
                    {
                        "workload_id": workload_id,
                        "status": "COMPARED",
                        "unit": baseline["unit"],
                        "baseline_median_real_time": baseline["median_real_time"],
                        "candidate_median_real_time": candidate["median_real_time"],
                        "ratio_candidate_over_baseline": ratio,
                        "delta_percent": (ratio - 1.0) * 100.0,
                        "candidate_is_faster": ratio < 1.0,
                        "baseline_run_ids": [baseline["run_id"]],
                        "candidate_run_ids": [candidate["run_id"]],
                        "dataset_sha256": baseline["dataset_sha256"],
                        "gates": baseline["gates"] + candidate["gates"],
                    }
                )

    return {
        "report_version": REPORT_VERSION,
        "experiment_id": experiment_id,
        "generated_at": _now(),
        "pair": {"baseline": pair[0], "candidate": pair[1]} if pair else None,
        "protocol": protocol.get("protocol", {}),
        "definition_sha256": protocol.get("definition_sha256"),
        "run_ids": sorted({observation["run_id"] for observation in observations}),
        # A changed definition is a new experiment version, so a report that
        # mixes them is describing two different questions.
        "definition_sha256_by_run": {
            run_id: next(
                observation["definition_sha256"]
                for observation in observations
                if observation["run_id"] == run_id
            )
            for run_id in sorted({observation["run_id"] for observation in observations})
        },
        "definition_versions_in_report": len({
            observation["definition_sha256"] for observation in observations
        }),
        "aggregation": {
            "statistic": "median",
            "population": "run_type == 'iteration' rows, error rows excluded",
            "library": "python statistics.median",
        },
        "unmapped_rows": [dict(zip(("run_id", "benchmark_name", "mapping_status"), row)) for row in unmapped],
        "observation_count": len(observations),
        "observations": observations,
        "comparisons": comparisons,
    }


def record_analysis(
    *,
    database_path: Path,
    report: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    """Persist a report as an analysis run with typed derived results."""
    configuration = {
        "experiment_id": report["experiment_id"],
        "pair": report["pair"],
        "report_version": report["report_version"],
        "aggregation": report["aggregation"],
        "run_ids": report.get("run_ids")
        or sorted({row["run_id"] for row in report["observations"]}),
        "definition_sha256_by_run": report.get("definition_sha256_by_run", {}),
    }
    analysis_run_id = "analysis-" + hashlib.sha256(
        _json(configuration).encode("utf-8")
    ).hexdigest()[:24]

    migrate(database_path)
    with connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT analysis_run_id FROM analysis_runs WHERE analysis_run_id=?",
            (analysis_run_id,),
        ).fetchone()
        if existing is not None:
            connection.rollback()
            return {
                "analysis_run_id": analysis_run_id,
                "status": "ALREADY_RECORDED",
                "output_path": str(output_path),
            }
        connection.execute(
            """INSERT INTO analysis_runs(
                analysis_run_id, started_at, finished_at, analysis_version,
                configuration_json, status, output_path)
               VALUES(?,?,?,?,?,?,?)""",
            (
                analysis_run_id, report["generated_at"], _now(), REPORT_VERSION,
                _json(configuration), "COMPLETED", str(output_path),
            ),
        )
        provenance = {
            "experiment_id": report["experiment_id"],
            "definition_sha256": report["definition_sha256"],
            "generated_at": report["generated_at"],
        }
        for comparison in report["comparisons"]:
            if comparison["status"] != "COMPARED":
                continue
            connection.execute(
                """INSERT OR IGNORE INTO derived_results(
                    derived_result_id, analysis_run_id, result_type, result_key,
                    value_json, provenance_json)
                   VALUES(?,?,?,?,?,?)""",
                (
                    "derived-" + hashlib.sha256(
                        f"{analysis_run_id}|{comparison['workload_id']}".encode("utf-8")
                    ).hexdigest()[:24],
                    analysis_run_id,
                    "benchmark_pair_comparison",
                    f"{report['experiment_id']}|{comparison['workload_id']}",
                    _json(comparison),
                    _json(
                        {
                            **provenance,
                            "baseline_run_ids": comparison["baseline_run_ids"],
                            "candidate_run_ids": comparison["candidate_run_ids"],
                            "dataset_sha256": comparison.get("dataset_sha256"),
                        }
                    ),
                ),
            )
        for run_id in configuration["run_ids"] or []:
            connection.execute(
                "INSERT OR IGNORE INTO analysis_inputs(analysis_run_id, run_id) VALUES(?,?)",
                (analysis_run_id, run_id),
            )
        connection.commit()

    return {
        "analysis_run_id": analysis_run_id,
        "status": "RECORDED",
        "output_path": str(output_path),
    }


def export_payload(
    *, database_path: Path, experiment_id: str | None = None
) -> dict[str, Any]:
    """Notebook-friendly export carrying full provenance per row."""
    if experiment_id:
        experiments = [experiment_id]
    else:
        connection = connect(database_path)
        try:
            experiments = [
                row["experiment_id"]
                for row in connection.execute(
                    "SELECT DISTINCT experiment_id FROM experiments ORDER BY experiment_id"
                )
            ]
        finally:
            connection.close()

    sections = []
    skipped: list[dict[str, str]] = []
    for current in experiments:
        try:
            sections.append(build_report(database_path=database_path, experiment_id=current))
        except ReportError as exc:
            # An experiment with no benchmark rows (a correctness suite, for
            # example) has nothing to export. Skipping is correct; crashing is
            # not, because a plain "export everything" should still work.
            skipped.append({"experiment_id": current, "reason": str(exc)})
    return {
        "export_version": REPORT_VERSION,
        "generated_at": _now(),
        "experiment_count": len(sections),
        "experiments": sections,
        "skipped_experiments": skipped,
    }
