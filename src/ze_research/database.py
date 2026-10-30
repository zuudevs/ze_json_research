"""SQLite migrations and persistence for research metadata/results."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .registry import definition_sha256


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(database_path: Path) -> sqlite3.Connection:
    database_path = database_path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection


def migrate(database_path: Path) -> list[int]:
    applied_now: list[int] = []
    with connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum_sha256 TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )"""
        )
        applied = {
            int(row["version"]): row["checksum_sha256"]
            for row in connection.execute("SELECT version, checksum_sha256 FROM schema_migrations")
        }
        for migration_path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(migration_path.name.split("_", 1)[0])
            sql = migration_path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            if version in applied:
                if applied[version] != checksum:
                    raise RuntimeError(
                        f"Migrasi {migration_path.name} berubah setelah diterapkan. "
                        "Buat migrasi baru; jangan mengedit migrasi historis."
                    )
                continue
            escaped_name = migration_path.name.replace("'", "''")
            escaped_hash = checksum.replace("'", "''")
            script = (
                "BEGIN IMMEDIATE;\n"
                + sql
                + f"\nINSERT INTO schema_migrations(version, name, checksum_sha256) "
                f"VALUES ({version}, '{escaped_name}', '{escaped_hash}');\nCOMMIT;"
            )
            try:
                connection.executescript(script)
            except sqlite3.Error:
                connection.rollback()
                raise
            applied_now.append(version)
    return applied_now


