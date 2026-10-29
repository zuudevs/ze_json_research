# Architecture

## Purpose

ze_json_research manages research definitions, local evaluation executions, provenance, raw evidence, relational storage, and analysis inputs. It is separated from the ze_json product repository.

## Three locations

1. ze_json source checkout: library implementation and product tests.
2. ze_json_research checkout: research CLI, registry YAML, schemas, migrations, tests, and documentation.
3. External research-data directory: SQLite, run folders, raw result files, logs, build trees, and reports.

The third location must be outside both repositories.

Example on Windows:

~~~text
D:\Projects\ze_json\
D:\Projects\ze_json_research\
D:\ResearchData\ze_json_research\
    database\ze_json_research.sqlite
    runs\<run-id>\manifest.json
    runs\<run-id>\raw\
    runs\<run-id>\logs\
    builds\<run-id>\
    artifacts\
    reports\
~~~

## Local data flow

~~~text
Research YAML
    -> Registry loader and validator
    -> Research CLI
    -> External build directory
    -> Benchmark / test / other declared process
    -> Raw result + manifest
    -> Artifact verifier
    -> SQLite importer
    -> Analysis and traceability
~~~

There is no server or network transfer in the current MVP. The runner invokes configured argument arrays with shell=False and directs known outputs to an external run directory.

## Components

- Registry: loads YAML, validates IDs/references, checks the research basis, and computes a canonical SHA-256 fingerprint.
- Paths: resolves configuration and rejects data/build paths inside either repository.
- Runner: captures source state, runs configure/build/run commands, records exit codes and logs, and generates a manifest.
- Manifest verifier: validates contained relative paths, expected outputs, definition fingerprint, artifact hashes/sizes, and executable hash if recorded.
- Database: applies immutable, versioned SQL migrations and stores definitions, versions, references, basis items, variants, workloads, contracts, runs, executions, artifacts, and typed results.
- Importer: verifies completed runs before import; imports Google Benchmark result rows and basic Google Test test cases.
- CLI: exposes initialization, validation, run, verify, ingest, database status, and listing commands.

## Run, execution, and result are different entities

A research run is a single attempt governed by one versioned experiment definition. The run may contain configure, build, and run executions. A single execution can produce several artifacts and result rows.

A shared run ID does not imply that two benchmark variants were interleaved in the same process. Claims requiring an interleaved comparison must be supported by execution records and by a harness that really runs both variants in one process.

## Data model summary

- experiments and experiment_versions hold stable identity plus immutable definition snapshots.
- research_references, research_basis_items, and research_basis_references preserve the basis/reference relationship per experiment version.
- algorithm_variants, workload_cases, measurement_contracts, and benchmark_specs describe the catalog.
- research_runs, executions, run_inputs, and artifacts describe provenance and runtime.
- benchmark_results and test_results keep domain-specific observations.
- analysis_runs, analysis_inputs, and derived_results reserve traceability for analysis output.

The artifact store remains the source evidence. SQLite is a queryable index, not the only copy of the evidence.

## MVP boundaries

Current implementation supports local orchestration, registry validation, manifests, hashes, SQLite migration, benchmark JSON import, and basic Google Test JSON import. Profiling-specific and coverage-specific parsers, statistical analysis reports, remote ingest, and digital signatures are not implemented.
