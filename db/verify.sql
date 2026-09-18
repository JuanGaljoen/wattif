-- Slice 1+3 proof: the hypertable exists, it's chunked, time_bucket rolls
-- up, and the full 6-site x 10-year backfill landed completely and
-- idempotently (specs/slice-3.md, success criteria).

\echo '== hypertable and chunks =='
SELECT hypertable_name, num_chunks,
       pg_size_pretty(total_bytes) AS total
FROM timescaledb_information.hypertables h
JOIN LATERAL hypertable_detailed_size(format('%I.%I', h.hypertable_schema,
                                             h.hypertable_name)::regclass) ON true
WHERE hypertable_name = 'weather_hour';

\echo ''
\echo '== per-site row counts (expect 87,672 each: 7x8760 + 3x8784 leap years) =='
SELECT s.name, count(w.*) AS rows, min(w.ts) AS first, max(w.ts) AS last
FROM site s LEFT JOIN weather_hour w ON w.site_id = s.id
GROUP BY s.name ORDER BY s.name;

\echo ''
\echo '== total rows (expect 526,032 = 6 x 87,672) =='
SELECT count(*) AS total_rows FROM weather_hour;

\echo ''
\echo '== ingest_job: every job done, rows column matches rows actually held =='
SELECT count(*) AS total_jobs,
       count(*) FILTER (WHERE status = 'done') AS done_jobs,
       sum(rows) AS sum_rows_column
FROM ingest_job;

\echo ''
\echo '== time_bucket: monthly mean GTI per site, Karoo 2024 (local days) =='
SELECT s.name, time_bucket('1 month', w.ts) AS month,
       round(avg(w.global_tilted_irradiance)::numeric, 1) AS mean_gti,
       round(max(w.global_tilted_irradiance)::numeric, 1) AS peak_gti
FROM weather_hour w JOIN site s ON s.id = w.site_id
WHERE s.name = 'Karoo' AND w.local_date >= '2024-01-01' AND w.local_date < '2025-01-01'
GROUP BY s.name, month ORDER BY month;

\echo ''
\echo '== daily_cf: the continuous aggregate (expect 21,918 rows, all hours = 24) =='
SELECT count(*) AS rows, count(DISTINCT day) AS days,
       min(hours) AS min_hours, max(hours) AS max_hours
FROM daily_cf;

\echo ''
\echo '== compression: ratio and chunk coverage =='
SELECT count(*) FILTER (WHERE is_compressed) AS compressed_chunks,
       count(*)                              AS total_chunks
FROM timescaledb_information.chunks WHERE hypertable_name = 'weather_hour';
SELECT pg_size_pretty(sum(total_bytes)) AS hypertable_size
FROM hypertable_detailed_size('weather_hour');

\echo ''
\echo '== policies: compression + cagg refresh =='
SELECT proc_name, schedule_interval
FROM timescaledb_information.jobs
WHERE proc_name IN ('policy_compression',
                    'policy_refresh_continuous_aggregate')
ORDER BY proc_name;

\echo ''
\echo '== chunk exclusion: one month should scan few chunks, not all 120 =='
EXPLAIN (COSTS OFF)
SELECT avg(global_tilted_irradiance) FROM weather_hour
WHERE ts >= '2024-06-01' AND ts < '2024-07-01';
