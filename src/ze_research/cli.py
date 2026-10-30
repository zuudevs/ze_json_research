"""Command-line interface for local research management."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .database import connect, database_status, migrate
from .ingest import IngestError, ingest_manifest
from .manifest import verify_manifest
from .paths import (
    PathConfigurationError,
    init_data_layout,
    research_repo_root,
    resolve_data_dir,
    resolve_source_dir,
    validate_external_data_dir,
)
from .registry import RegistryError, definition_sha256, find_experiment, load_yaml, validate_registry
from .report import (
    ReportError,
    build_report,
    export_payload,
    record_analysis,
)
from .runner import RunnerError, run_experiment


def _registry_dir(value: str | None) -> Path:
    return Path(value).expanduser().resolve() if value else research_repo_root() / "research" / "experiments"


def _manifest_path(args: argparse.Namespace, data_dir: Path) -> Path:
    if getattr(args, "manifest", None):
        return Path(args.manifest).expanduser().resolve()
    if getattr(args, "run_id", None):
        if not args.run_id or "/" in args.run_id or "\\" in args.run_id or args.run_id in {".", ".."}:
            raise ValueError("run_id tidak valid.")
        return data_dir / "runs" / args.run_id / "manifest.json"
    raise ValueError("Berikan --manifest atau --run-id.")


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _cmd_init(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    source_dir = resolve_source_dir(args.source_dir) if (
        args.source_dir or os.environ.get("ZE_JSON_SOURCE_DIR")
    ) else None
    validate_external_data_dir(data_dir, source_dir=source_dir)
    init_data_layout(data_dir)
    database_path = data_dir / "database" / "ze_json_research.sqlite"
    applied = migrate(database_path)
    _print_json({
        "status": "INITIALIZED",
        "data_dir": str(data_dir),
        "database": str(database_path),
        "migrations_applied": applied,
    })
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    messages = validate_registry(
        _registry_dir(args.registry_dir),
        experiment_id=args.experiment,
        for_run=args.for_run,
    )
    if messages:
        print("VALIDATION FAILED", file=sys.stderr)
        for message in messages:
            print(f"- {message}", file=sys.stderr)
        return 1
    print("VALIDATION PASSED")
    if args.for_run:
        print("Pemeriksaan formal lulus; ini bukan bukti bahwa run atau klaim ilmiah sudah benar.")
    return 0


def _cmd_list_experiments(args: argparse.Namespace) -> int:
    directory = _registry_dir(args.registry_dir)
    result = []
    for path in sorted([*directory.rglob("*.yaml"), *directory.rglob("*.yml")]):
        try:
            document = load_yaml(path)
            experiment = document["experiment"]
            result.append({
                "id": experiment["id"],
                "title": experiment["title"],
                "status": experiment.get("status"),
                "definition_sha256": definition_sha256(document),
                "file": str(path),
            })
        except (RegistryError, KeyError, TypeError) as exc:
            result.append({"file": str(path), "status": "INVALID", "error": str(exc)})
    _print_json(result)
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    source_dir = resolve_source_dir(args.source_dir)
    validate_external_data_dir(data_dir, source_dir=source_dir)
    registry_dir = _registry_dir(args.registry_dir)
    messages = validate_registry(registry_dir, experiment_id=args.experiment, for_run=True)
    if messages:
        raise RunnerError("Registry tidak mengizinkan run formal:\n- " + "\n- ".join(messages))
    experiment_path, document = find_experiment(registry_dir, args.experiment)
    manifest_path = run_experiment(
        document, source_dir=source_dir, data_dir=data_dir, registry_path=experiment_path
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _print_json({
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "manifest": str(manifest_path),
        "artifact_count": len(manifest.get("artifacts", [])),
        "missing_expected_outputs": manifest.get("missing_expected_outputs", []),
    })
    return 0 if manifest["status"] == "COMPLETED" else 2


def _cmd_verify(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    source_dir = (
        resolve_source_dir(None)
        if os.environ.get("ZE_JSON_SOURCE_DIR") else None
    )
    validate_external_data_dir(data_dir, source_dir=source_dir)
    manifest_path = _manifest_path(args, data_dir)
    result = verify_manifest(manifest_path)
    _print_json(result)
    return 0 if result["valid"] else 2


def _cmd_ingest(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    source_dir = resolve_source_dir(None) if os.environ.get("ZE_JSON_SOURCE_DIR") else None
    validate_external_data_dir(data_dir, source_dir=source_dir)
    manifest_path = _manifest_path(args, data_dir)
    database_path = (
        Path(args.database).expanduser().resolve()
        if args.database else data_dir / "database" / "ze_json_research.sqlite"
    )
    validate_external_data_dir(database_path.parent, source_dir=source_dir)
    result = ingest_manifest(manifest_path, database_path, verify=not args.skip_verify)
    _print_json(result)
    if args.strict and result.get("status") == "IMPORTED":
        with connect(database_path) as connection:
            unmapped = [
                dict(row)
                for row in connection.execute(
                    """SELECT ex.run_id, br.benchmark_name_raw, br.mapping_status
                       FROM benchmark_results AS br
                       JOIN executions AS ex ON ex.execution_id = br.execution_id
                       WHERE ex.run_id = ? AND br.mapping_status <> 'MAPPED'""",
                    (result["run_id"],),
                )
            ]
        if unmapped:
            print(
                "STRICT INGEST GAGAL: ada baris hasil yang tidak terpetakan ke satu varian.",
                file=sys.stderr,
            )
            for row in unmapped:
                print(
                    f"- {row['run_id']}: {row['benchmark_name_raw']} ({row['mapping_status']})",
                    file=sys.stderr,
                )
            return 2
    return 0


def _cmd_db_status(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    source_dir = resolve_source_dir(None) if os.environ.get("ZE_JSON_SOURCE_DIR") else None
    validate_external_data_dir(data_dir, source_dir=source_dir)
    result = database_status(data_dir / "database" / "ze_json_research.sqlite")
    _print_json(result)
    if result.get("exists") and (
        result.get("integrity_check") != "ok" or result.get("foreign_key_violations")
    ):
        return 2
    return 0


def _cmd_list_runs(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    source_dir = resolve_source_dir(None) if os.environ.get("ZE_JSON_SOURCE_DIR") else None
    validate_external_data_dir(data_dir, source_dir=source_dir)
    database_path = data_dir / "database" / "ze_json_research.sqlite"
    if not database_path.exists():
        print("Database belum dibuat. Jalankan ze-research init terlebih dahulu.", file=sys.stderr)
        return 2
    with connect(database_path) as connection:
        rows = connection.execute(
            """SELECT run_id, experiment_version_id, started_at, finished_at, status,
                      verification_status, manifest_path
               FROM research_runs ORDER BY started_at DESC"""
        ).fetchall()
    _print_json([dict(row) for row in rows])
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    source_dir = resolve_source_dir(None) if os.environ.get("ZE_JSON_SOURCE_DIR") else None
    validate_external_data_dir(data_dir, source_dir=source_dir)
    database_path = (
        Path(args.database).expanduser().resolve()
        if args.database else data_dir / "database" / "ze_json_research.sqlite"
    )
    validate_external_data_dir(database_path.parent, source_dir=source_dir)

    pair: tuple[str, str] | None = None
    if args.pair:
        parts = [part.strip() for part in args.pair.split(":", 1)]
        if len(parts) != 2 or not all(parts):
            print("ERROR: --pair harus berformat baseline:candidate.", file=sys.stderr)
            return 2
        pair = (parts[0], parts[1])

    report = build_report(
        database_path=database_path, experiment_id=args.experiment, pair=pair
    )
    destination = (
        Path(args.output).expanduser().resolve()
        if args.output else data_dir / "reports" / f"{args.experiment}-report.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    record_analysis(database_path=database_path, report=report, output_path=destination)
    _print_json({
        "experiment_id": args.experiment,
        "pair": report["pair"],
        "observation_count": report["observation_count"],
        "comparison_count": len(report["comparisons"]),
        "unmapped_row_count": len(report["unmapped_rows"]),
        "unverifiable_comparisons": [
            comparison["workload_id"]
            for comparison in report["comparisons"]
            if comparison["status"] != "COMPARED"
        ],
        "output": str(destination),
    })
    if report["unmapped_rows"] or any(
        comparison["status"] != "COMPARED" for comparison in report["comparisons"]
    ):
        print(
            "Laporan dihasilkan, tetapi datanya belum dapat dipertanggungjawabkan "
            "sepenuhnya: ada baris tak terpetakan atau pasangan tak lengkap.",
            file=sys.stderr,
        )
        return 1
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    data_dir = resolve_data_dir(args.data_dir)
    source_dir = resolve_source_dir(None) if os.environ.get("ZE_JSON_SOURCE_DIR") else None
    validate_external_data_dir(data_dir, source_dir=source_dir)
    database_path = (
        Path(args.database).expanduser().resolve()
        if args.database else data_dir / "database" / "ze_json_research.sqlite"
    )
    validate_external_data_dir(database_path.parent, source_dir=source_dir)
    payload = export_payload(database_path=database_path, experiment_id=args.experiment)
    destination = (
        Path(args.output).expanduser().resolve()
        if args.output else data_dir / "reports" / "research-export.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _print_json({"output": str(destination), "experiment_count": payload["experiment_count"]})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ze-research",
        description="Local-first research registry, runner, artifact verifier, and SQLite store.",
    )
    parser.add_argument("--version", action="version", version=f"ze_json_research {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create external data layout and initialize SQLite")
    init_parser.add_argument("--data-dir", help="External data directory; defaults to ZE_JSON_RESEARCH_HOME")
    init_parser.add_argument("--source-dir", help="Optional ze_json source root for the separation check")
    init_parser.set_defaults(handler=_cmd_init)

    validate_parser = subparsers.add_parser("validate", help="Validate registry definitions")
    validate_parser.add_argument("--registry-dir", help="Directory of experiment YAML files")
    validate_parser.add_argument("--experiment", help="Validate one experiment ID")
    validate_parser.add_argument("--for-run", action="store_true", help="Require reviewed references and formal approval")
    validate_parser.set_defaults(handler=_cmd_validate)

    list_exp_parser = subparsers.add_parser("list-experiments", help="List registered experiments")
    list_exp_parser.add_argument("--registry-dir", help="Directory of experiment YAML files")
    list_exp_parser.set_defaults(handler=_cmd_list_experiments)

    run_parser = subparsers.add_parser("run", help="Build and run a formally approved experiment")
    run_parser.add_argument("--experiment", required=True, help="Experiment ID from registry")
    run_parser.add_argument("--source-dir", help="ze_json repository root; defaults to ZE_JSON_SOURCE_DIR")
    run_parser.add_argument("--data-dir", help="External data directory; defaults to ZE_JSON_RESEARCH_HOME")
    run_parser.add_argument("--registry-dir", help="Directory of experiment YAML files")
    run_parser.set_defaults(handler=_cmd_run)

    verify_parser = subparsers.add_parser("verify", help="Verify manifest, executable, and artifact hashes")
    verify_parser.add_argument("--run-id", help="Run ID under the data directory")
    verify_parser.add_argument("--manifest", help="Explicit manifest path")
    verify_parser.add_argument("--data-dir", help="External data directory")
    verify_parser.set_defaults(handler=_cmd_verify)

    ingest_parser = subparsers.add_parser("ingest", help="Verify and import a manifest into SQLite")
    ingest_parser.add_argument("--run-id", help="Run ID under the data directory")
    ingest_parser.add_argument("--manifest", help="Explicit manifest path")
    ingest_parser.add_argument("--data-dir", help="External data directory")
    ingest_parser.add_argument("--database", help="Optional explicit SQLite path")
    ingest_parser.add_argument(
        "--skip-verify", action="store_true",
        help="Skip artifact verification for controlled recovery/debugging only",
    )
    ingest_parser.add_argument(
        "--strict", action="store_true",
        help="Fail the import when benchmark rows cannot be mapped to exactly one variant",
    )
    ingest_parser.set_defaults(handler=_cmd_ingest)

    status_parser = subparsers.add_parser("db-status", help="Show SQLite integrity, migrations, and counts")
    status_parser.add_argument("--data-dir", help="External data directory")
    status_parser.set_defaults(handler=_cmd_db_status)

    runs_parser = subparsers.add_parser("list-runs", help="List imported research runs")
    runs_parser.add_argument("--data-dir", help="External data directory")
    runs_parser.set_defaults(handler=_cmd_list_runs)

    report_parser = subparsers.add_parser(
        "report", help="Build a traceable comparison from ingested runs"
    )
    report_parser.add_argument("--experiment", required=True, help="Experiment ID to report on")
    report_parser.add_argument(
        "--pair", help="Comparison pair as baseline:candidate variant IDs"
    )
    report_parser.add_argument("--data-dir", help="External data directory")
    report_parser.add_argument("--database", help="Optional explicit SQLite path")
    report_parser.add_argument("--output", help="Report output path")
    report_parser.set_defaults(handler=_cmd_report)

    export_parser = subparsers.add_parser(
        "export", help="Export ingested runs with provenance for analysis notebooks"
    )
    export_parser.add_argument("--experiment", help="Limit the export to one experiment ID")
    export_parser.add_argument("--data-dir", help="External data directory")
    export_parser.add_argument("--database", help="Optional explicit SQLite path")
    export_parser.add_argument("--output", help="Export output path")
    export_parser.set_defaults(handler=_cmd_export)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.handler(args))
    except (
        PathConfigurationError, RegistryError, RunnerError, IngestError,
        sqlite3.Error, OSError, RuntimeError, ValueError, KeyError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
