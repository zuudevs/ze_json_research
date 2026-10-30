"""Load, fingerprint, and validate YAML experiment registry definitions."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml


ALLOWED_STATUSES = {"draft", "reviewed", "approved_for_run", "retired"}
ALLOWED_REFERENCE_REVIEWS = {"unreviewed", "reviewed", "rejected"}
ALLOWED_ROLES = {"baseline", "candidate", "control", "replica", "other"}
ALLOWED_EXECUTION_MODES = {"native", "script"}
ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$")
DATASET_PLACEHOLDER = "{dataset}"


class RegistryError(ValueError):
    """Raised when registry data cannot be loaded or selected."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def definition_sha256(document: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RegistryError(f"Tidak dapat membaca YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RegistryError(f"Root YAML harus berupa object/map: {path}")
    return value


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _unique_ids(items: Any, label: str, errors: list[str]) -> set[str]:
    if not isinstance(items, list):
        errors.append(f"{label} harus berupa array/list.")
        return set()
    found: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"{label}[{index}] harus berupa object/map.")
            continue
        item_id = item.get("id")
        if not _nonempty(item_id) or not ID_PATTERN.fullmatch(item_id):
            errors.append(f"{label}[{index}].id wajib berupa ID valid.")
            continue
        if item_id in found:
            errors.append(f"ID duplikat {item_id!r} pada {label}.")
        found.add(item_id)
    return found


