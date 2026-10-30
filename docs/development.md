# Development

## Requirements

- Python 3.11 or newer.
- PyYAML 6.x.
- pytest for the test suite.
- Git available on PATH for source provenance.
- CMake, Ninja, compiler, and project dependencies only when invoking the example native experiment.

~~~powershell
python -m pip install -e ".[dev]"
python -m pytest -q
ze-research --help
ze-research validate
~~~

## MVP boundaries

Implemented:
- YAML loading, structural and semantic checks, cross-reference validation, formal-run approval gate, deterministic definition fingerprint.
- External data directory checks.
- Generic configure/build/run orchestration using argument arrays and shell=False.
- Git source state and environment capture.
- Atomic run manifest and SHA-256 hashes.
- SQLite migration and relational storage.
- Google Benchmark JSON and basic Google Test JSON ingestion.
- Manifest verification and run listing.

Not yet implemented:
- A profiler-specific parser or coverage importer.
- Statistical analysis reports beyond median/pair comparison and visualization.
- Remote ingest/API/server.
- Digital signature of manifests.
- Full binary/build reproducibility or a sandbox for arbitrary declared commands.

Integrated so far: the scanner, parser, session, and span_iso benchmarks, the
JavaScript `JSON.parse` baseline, and the whole Google Test suite are recorded
through this repository. Profiling-specific and coverage-specific importers are
next; they follow the same pattern (see `docs/registry.md`, "Execution steps",
and `docs/cli.md`).

## Test expectations

Tests should cover valid and invalid registry definitions, duplicate IDs, unknown basis references, external path rejection, migration repeatability, manifest tampering, import idempotency, mapping status, and failed command handling.

Coverage for the benchmark/test integration additionally includes: dataset
drift and missing-dataset refusal, declared step order and output attribution,
script-mode interpreter version, gate acceptance and rejection (no gate run,
wrong commit, unverified run), report pair comparison including unit mismatch
refusal and unmapped visibility, export provenance, and idempotent analysis
recording.

A fast way to exercise the whole pipeline without a C++ toolchain:

~~~powershell
ze-research init
New-Item -ItemType Directory -Force $env:TEMP\smoke-registry | Out-Null
python scripts/research/make_smoke_experiment.py $env:TEMP\smoke-registry\smoke.yaml
ze-research validate --registry-dir $env:TEMP\smoke-registry --for-run
$run = ze-research run --experiment smoke-script-benchmark --registry-dir $env:TEMP\smoke-registry
$runId = ($run | ConvertFrom-Json).run_id
ze-research verify --run-id $runId
ze-research ingest --run-id $runId --strict
ze-research report --experiment smoke-script-benchmark --pair fake-baseline:fake-candidate
~~~

Formal-run approval is a metadata gate, not a security sandbox. Only run command arrays reviewed by a trusted researcher. The runner does not invoke a shell, but a declared executable can still access the host filesystem.

## Adding a result domain

Keep common provenance in research_runs, executions, and artifacts. Add a typed result table and dedicated importer for each new domain. A profiler can produce large binary files; persist their path, digest, tool version, and summary metrics in SQLite rather than embedding profile bytes in the database.

Preserve raw outputs and original names. Data that cannot be mapped uniquely should be retained with UNMAPPED or AMBIGUOUS status instead of dropped or guessed.
