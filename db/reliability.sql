-- Reliability foundation (slice 4 CP3).
--
-- One representative query per metric kind, proving the data structures can
-- answer the reliability question decided at Understand: longest lull,
-- hourly-led. Slice 5 builds the actual view; this proves the foundation.
--
-- Two of the four run against weather_hour (hourly resolution, which the
-- daily cagg cannot reconstruct); two run against daily_cf.

-- NB both hourly queries below compute each hour's capacity factor ONCE, in
-- a MATERIALIZED CTE, and window/filter over the results.
--
-- MATERIALIZED is load-bearing, not decoration. Without it PG inlines the
-- CTE and the function calls move back inside the window frame -- and a
-- moving-window avg() over floating point cannot use inverse transitions,
-- so it recomputes the whole 24-row frame per row: 526k x 24 = 12.6M calls
-- instead of 526k. wind_capacity_factor unnests a 50-element array twice
-- per call, so that difference is minutes versus seconds. Measured: the
-- inlined version ran 8 minutes without finishing query 1.

\echo '== 1. WORST ROLLING 24h  (hourly, weather_hour) =='
\echo '   The deepest lull: lowest mean capacity factor over any 24 consecutive hours.'
WITH hourly AS MATERIALIZED (
    SELECT site_id, ts,
           pv_capacity_factor(global_tilted_irradiance, temperature_2m)   AS pv,
           wind_capacity_factor(wind_speed_100m, surface_pressure,
                                temperature_2m)                          AS wind
    FROM weather_hour
)
SELECT s.name,
       round(min(w24.pv)::numeric, 4)   AS worst_24h_pv,
       round(min(w24.wind)::numeric, 4) AS worst_24h_wind
FROM (
    SELECT site_id,
           avg(pv)   OVER win AS pv,
           avg(wind) OVER win AS wind,
           count(*)  OVER win AS n
    FROM hourly
    WINDOW win AS (PARTITION BY site_id ORDER BY ts
                   ROWS BETWEEN 23 PRECEDING AND CURRENT ROW)
) w24
JOIN site s ON s.id = w24.site_id
WHERE w24.n = 24          -- drop the partial windows at each site's start
GROUP BY s.name ORDER BY s.name;

\echo ''
\echo '== 2. HOURS PER YEAR BELOW 10% OUTPUT  (hourly, weather_hour) =='
\echo '   Wind counts all hours. PV counts DAYLIGHT hours only -- otherwise'
\echo '   every night hour qualifies and the metric just measures darkness.'
WITH hourly AS MATERIALIZED (
    SELECT site_id,
           extract(year FROM (ts AT TIME ZONE 'Africa/Johannesburg')) AS yr,
           global_tilted_irradiance                                    AS gti,
           pv_capacity_factor(global_tilted_irradiance, temperature_2m) AS pv,
           wind_capacity_factor(wind_speed_100m, surface_pressure,
                                temperature_2m)                        AS wind
    FROM weather_hour
)
SELECT s.name,
       round(avg(yearly.wind_hours), 0)        AS wind_hrs_below_10pct_per_yr,
       round(avg(yearly.pv_daylight_hours), 0) AS pv_daylight_hrs_below_10pct_per_yr
FROM (
    SELECT site_id, yr,
           count(*) FILTER (WHERE wind < 0.10)             AS wind_hours,
           count(*) FILTER (WHERE gti > 0 AND pv < 0.10)   AS pv_daylight_hours
    FROM hourly
    GROUP BY site_id, yr
) yearly
JOIN site s ON s.id = yearly.site_id
GROUP BY s.name ORDER BY s.name;

\echo ''
\echo '== 3. WORST ROLLING 7 DAYS  (daily_cf) =='
\echo '   The sustained lull a week of storage would have to cover.'
SELECT s.name,
       round(min(w7.pv)::numeric, 4)   AS worst_7d_pv,
       round(min(w7.wind)::numeric, 4) AS worst_7d_wind
FROM (
    SELECT site_id,
           avg(pv_cf)   OVER win AS pv,
           avg(wind_cf) OVER win AS wind,
           count(*)     OVER win AS n
    FROM daily_cf
    WINDOW win AS (PARTITION BY site_id ORDER BY day
                   ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
) w7
JOIN site s ON s.id = w7.site_id
WHERE w7.n = 7
GROUP BY s.name ORDER BY s.name;

\echo ''
\echo '== 4. P50 / P90 ANNUAL YIELD  (daily_cf) =='
\echo '   NB P90 is the yield EXCEEDED in 90% of years -- the 10th percentile'
\echo '   of the distribution, percentile_cont(0.1), NOT 0.9. n=10 years.'
SELECT s.name,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY a.pv)::numeric, 4)   AS pv_p50,
       round(percentile_cont(0.1) WITHIN GROUP (ORDER BY a.pv)::numeric, 4)   AS pv_p90,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY a.wind)::numeric, 4) AS wind_p50,
       round(percentile_cont(0.1) WITHIN GROUP (ORDER BY a.wind)::numeric, 4) AS wind_p90,
       count(*)                                                              AS years
FROM (
    -- AT TIME ZONE before extracting the year: `day` is a timestamptz whose
    -- bucket starts at local midnight, and extracting the year directly
    -- would convert to the server's UTC first, pushing 1 Jan into the
    -- previous year.
    SELECT site_id,
           extract(year FROM (day AT TIME ZONE 'Africa/Johannesburg')) AS yr,
           avg(pv_cf)   AS pv,
           avg(wind_cf) AS wind
    FROM daily_cf
    GROUP BY site_id, yr
) a
JOIN site s ON s.id = a.site_id
GROUP BY s.name ORDER BY s.name;