def validate_experiment(
    document: dict[str, Any], *, for_run: bool = False
) -> list[str]:
    errors: list[str] = []
    if document.get("schema_version") != 1:
        errors.append("schema_version harus bernilai 1.")

    experiment = document.get("experiment")
    if not isinstance(experiment, dict):
        return errors + ["Field experiment wajib berupa object/map."]

    for field in ("id", "title", "purpose", "research_question"):
        if not _nonempty(experiment.get(field)):
            errors.append(f"experiment.{field} wajib berupa string nonkosong.")
    experiment_id = experiment.get("id", "<unknown>")

    if _nonempty(experiment.get("id")) and not ID_PATTERN.fullmatch(experiment["id"]):
        errors.append("experiment.id memiliki format yang tidak valid.")

    status = experiment.get("status")
    if status not in ALLOWED_STATUSES:
        errors.append(
            "experiment.status harus salah satu dari: "
            + ", ".join(sorted(ALLOWED_STATUSES))
        )

    categories = experiment.get("categories")
    if not isinstance(categories, list) or not categories or not all(_nonempty(x) for x in categories):
        errors.append("experiment.categories wajib berupa array string nonkosong.")

    references = experiment.get("references")
    reference_ids = _unique_ids(references, "experiment.references", errors)
    if isinstance(references, list):
        for index, reference in enumerate(references):
            if not isinstance(reference, dict):
                continue
            for field in ("source_type", "title", "evidence_note", "review_status"):
                if not _nonempty(reference.get(field)):
                    errors.append(f"experiment.references[{index}].{field} wajib diisi.")
            review_status = reference.get("review_status")
            if review_status not in ALLOWED_REFERENCE_REVIEWS:
                errors.append(f"experiment.references[{index}].review_status tidak dikenal.")
            url = reference.get("url")
            doi = reference.get("doi")
            source_type = reference.get("source_type")
            if url is not None:
                parsed = urlparse(str(url))
                if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                    errors.append(f"URL referensi tidak valid: {url!r}")
            if not url and not doi and source_type not in {
                "internal_artifact", "git_commit", "local_file", "source_code"
            }:
                errors.append(
                    f"Referensi {reference.get('id', index)!r} perlu url, doi, "
                    "atau locator sumber internal yang eksplisit."
                )

    basis_items = experiment.get("research_basis")
    _unique_ids(basis_items, "experiment.research_basis", errors)
    if isinstance(basis_items, list):
        for index, basis in enumerate(basis_items):
            if not isinstance(basis, dict):
                continue
            if not _nonempty(basis.get("basis_type")) or not _nonempty(basis.get("statement")):
                errors.append(
                    f"experiment.research_basis[{index}] wajib memiliki basis_type dan statement."
                )
            refs = basis.get("reference_ids")
            if not isinstance(refs, list) or not refs:
                errors.append(
                    f"experiment.research_basis[{index}].reference_ids wajib berupa array nonkosong."
                )
            else:
                for ref_id in refs:
                    if ref_id not in reference_ids:
                        errors.append(
                            f"Basis {basis.get('id')!r} merujuk reference ID yang tidak ada: {ref_id!r}."
                        )

    hypotheses = experiment.get("hypotheses", [])
    hypothesis_ids = _unique_ids(hypotheses, "experiment.hypotheses", errors)
    if isinstance(hypotheses, list):
        for index, hypothesis in enumerate(hypotheses):
            if isinstance(hypothesis, dict) and not _nonempty(hypothesis.get("statement")):
                errors.append(f"experiment.hypotheses[{index}].statement wajib diisi.")

    for field in ("assumptions", "limitations"):
        value = experiment.get(field, [])
        if not isinstance(value, list) or not all(_nonempty(item) for item in value):
            errors.append(f"experiment.{field} harus berupa array string.")

    variants = experiment.get("variants")
    variant_ids = _unique_ids(variants, "experiment.variants", errors)
    workloads = experiment.get("workloads")
    workload_ids = _unique_ids(workloads, "experiment.workloads", errors)
    contracts = experiment.get("measurement_contracts")
    contract_ids = _unique_ids(contracts, "experiment.measurement_contracts", errors)

    if not isinstance(variants, list) or not variants:
        errors.append("experiment.variants wajib memuat minimal satu varian.")
    if not isinstance(workloads, list) or not workloads:
        errors.append("experiment.workloads wajib memuat minimal satu workload.")
    if not isinstance(contracts, list) or not contracts:
        errors.append("experiment.measurement_contracts wajib memuat minimal satu kontrak.")

    for index, variant in enumerate(variants if isinstance(variants, list) else []):
        if not isinstance(variant, dict):
            continue
        role = variant.get("comparison_role")
        if role not in ALLOWED_ROLES:
            errors.append(f"experiment.variants[{index}].comparison_role tidak dikenal.")
        contract_id = variant.get("measurement_contract_id")
        if contract_id not in contract_ids:
            errors.append(f"Varian {variant.get('id')!r} merujuk kontrak yang tidak ada.")
        if not _nonempty(variant.get("benchmark_name_pattern")):
            errors.append(f"Varian {variant.get('id')!r} wajib memiliki benchmark_name_pattern.")

    for index, contract in enumerate(contracts if isinstance(contracts, list) else []):
        if not isinstance(contract, dict):
            continue
        for field in ("name", "operation"):
            if not _nonempty(contract.get(field)):
                errors.append(f"experiment.measurement_contracts[{index}].{field} wajib diisi.")
        for field in ("includes", "excludes"):
            if not isinstance(contract.get(field), list) or not all(
                _nonempty(item) for item in contract.get(field, [])
            ):
                errors.append(
                    f"experiment.measurement_contracts[{index}].{field} harus berupa array string."
                )

    # Workload-level benchmark token: turns a templated variant name pattern
    # into an exact benchmark name so one imported row maps to exactly one spec.
    workload_tokens: set[str] = set()
    for index, workload in enumerate(workloads if isinstance(workloads, list) else []):
        if not isinstance(workload, dict):
            continue
        token = workload.get("benchmark_token")
        if token is None:
            continue
        if not _nonempty(token):
            errors.append(
                f"experiment.workloads[{index}].benchmark_token wajib berupa string nonkosong."
            )
            continue
        workload_tokens.add(workload["id"])

    variant_list = variants if isinstance(variants, list) else []
    workload_list = workloads if isinstance(workloads, list) else []
    for variant in variant_list:
        if not isinstance(variant, dict):
            continue
        pattern = variant.get("benchmark_name_pattern")
        if not _nonempty(pattern):
            continue
        if DATASET_PLACEHOLDER in pattern:
            for workload in workload_list:
                if not isinstance(workload, dict) or workload.get("id") not in workload_tokens:
                    errors.append(
                        f"Varian {variant.get('id')!r} memakai pola {DATASET_PLACEHOLDER}; "
                        f"workload {workload.get('id')!r} wajib memiliki benchmark_token "
                        "agar pemetaan hasil ke varian bersifat satu-ke-satu."
                    )
        elif len(workload_list) > 1:
            errors.append(
                f"Varian {variant.get('id')!r} memakai pola literal tanpa {DATASET_PLACEHOLDER} "
                f"padahal eksperimen memiliki {len(workload_list)} workload; hasil pemetaan akan ambigu."
            )

    metrics = experiment.get("metrics")
    primary = metrics.get("primary") if isinstance(metrics, dict) else None
    if not isinstance(primary, dict):
        errors.append("experiment.metrics.primary wajib berupa object/map.")
    else:
        for field in ("name", "unit", "direction"):
            if not _nonempty(primary.get(field)):
                errors.append(f"experiment.metrics.primary.{field} wajib diisi.")

    protocol = experiment.get("protocol")
    if not isinstance(protocol, dict) or not _nonempty(protocol.get("design")):
        errors.append("experiment.protocol.design wajib diisi.")

    comparison = experiment.get("comparison", {})
    if comparison and not isinstance(comparison, dict):
        errors.append("experiment.comparison harus berupa object/map.")
    elif isinstance(comparison, dict):
        for role in ("baseline", "candidate"):
            if role in comparison and comparison[role] not in variant_ids:
                errors.append(f"comparison.{role} merujuk varian yang tidak dikenal.")

    execution = experiment.get("execution")
    if not isinstance(execution, dict):
        errors.append("experiment.execution wajib berupa object/map.")
    else:
        mode = execution.get("mode", "native")
        if mode not in ALLOWED_EXECUTION_MODES:
            errors.append(
                "experiment.execution.mode harus salah satu dari: "
                + ", ".join(sorted(ALLOWED_EXECUTION_MODES))
            )
        elif mode == "script":
            for field in ("configure_command", "build_command"):
                if field in execution:
                    errors.append(
                        f"experiment.execution.{field} tidak digunakan pada mode 'script'."
                    )
            if "executable_path" in execution and execution.get("executable_path"):
                errors.append(
                    "experiment.execution.executable_path tidak digunakan pada mode 'script'."
                )

        steps = execution.get("steps")
        legacy_commands = {}
        if steps is not None:
            if not isinstance(steps, list) or not steps:
                errors.append("experiment.execution.steps wajib berupa array nonkosong.")
            else:
                seen_step_ids: set[str] = set()
                for index, step in enumerate(steps):
                    label = f"experiment.execution.steps[{index}]"
                    if not isinstance(step, dict):
                        errors.append(f"{label} harus berupa object/map.")
                        continue
                    step_id = step.get("id") or step.get("purpose") or f"step{index + 1}"
                    if not _nonempty(step_id):
                        errors.append(f"{label}.id wajib berupa string nonkosong.")
                    elif step_id in seen_step_ids:
                        errors.append(f"experiment.execution.steps memiliki id duplikat: {step_id!r}.")
                    else:
                        seen_step_ids.add(step_id)
                    command = step.get("command")
                    if not isinstance(command, list) or not command or not all(
                        isinstance(arg, str) and arg for arg in command
                    ):
                        errors.append(f"{label}.command wajib berupa array argumen string nonkosong.")
                    cwd = step.get("cwd", "source_dir")
                    if cwd not in {"source_dir", "run_dir"}:
                        errors.append(f"{label}.cwd harus 'source_dir' atau 'run_dir'.")
        else:
            for field in ("configure_command", "build_command", "run_command"):
                legacy_commands[field] = execution.get(field)
            if mode == "native":
                for field, command in legacy_commands.items():
                    if not isinstance(command, list) or not command or not all(
                        isinstance(arg, str) and arg for arg in command
                    ):
                        errors.append(
                            f"experiment.execution.{field} wajib berupa array argumen string nonkosong "
                            "saat execution.steps tidak digunakan."
                        )
            elif "run_command" not in execution:
                errors.append("experiment.execution.run_command wajib ada pada mode 'script'.")

        passthrough = execution.get("env_passthrough", [])
        if not isinstance(passthrough, list) or not all(_nonempty(name) for name in passthrough):
            errors.append("experiment.execution.env_passthrough harus berupa array string.")

        multi = execution.get("executable_paths")
        if multi is not None and (
            not isinstance(multi, list)
            or not multi
            or not all(_nonempty(template) for template in multi)
        ):
            errors.append(
                "experiment.execution.executable_paths wajib berupa array template path nonkosong."
            )
        single = execution.get("executable_path")
        if single is not None and not _nonempty(single):
            errors.append("experiment.execution.executable_path wajib berupa string nonkosong.")

        outputs = execution.get("expected_outputs")
        if not isinstance(outputs, list) or not outputs or not all(_nonempty(p) for p in outputs):
            errors.append("experiment.execution.expected_outputs wajib berupa array path nonkosong.")

    gate = experiment.get("correctness_gate")
    if gate not in (None, False):
        if not isinstance(gate, dict):
            errors.append("experiment.correctness_gate harus berupa object/map.")
        else:
            if not _nonempty(gate.get("experiment_id")):
                errors.append("experiment.correctness_gate.experiment_id wajib berupa string nonkosong.")
            required_status = gate.get("require_verification_status", "VERIFIED")
            if not _nonempty(required_status):
                errors.append(
                    "experiment.correctness_gate.require_verification_status wajib berupa string nonkosong."
                )

    if for_run:
        if status != "approved_for_run":
            errors.append(
                f"Eksperimen {experiment_id!r} harus berstatus 'approved_for_run' untuk run formal."
            )
        if not isinstance(basis_items, list) or not basis_items:
            errors.append("Run formal membutuhkan minimal satu research_basis.")
        if isinstance(references, list):
            for reference in references:
                if isinstance(reference, dict) and reference.get("review_status") != "reviewed":
                    errors.append(
                        f"Referensi {reference.get('id')!r} belum berstatus reviewed."
                    )

    return errors


