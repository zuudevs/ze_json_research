# CLI reference

Install editable for development:

~~~powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
~~~

Configure external paths:

~~~powershell
$env:ZE_JSON_RESEARCH_HOME = "D:\ResearchData\ze_json_research"
$env:ZE_JSON_SOURCE_DIR = "D:\Projects\ze_json"
~~~

## init

Creates external data directories and initializes/migrates SQLite.

~~~powershell
ze-research init
ze-research init --data-dir D:\ResearchData\ze_json_research --source-dir D:\Projects\ze_json
~~~

The data path is rejected if it is nested inside either repository.

## validate

Checks structure and internal references.

~~~powershell
ze-research validate
ze-research validate --experiment scanner-width-comparison
ze-research validate --experiment scanner-width-comparison --for-run
~~~

Structural validation allows a draft. The --for-run gate additionally requires approved_for_run, a nonempty research basis, and all references to have review_status reviewed.

## list-experiments

Prints experiment IDs, titles, lifecycle status, and definition fingerprints for YAML files in the registry.

## run

Runs configure, build, and execution commands declared by the selected experiment. Formal execution requires --for-run validation to pass. Each run receives a unique directory under the external data root, with a separate build tree, logs, raw outputs, and manifest.

~~~powershell
ze-research run --experiment scanner-width-comparison
~~~

This command is an orchestrator for the declared commands. The sample definition is a draft and must not be run formally until reviewed and adapted for the exact ze_json checkout. The example CMake commands may require a vcpkg/toolchain configuration for a particular environment.

## verify

Verifies manifest fingerprints and artifact/executable hashes.

~~~powershell
ze-research verify --run-id RUN_ID
ze-research verify --manifest D:\ResearchData\ze_json_research\runs\RUN_ID\manifest.json
~~~

## ingest

Validates a completed manifest and imports normalized experiment/run/result data into SQLite.

~~~powershell
ze-research ingest --run-id RUN_ID
~~~

The --skip-verify option is intended for controlled recovery/debugging only. Do not use it for normal formal ingest.

## db-status and list-runs

~~~powershell
ze-research db-status
ze-research list-runs
~~~

db-status checks SQLite integrity, foreign keys, applied migrations, and record counts.

## Exit codes

- 0: command completed successfully.
- 1: registry validation failed; for `report`, the data is present but not
  defensible (unmapped result rows or an incomplete/incompatible comparison).
- 2: runtime, provenance, database, verification, or execution failure.

## run: multi-step and script executions

`execution.steps` replaces the fixed configure/build/run trio when a run needs
more (or fewer) commands, for example one step per test executable:

~~~yaml
execution:
  mode: native        # or script
  steps:
    - id: configure
      command: [cmake, -S, "{source_dir}", -B, "{build_dir}", -G, Ninja]
    - id: build-tests
      command: [cmake, --build, "{build_dir}", "--target", "scanner_test"]
    - id: test-scanner
      command:
        - "{build_dir}/tests/scanner_test"
        - "--gtest_output=json:{run_dir}/raw/tests/scanner_test.json"
~~~

Each step may set `cwd: source_dir|run_dir` and `required: false`. The runner
records which step produced which expected output, so a result row is attributed
to the execution that actually created it. The legacy `configure_command`,
`build_command`, and `run_command` arrays remain supported.

Supported placeholders: `source_dir`, `build_dir`, `run_dir`, `data_dir`,
`experiment_id`, `run_id`, `benchmark_executable`, and `vcpkg_root` (an empty
`VCPKG_ROOT` is an error rather than a silently broken toolchain path). Wrap a
literal brace as `{{` or `}}` so a command may carry a JSON or Python payload.

`mode: script` skips configure/build, records the interpreter and its version
instead of a binary hash, and is what `benchmarks/json_baseline` uses.

## ingest

`--strict` makes an import fail when benchmark rows cannot be mapped to exactly
one variant. Mapped-out rows stay stored as UNMAPPED or AMBIGUOUS either way;
this flag decides whether that is acceptable for a formal record.

## report and export

~~~powershell
ze-research report --experiment scanner-width-comparison `
  --pair scanner8-library:scanner64-library
ze-research export --experiment scanner-width-comparison
~~~

`report` aggregates `run_type == iteration` rows into medians, refuses to
compare rows with different units or a missing pair side, and lists unmapped
rows. It records the comparison in `analysis_runs` and `derived_results` with
the run IDs, manifest digests, definition fingerprint, dataset digest, and
correctness gates behind it. A non-zero exit means the numbers exist but are
not yet defensible.

`export` writes the same analysis as JSON for `scripts/analyze_result/main.py`,
so notebook figures are derived from ingested runs instead of loose
`out/results/` files.
