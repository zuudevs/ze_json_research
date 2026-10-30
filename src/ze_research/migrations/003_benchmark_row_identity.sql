-- Keep the registered benchmark name and its parameter separate from the raw
-- reported name. A raw name mixes the registration with Google Benchmark
-- options (min_time:...) and arguments (KiB:0), and averaging across a size
-- sweep hides the shape of the curve being measured.
ALTER TABLE benchmark_results ADD COLUMN benchmark_name_normalized TEXT;
ALTER TABLE benchmark_results ADD COLUMN benchmark_argument TEXT;

CREATE INDEX IF NOT EXISTS idx_benchmark_normalized
    ON benchmark_results(benchmark_name_normalized, benchmark_argument);