def load_experiments(registry_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not registry_dir.is_dir():
        raise RegistryError(f"Direktori registry tidak ditemukan: {registry_dir}")
    # Recursive: experiments may be grouped into subdirectories by domain
    # (for example experiments/benchmark/ and experiments/correctness/).
    paths = sorted({*registry_dir.rglob("*.yaml"), *registry_dir.rglob("*.yml")})
    return [(path, load_yaml(path)) for path in paths]


def validate_registry(
    registry_dir: Path, *, experiment_id: str | None = None, for_run: bool = False
) -> list[str]:
    try:
        entries = load_experiments(registry_dir)
    except RegistryError as exc:
        return [str(exc)]
    if not entries:
        return [f"Tidak ada file YAML eksperimen di {registry_dir}."]

    errors: list[str] = []
    seen: set[str] = set()
    selected = 0
    for path, document in entries:
        definition = document.get("experiment")
        current_id = definition.get("id") if isinstance(definition, dict) else None
        if experiment_id and current_id != experiment_id:
            continue
        selected += 1
        errors.extend(f"{path.name}: {message}" for message in validate_experiment(document, for_run=for_run))
        if isinstance(current_id, str):
            if current_id in seen:
                errors.append(f"ID eksperimen duplikat {current_id!r}.")
            seen.add(current_id)
    if experiment_id and selected == 0:
        errors.append(f"Eksperimen {experiment_id!r} tidak ditemukan di {registry_dir}.")
    return errors


def find_experiment(
    registry_dir: Path, experiment_id: str
) -> tuple[Path, dict[str, Any]]:
    matches = []
    for path, document in load_experiments(registry_dir):
        experiment = document.get("experiment")
        if isinstance(experiment, dict) and experiment.get("id") == experiment_id:
            matches.append((path, document))
    if len(matches) != 1:
        raise RegistryError(
            f"Diharapkan tepat satu eksperimen {experiment_id!r}; ditemukan {len(matches)}."
        )
    errors = validate_experiment(matches[0][1])
    if errors:
        raise RegistryError("Registry tidak valid:\n- " + "\n- ".join(errors))
    return matches[0]
