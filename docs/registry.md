# Research registry

## Purpose

The registry defines what is intended to be studied. It is not the result database and does not prove that a command ran. The run manifest and raw artifacts record what actually happened.

Definitions live in research/experiments/*.yaml. Keep them versioned in Git so semantic changes can be reviewed as diffs.

## Required research context

Each definition provides:

- id, title, status, and categories;
- purpose, motivation, and research_question;
- hypotheses with IDs and statements;
- research_basis statements with explicit reference_ids;
- references containing source type, title, evidence note, review status, and URL/DOI or an internal locator;
- assumptions and limitations;
- primary metric and protocol;
- variants, workloads, and measurement contracts;
- configure, build, and run command argument arrays;
- expected output paths.

The registry distinguishes comparison_role (baseline, candidate, control, replica, other) from algorithm lifecycle_status (experimental, approved, deprecated, etc.). They answer different questions.

## References and evidence

The registry may refer to papers, official specifications, source code, commits, issues, previous run manifests, or other artifacts. URL syntax validation does not fetch the URL or prove that the cited passage supports the claim.

A human reviewer must inspect the referenced source, record an evidence note and locator, and mark review_status as reviewed. Do not use a reviewed status merely to bypass the formal-run gate.

A research basis statement must cite one or more existing local reference IDs. This makes the link between a claim and its evidence explicit.

## Lifecycle

- draft: work is still being prepared.
- reviewed: metadata and evidence were inspected; the definition is not yet approved for formal execution.
- approved_for_run: the formal-run gate is open after review.
- retired: retained for history but not intended for new formal runs.

Approval is a researcher's declaration, not an automated proof of scientific correctness. Keep older definitions and their fingerprints in Git history.

## Fingerprinting

The loader serializes the loaded YAML data into deterministic JSON (sorted object keys and fixed separators) and records SHA-256. A change to the question, hypothesis, evidence, workload, metric, commands, or protocol changes the fingerprint.

The run stores the complete definition snapshot, not just the current YAML path. Future edits therefore do not silently rewrite the context of a past run.

## Command arrays

The runner launches commands without a shell. Do not rely on shell operators such as pipes, redirects, or &&. Use separate commands and declared output paths. Supported placeholders are source_dir, build_dir, run_dir, data_dir, experiment_id, run_id, benchmark_executable, and vcpkg_root.

Review executable paths and commands before approval. A registry is executable configuration, not inert documentation.

## Execution steps

`execution.steps` is the explicit command list and supersedes the legacy
`configure_command`/`build_command`/`run_command` trio. Use it whenever a run
needs more than three commands, such as one step per test executable.

Each step declares an `id` (unique), a `command` argument array, optionally
`cwd: source_dir|run_dir` and `required: false`. The runner records which step
produced which expected output, so imported results are attributed to the
execution that really created them.

`execution.mode` selects `native` (configure/build, binary hashes) or `script`
(no build, interpreter identity and version recorded). `mode: script` is what
`benchmarks/json_baseline` declares; its wrapper writes its own artifacts into
the external run directory.

`execution.env_passthrough` names environment variables that can change a
measurement. The runner records their names and values at run time so the
measurement contract is complete.

Literal braces are written `{{` and `}}` because `{name}` is reserved for
placeholders. An unknown `{name}` is rejected instead of passed through.

## String versus mapping in list items

A plain-scalar list item that contains `": "` is parsed by YAML as a mapping, not
as a string. `- Because it is slower: really` becomes a one-key dict and the
validator rejects it with "harus berupa array string". Quote such items, or drop
the colon. The same applies to any unquoted field value.

## Workload tokens and mapping honesty

A variant's `benchmark_name_pattern` may contain `{dataset}`. Every workload
must then declare `benchmark_token` (for example `GithubEvents`), which the
registry substitutes into the stored pattern so the result is an exact
benchmark name. This is deliberate:

- a wildcard pattern matches `BM_Scanner8_Synth` as well as
  `BM_Scanner8_GithubEvents`, which labels a synthetic payload as a real
  dataset workload;
- with more than one workload the same wildcard matches several specs and the
  row becomes AMBIGUOUS.

A literal pattern (no `{dataset}`) is only valid for a single-workload
experiment. Rows that map to nothing stay UNMAPPED; the system never guesses.

A workload with `dataset_path: null` may still declare a token, which is how
an in-process generated payload is recorded without pretending a file exists.

## Variant workload restriction

A variant may declare which workloads it actually implements:

~~~yaml
variants:
  - id: scanner8-library
    benchmark_name_pattern: "BM_Scanner8_{dataset}"
    workloads: [tiny, literal, github-events, update-center, jeopardy, synthetic-256k]
  - id: scanner8-integrated-replica
    benchmark_name_pattern: "BM_Scanner8_IntegratedReplica_{dataset}"
    workloads: [github-events, jeopardy, replica-jeopardy-via-synth-slot]
~~~

Without it, every variant is crossed with every workload. Two real cases make
that wrong rather than merely noisy:

- the benchmark uses the **same `Synth` name slot for two different payloads**
  (the in-process synthetic document, and `jeopardy.json` for the replicas), so
  crossing both workloads with both variants produces identical patterns and
  the result rows become AMBIGUOUS;
- a replica variant that only registers three datasets otherwise gets specs for
  all of them, describing measurements that never happen.

Restriction is therefore honesty about the catalog, not an optimization.

## Correctness gates

`correctness_gate` declares the evidence a performance run must already have:

~~~yaml
correctness_gate:
  id: gate-correctness-suite
  experiment_id: correctness-gtest-suite
  require_verification_status: VERIFIED   # default
  require_same_commit: true               # default
~~~

The runner refuses to start when no matching run exists, and records the gate
run it used in the manifest. The gate is fail-closed on purpose: missing
evidence is not permission.

## Dataset digests

`dataset_sha256` values come from the ze_json-side generator
(`scripts/research/dataset-digests.py`), not from memory. The runner re-hashes
each declared dataset before executing and aborts when the bytes no longer
match the reviewed definition.
