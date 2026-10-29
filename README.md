# ze_json_research

Local-first research management for [zuudevs/ze_json](https://github.com/zuudevs/ze_json). This repository contains the research CLI, registry validation, run provenance, artifact verification, SQLite storage, and documentation. Runtime data is stored outside both repositories.

## Status

MVP implementation. Includes a YAML registry with research basis and references, formal-run approval checks, an external-directory runner, SHA-256 manifests, SQLite migrations, artifact verification, and importers for Google Benchmark JSON plus basic Google Test JSON. Profiling and coverage importer integrations and automatic analysis reports remain future work.

## Quick start (PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

$env:ZE_JSON_RESEARCH_HOME = "D:\ResearchData\ze_json_research"
$env:ZE_JSON_SOURCE_DIR = "D:\Projects\ze_json"

ze-research init
ze-research validate
```

The sample experiment is intentionally a draft. Review its scientific basis, references, workload, commands, and measurement contract before setting its status to `approved_for_run`.

## Documentation

- [Architecture](docs/architecture.md)
- [Registry](docs/registry.md)
- [Data integrity and provenance](docs/data-integrity.md)
- [CLI reference](docs/cli.md)
- [Development and MVP boundaries](docs/development.md)

## Repository/data separation

Keep source code in `ze_json`, research tooling and configuration in this repository, and SQLite/raw results/build trees in the external `ZE_JSON_RESEARCH_HOME` directory. The CLI rejects output paths nested under the repository roots.
