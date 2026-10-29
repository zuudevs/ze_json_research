# ze_json_research

Local-first research management for the [ze_json](https://github.com/zuudevs/ze_json) project. This repository contains research definitions, a registry validator, a local runner, provenance manifests, artifact verification, SQLite persistence, and JSON importers.

## Design boundaries

- The ze_json repository contains library/source code and product tests.
- This repository contains research tooling, YAML definitions, schemas, migrations, tests, and documentation.
- Runtime data is stored outside both repositories in a configured directory.

No server or HTTP API is required for local execution.

## MVP capabilities

- YAML experiment registry with purpose, motivation, research question, hypotheses, assumptions, limitations, research basis, and reviewed references.
- Formal-run gate: draft definitions or unreviewed references cannot run as formal experiments.
- External-path guard for data and build directories.
- Generic local configure/build/run orchestrator using argument arrays (no shell).
- Run manifest with source Git state, environment, command records, expected outputs, and SHA-256 artifact digests.
- Artifact verification.
- Versioned SQLite migration and relational experiment/run/artifact records.
- Import of Google Benchmark JSON and basic Google Test JSON.
- Idempotent re-import of an identical manifest, with rejection when an existing run ID has a different manifest digest.

Profiler-specific and coverage-specific importers, statistical reports, remote ingest, and signed manifests are future integrations, not current MVP capabilities.

## Quick start (PowerShell)

Install Python 3.11 or newer, then from this repository:

~~~powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

$env:ZE_JSON_RESEARCH_HOME = "D:\ResearchData\ze_json_research"
$env:ZE_JSON_SOURCE_DIR = "D:\Projects\ze_json"

ze-research init
ze-research validate
ze-research list-experiments
python -m pytest -q
~~~

The example experiment is deliberately a draft. Its reference is not reviewed, and its build commands must be validated against the exact ze_json checkout before approval. A successful structural validation does not approve the experiment.

## Run workflow

1. Review the definition under research/experiments.
2. Check reference evidence, dataset path, command arguments, measurement contract, and comparison design.
3. Mark the definition approved_for_run only after human review.
4. Run the experiment using the configured source/data directories.
5. Verify its manifest and artifacts.
6. Import the completed run to SQLite.
7. Query the database or use it as an input to later analysis.

See docs/ for architecture, registry rules, provenance, CLI use, and development notes.

## Runtime data

Set ZE_JSON_RESEARCH_HOME or pass --data-dir. The CLI rejects a data directory nested inside the research repository or configured ze_json source repository. Build directories and raw outputs are created under the external data root; the CLI does not copy results through ze_json/out/results.

## Important interpretation boundary

SHA-256 proves that a file matches the recorded digest, assuming the digest is trusted. It does not prove scientific correctness or authorship. A VERIFIED manifest means mechanical checks passed; it is not a certificate that a hypothesis is true.
