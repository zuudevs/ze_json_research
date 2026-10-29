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
- 1: registry validation failed.
- 2: runtime, provenance, database, verification, or execution failure.

Run a command with --help to inspect its current options.