def database_status(database_path: Path) -> dict[str, Any]:
    if not database_path.exists():
        return {"exists": False, "path": str(database_path.resolve()), "migrations": []}
    with connect(database_path) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        violations = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
        has_migrations = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        migrations = []
        if has_migrations:
            migrations = [
                dict(row) for row in connection.execute(
                    "SELECT version, name, checksum_sha256, applied_at "
                    "FROM schema_migrations ORDER BY version"
                )
            ]
        counts: dict[str, int] = {}
        for table in (
            "experiments", "experiment_versions", "research_runs", "executions",
            "artifacts", "benchmark_results", "test_results", "js_parse_results",
        ):
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if exists:
                counts[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        return {
            "exists": True,
            "path": str(database_path.resolve()),
            "integrity_check": integrity,
            "foreign_key_violations": violations,
            "migrations": migrations,
            "row_counts": counts,
        }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def persist_definition(connection: sqlite3.Connection, document: dict[str, Any]) -> str:
    """Persist immutable definition snapshot and its normalized catalog rows."""
    experiment = document["experiment"]
    exp_id = experiment["id"]
    fingerprint = definition_sha256(document)
    version_id = f"{exp_id}@{fingerprint[:16]}"
    connection.execute(
        """INSERT INTO experiments(experiment_id, title, status, categories_json)
           VALUES(?,?,?,?)
           ON CONFLICT(experiment_id) DO UPDATE SET title=excluded.title,
             status=excluded.status, categories_json=excluded.categories_json""",
        (exp_id, experiment["title"], experiment["status"], _json(experiment.get("categories", []))),
    )
    connection.execute(
        """INSERT OR IGNORE INTO experiment_versions(
               experiment_version_id, experiment_id, definition_sha256, schema_version,
               purpose, motivation, research_question, hypotheses_json, assumptions_json,
               limitations_json, protocol_json, definition_json)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            version_id, exp_id, fingerprint, str(document.get("schema_version", 1)),
            experiment["purpose"], experiment.get("motivation"), experiment["research_question"],
            _json(experiment.get("hypotheses", [])), _json(experiment.get("assumptions", [])),
            _json(experiment.get("limitations", [])), _json(experiment["protocol"]), _json(document),
        ),
    )

    ref_keys: dict[str, str] = {}
    for reference in experiment.get("references", []):
        reference_key = "ref-" + hashlib.sha256(
            f"{version_id}|{reference['id']}".encode("utf-8")
        ).hexdigest()[:24]
        ref_keys[reference["id"]] = reference_key
        connection.execute(
            """INSERT OR IGNORE INTO research_references(
                reference_key, experiment_version_id, source_id, source_type, title, url,
                doi, locator, accessed_at, evidence_note, review_status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                reference_key, version_id, reference["id"], reference["source_type"],
                reference["title"], reference.get("url"), reference.get("doi"),
                reference.get("locator"), reference.get("accessed_at"),
                reference["evidence_note"], reference["review_status"],
            ),
        )

    for basis in experiment.get("research_basis", []):
        basis_key = "basis-" + hashlib.sha256(
            f"{version_id}|{basis['id']}".encode("utf-8")
        ).hexdigest()[:24]
        connection.execute(
            """INSERT OR IGNORE INTO research_basis_items(
                basis_key, experiment_version_id, basis_id, basis_type, statement)
               VALUES(?,?,?,?,?)""",
            (basis_key, version_id, basis["id"], basis["basis_type"], basis["statement"]),
        )
        for ref_id in basis.get("reference_ids", []):
            connection.execute(
                "INSERT OR IGNORE INTO research_basis_references(basis_key, reference_key) VALUES(?,?)",
                (basis_key, ref_keys[ref_id]),
            )

    for variant in experiment.get("variants", []):
        connection.execute(
            """INSERT OR IGNORE INTO algorithm_variants(
                algorithm_id, name, description, lifecycle_status) VALUES(?,?,?,?)""",
            (variant["id"], variant.get("name", variant["id"]), variant.get("description", ""),
             variant.get("lifecycle_status", "experimental")),
        )
    for workload in experiment.get("workloads", []):
        connection.execute(
            """INSERT OR IGNORE INTO workload_cases(
                workload_id, name, dataset_path, dataset_sha256, parameters_json)
               VALUES(?,?,?,?,?)""",
            (workload["id"], workload.get("name", workload["id"]), workload.get("dataset_path"),
             workload.get("dataset_sha256"), _json(workload.get("parameters", {}))),
        )
    for contract in experiment.get("measurement_contracts", []):
        connection.execute(
            """INSERT OR IGNORE INTO measurement_contracts(
                contract_id, name, operation, includes_json, excludes_json) VALUES(?,?,?,?,?)""",
            (contract["id"], contract["name"], contract["operation"],
             _json(contract.get("includes", [])), _json(contract.get("excludes", []))),
        )

    comparison_group = experiment.get("comparison", {}).get("group")
    for variant in experiment.get("variants", []):
        # A variant may restrict itself to the workloads it really implements;
        # otherwise it is crossed with every declared workload.
        selected = variant.get("workloads")
        if selected is None:
            workloads = experiment.get("workloads", [])
        else:
            allowed = set(selected)
            workloads = [
                workload for workload in experiment.get("workloads", [])
                if workload.get("id") in allowed
            ]
        for workload in workloads:
            # Substitute the workload's benchmark token into the variant pattern
            # so that the stored pattern is an exact benchmark name. A wildcard
            # pattern would let one imported row match several specs, which is
            # how a synthetic case gets mislabelled as a real dataset.
            name_pattern = variant["benchmark_name_pattern"]
            token = workload.get("benchmark_token")
            if isinstance(token, str) and token:
                for placeholder in ("{dataset}", "{case}"):
                    if placeholder in name_pattern:
                        name_pattern = name_pattern.replace(placeholder, token)
            spec_id = "spec-" + hashlib.sha256(
                f"{version_id}|{variant['id']}|{workload['id']}|{variant['measurement_contract_id']}".encode("utf-8")
            ).hexdigest()[:24]
            connection.execute(
                """INSERT OR IGNORE INTO benchmark_specs(
                    benchmark_spec_id, experiment_version_id, algorithm_id, workload_id,
                    contract_id, name_pattern, comparison_role, comparison_group)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    spec_id, version_id, variant["id"], workload["id"],
                    variant["measurement_contract_id"], name_pattern,
                    variant["comparison_role"], comparison_group,
                ),
            )
    return version_id
