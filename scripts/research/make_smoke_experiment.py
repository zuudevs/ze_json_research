#!/usr/bin/env python3
"""Generate a self-contained experiment for CI smoke runs.

The real experiments need CMake, vcpkg, and the ze_json checkout, which the
research repository CI does not have. This generator writes a small
``script``-mode experiment whose steps are pure Python, so the whole
validate -> run -> verify -> ingest -> report path is exercised end to end
without any C++ toolchain.

Usage:

    python scripts/research/make_smoke_experiment.py <output.yaml>
"""

from __future__ import annotations

import sys
from pathlib import Path

# The runner expands {placeholders} and treats {{ }} as literal braces, so the
# Python payload below has to double its own braces.
SMOKE_STEP = (
    "import json; from pathlib import Path; "
    "p = Path(r'{run_dir}/raw/smoke_benchmark.json'); "
    "p.parent.mkdir(parents=True, exist_ok=True); "
    "rows = ["
    "  dict(name='BM_Fake_Baseline_Smoke', run_type='iteration', iterations=10, "
    "       real_time=20.0, cpu_time=20.0, time_unit='ns', bytes_per_second=5.0e7, "
    "       error_occurred=False), "
    "  dict(name='BM_Fake_Baseline_Smoke', run_type='iteration', iterations=10, "
    "       real_time=22.0, cpu_time=22.0, time_unit='ns', bytes_per_second=5.0e7, "
    "       error_occurred=False), "
    "  dict(name='BM_Fake_Candidate_Smoke', run_type='iteration', iterations=10, "
    "       real_time=11.0, cpu_time=11.0, time_unit='ns', bytes_per_second=9.0e7, "
    "       error_occurred=False), "
    "  dict(name='BM_Fake_Candidate_Smoke', run_type='iteration', iterations=10, "
    "       real_time=13.0, cpu_time=13.0, time_unit='ns', bytes_per_second=9.0e7, "
    "       error_occurred=False), "
    "]; "
    "p.write_text(json.dumps({{'benchmarks': rows}}), encoding='utf-8')"
)

TEMPLATE = """schema_version: 1

experiment:
  id: smoke-script-benchmark
  title: CI smoke experiment (script mode, no compiler)
  status: approved_for_run
  categories:
    - smoke
    - tooling

  purpose: >
    Verify the whole research pipeline in CI without requiring a C++ toolchain.

  motivation: >
    A registry that validates but cannot be executed hides breakage in the
    runner, manifest, verifier, importer, and report path.

  research_question: >
    Does the pipeline record, verify, ingest, and report a declared run?

  hypotheses:
    - id: H1
      statement: >
        A declared script run completes, verifies, ingests, and reports without
        unmapped result rows.
      metric: real_time
      expected_direction: lower

  assumptions:
    - The Python interpreter is available in the CI environment.

  limitations:
    - This experiment measures nothing about ze_json; it only exercises tooling.

  research_basis:
    - id: BASIS-SMOKE-001
      basis_type: internal_implementation
      statement: >
        The runner supports script mode, where no configure or build step runs
        and the interpreter version is recorded instead of a binary hash.
      reference_ids:
        - REF-RUNNER-SOURCE

  references:
    - id: REF-RUNNER-SOURCE
      source_type: source_code
      title: ze_json_research runner
      locator: src/ze_research/runner.py execution.steps and mode handling
      accessed_at: null
      evidence_note: Reviewed against the runner implementation of script mode.
      review_status: reviewed

  metrics:
    primary:
      name: real_time
      unit: ns/op
      direction: lower_is_better
    secondary:
      - name: bytes_per_second
        direction: higher_is_better

  protocol:
    design: same_process_sequential
    require_same_workload: true
    require_same_measurement_contract: true
    notes: >
      Both benchmark rows are written by one interpreter invocation, so the
      shared workload condition holds by construction.

  comparison:
    group: smoke-pair
    baseline: fake-baseline
    candidate: fake-candidate

  variants:
    - id: fake-baseline
      name: Fake baseline
      description: Placeholder baseline used only by the CI smoke run.
      comparison_role: baseline
      lifecycle_status: experimental
      measurement_contract_id: smoke-contract-v1
      benchmark_name_pattern: "BM_Fake_Baseline_{dataset}"

    - id: fake-candidate
      name: Fake candidate
      description: Placeholder candidate used only by the CI smoke run.
      comparison_role: candidate
      lifecycle_status: experimental
      measurement_contract_id: smoke-contract-v1
      benchmark_name_pattern: "BM_Fake_Candidate_{dataset}"

  workloads:
    - id: smoke
      name: Synthetic smoke workload
      benchmark_token: Smoke
      dataset_path: null
      dataset_sha256: null
      parameters: {}

  measurement_contracts:
    - id: smoke-contract-v1
      name: Synthetic benchmark rows
      operation: Write synthetic benchmark rows with a Python step.
      includes:
        - the script step that writes raw/smoke_benchmark.json
      excludes:
        - any measurement of ze_json
      notes: >
        Numbers are fixed constants; the report only proves the plumbing works.

  execution:
    preset: null
    mode: script
    require_clean_worktree: false
    steps:
      - id: run
        command:
          - "@@INTERPRETER@@"
          - "-c"
          - "@@SMOKE_STEP@@"
    env_passthrough: []
    expected_outputs:
      - raw/smoke_benchmark.json
"""

def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: make_smoke_experiment.py <output.yaml>", file=sys.stderr)
        return 2
    destination = Path(argv[1]).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Forward slashes keep the value safe inside a YAML double-quoted scalar.
    interpreter = sys.executable.replace("\\", "/")
    document = TEMPLATE.replace("@@INTERPRETER@@", interpreter)
    document = document.replace("@@SMOKE_STEP@@", SMOKE_STEP)
    destination.write_text(document, encoding="utf-8")
    print(f"wrote {destination}")
    return 0


if __name__ == "__main__":
    # main() does its own positional handling, so hand it argv without the
    # program name.
    raise SystemExit(main(sys.argv[1:]))
