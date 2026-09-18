-- Slice 1 proof: the hypertable exists, it is chunked, and time_bucket rolls up.

\echo '== hypertable and chunks =='
SELECT hypertable_name, num_chunks,
       pg_size_pretty(total_bytes) AS total
FROM timescaledb_information.hypertables h
JOIN LATERAL hypertable_detailed_size(format('%I.%I', h.hypertable_schema,
                                             h.hypertable_name)::regclass) ON true
WHERE hypertable_name = 'weather_hour';

\echo ''
\echo '== row count, span =='
SELECT count(*) AS rows, min(ts) AS first, max(ts) AS last FROM weather_hour;

\echo ''
\echo '== time_bucket: monthly mean GTI and peak, local days =='
SELECT time_bucket('1 month', ts) AS month,
       round(avg(global_tilted_irradiance)::numeric, 1) AS mean_gti,
       round(max(global_tilted_irradiance)::numeric, 1) AS peak_gti,
       round(avg(temperature_2m)::numeric, 1)           AS mean_temp
FROM weather_hour
GROUP BY month
ORDER BY month;

\echo ''
\echo '== chunk exclusion: one month should scan one chunk =='
EXPLAIN (COSTS OFF)
SELECT avg(global_tilted_irradiance) FROM weather_hour
WHERE ts >= '2024-06-01' AND ts < '2024-07-01';
