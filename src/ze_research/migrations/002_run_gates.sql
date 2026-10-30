-- Records which correctness gates a formal run had to satisfy before it was
-- allowed to execute. Discoverable from the raw manifest, but keeping it in
-- SQLite means every performance row can be traced to the gate that vouched
-- for the same source commit.
ALTER TABLE research_runs ADD COLUMN gates_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_runs_definition ON research_runs(definition_sha256);
