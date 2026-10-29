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


def test_sample_is_not_allowed_for_formal_run_before_review():
    errors = validate_registry(
        REGISTRY, experiment_id="scanner-width-comparison", for_run=True
    )
    assert any("approved_for_run" in error for error in errors)
    assert any("reviewed" in error for error in errors)


def test_basis_cannot_reference_unknown_source():
    _, document = next(iter([
        (path, load_yaml(path))
        for path in REGISTRY.glob("*.yaml")
    ]))
    document["experiment"]["research_basis"][0]["reference_ids"] = ["MISSING-REF"]
    errors = validate_experiment(document)
    assert any("reference ID yang tidak ada" in error for error in errors)


def test_definition_fingerprint_changes_when_research_question_changes():
    _, document = next(iter([
        (path, load_yaml(path))
        for path in REGISTRY.glob("*.yaml")
    ]))
    original = definition_sha256(document)
    document["experiment"]["research_question"] += " Revised."
    assert definition_sha256(document) != original


def test_duplicate_reference_ids_are_rejected():
    _, document = next(iter([
        (path, load_yaml(path))
        for path in REGISTRY.glob("*.yaml")
    ]))
    duplicate = dict(document["experiment"]["references"][0])
    document["experiment"]["references"].append(duplicate)
    errors = validate_experiment(document)
    assert any("ID duplikat" in error for error in errors)
