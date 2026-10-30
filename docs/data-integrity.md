# Data integrity and provenance

## Verification layers

1. Registry validation checks required fields, ID uniqueness, cross-references, metric definitions, command arrays, and formal-run approval.
2. Source/build provenance records full Git commit, branch, working-tree state, tracked diff digest, untracked-file digests where possible, submodule status, commands, and executable identity when available.
3. Artifact integrity records SHA-256 and byte size for run files. Verification recomputes the digest and compares sizes.
4. Execution validation checks exit codes, expected outputs, and the completed status before ingest.
5. Methodological validation checks that a proposed comparison follows the declared workload, measurement contract, and protocol. The current MVP stores these definitions; it does not automatically prove all methodological conditions.

## Manifest

Each run directory contains manifest.json, written atomically after the configured commands finish. It records the run and experiment IDs, full definition snapshot and fingerprint, source details, runtime environment, build details, execution records, expected outputs, missing outputs, and hashes for files inside the run directory.

The manifest does not hash itself because that would be self-referential. The database stores a SHA-256 digest of the finalized manifest.

## Inputs are hashed at run time, not at import time

Declared workload datasets are hashed before any command runs. The digests are
written to `metadata/inputs.json` (hashed as a run artifact) and copied into
`manifest.inputs`. If a definition pins `dataset_sha256` and the checkout no
longer matches it, the run is refused.

Ingestion reads those recorded digests rather than recomputing them from the
live working tree. Recomputing would let the tree change between measurement
and import without any check noticing. A directory path (a corpus workload) is
digested as a unit over its file list, skipping symlinks.

## Correctness gates

A performance run may declare `correctness_gate` against a correctness
experiment. Before anything executes, the runner looks for a COMPLETED,
VERIFIED run of that experiment on the same git commit and refuses when none
exists. The gate run is recorded in `manifest.gates` and in
`research_runs.gates_json`.

This links the two kinds of evidence by commit instead of by memory. It does
not prove the algorithm is faster, and it does not make an unreviewed
definition runnable.

## Status meanings

- VERIFIED: the mechanical checks implemented by the verifier passed.
- FAILED: a required integrity check failed or a required artifact was missing.
- PARTIAL: a record lacks evidence required for stronger provenance.
- UNMAPPED: an imported raw result cannot be linked uniquely to a catalog entry.

The current verifier returns VERIFIED or FAILED for complete run manifests. Historical migration and additional domain importers may assign PARTIAL or UNMAPPED when applicable.

A VERIFIED result is not a statement that an algorithm is faster, that a hypothesis is true, or that the run is statistically conclusive.

## Artifact path security

Manifest artifact paths must be relative, may not contain parent traversal, and must resolve inside the run directory. Symbolic links are not treated as trusted result files. Expected output paths use the same containment rule.

The data directory and build directory must be outside both the research repository and the configured ze_json source checkout. Runtime output should be written directly there, not created under ze_json/out/results and copied later.

## Append-only operation and threat model

Normal CLI operations do not edit older run directories. Importing an identical finalized manifest is idempotent; reusing a run ID for a different manifest digest is rejected.

SHA-256 detects a mismatch only if the recorded digest is trusted. An actor who can modify both data and its stored digest can defeat a hash-only check. Stronger tamper evidence can later add signed manifests or digests stored in a separate trusted location.

Back up both SQLite and raw artifacts. A database can be rebuilt only from evidence that was actually retained. If historical source, binaries, or inputs were never recorded, the system must not invent them.
