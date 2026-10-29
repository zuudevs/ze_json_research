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

The runner launches commands without a shell. Do not rely on shell operators such as pipes, redirects, or &&. Use separate commands and declared output paths. Supported placeholders are source_dir, build_dir, run_dir, data_dir, experiment_id, run_id, and benchmark_executable.

Review executable paths and commands before approval. A registry is executable configuration, not inert documentation.
