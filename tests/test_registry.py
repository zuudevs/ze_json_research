import datetime
import json
from pathlib import Path

from ze_research.registry import (
    definition_sha256,
    load_yaml,
    validate_experiment,
    validate_registry,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "research" / "experiments"


def test_sample_registry_is_structurally_valid():
    assert validate_registry(REGISTRY) == []


def test_formal_run_gate_opens_only_after_review_and_approval():
    """The reviewed sample is runnable, and the gate re-closes when revoked."""
    document = load_yaml(REGISTRY / "scanner-width.yaml")

    # Approved and reviewed definitions pass the formal-run gate.
    assert validate_experiment(document, for_run=True) == []

    # Withdrawing approval closes it again.
    withdrawn = json.loads(json.dumps(document))
    withdrawn["experiment"]["status"] = "draft"
    errors = validate_experiment(withdrawn, for_run=True)
    assert any("approved_for_run" in error for error in errors)

    # So does marking a reference as unreviewed.
    unreviewed = json.loads(json.dumps(document))
    for reference in unreviewed["experiment"]["references"]:
        reference["review_status"] = "unreviewed"
    errors = validate_experiment(unreviewed, for_run=True)
    assert any("belum berstatus reviewed" in error for error in errors)


def test_workload_token_is_required_for_a_dataset_pattern():
    document = load_yaml(REGISTRY / "scanner-width.yaml")
    broken = json.loads(json.dumps(document))
    for workload in broken["experiment"]["workloads"]:
        workload.pop("benchmark_token", None)
    errors = validate_experiment(broken)
    assert any("benchmark_token" in error for error in errors)


def test_literal_pattern_is_rejected_for_multiple_workloads():
    document = load_yaml(REGISTRY / "correctness" / "gtest-suite.yaml")
    broken = json.loads(json.dumps(document))
    broken["experiment"]["variants"][0]["benchmark_name_pattern"] = "CorrectnessSuite"
    broken["experiment"]["workloads"] = [
        {"id": "corpus", "name": "corpus", "benchmark_token": "Corpus",
         "dataset_path": "samples/JSON_checker", "dataset_sha256": "0" * 64,
         "parameters": {}},
        {"id": "commons", "name": "commons", "benchmark_token": "Commons",
         "dataset_path": "samples/commons", "dataset_sha256": "0" * 64,
         "parameters": {}},
    ]
    errors = validate_experiment(broken)
    assert any("ambigu" in error for error in errors)


def test_yaml_dates_do_not_break_the_definition_fingerprint():
    document = {
        "schema_version": 1,
        "experiment": {
            "id": "date-probe",
            "references": [
                {
                    "id": "REF-1",
                    "source_type": "source_code",
                    "title": "probe",
                    "evidence_note": "probe",
                    "review_status": "reviewed",
                    "url": "https://example.invalid",
                    "accessed_at": datetime.date(2026, 10, 30),
                }
            ],
        },
    }
    # An unquoted YAML date becomes a date object; fingerprinting must not crash.
    digest = definition_sha256(document)
    assert len(digest) == 64
    assert definition_sha256(document) == digest


def test_basis_cannot_reference_unknown_source():
    _, document = next(iter([
        (path, load_yaml(path))
        for path in REGISTRY.rglob("*.yaml")
    ]))
    document["experiment"]["research_basis"][0]["reference_ids"] = ["MISSING-REF"]
    errors = validate_experiment(document)
    assert any("reference ID yang tidak ada" in error for error in errors)


def test_definition_fingerprint_changes_when_research_question_changes():
    _, document = next(iter([
        (path, load_yaml(path))
        for path in REGISTRY.rglob("*.yaml")
    ]))
    original = definition_sha256(document)
    document["experiment"]["research_question"] += " Revised."
    assert definition_sha256(document) != original


def test_duplicate_reference_ids_are_rejected():
    _, document = next(iter([
        (path, load_yaml(path))
        for path in REGISTRY.rglob("*.yaml")
    ]))
    duplicate = dict(document["experiment"]["references"][0])
    document["experiment"]["references"].append(duplicate)
    errors = validate_experiment(document)
    assert any("ID duplikat" in error for error in errors)
