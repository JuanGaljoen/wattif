-- Renewable resource explorer — schema.
-- Runs once, on first container start, via docker-entrypoint-initdb.d.

CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE site (
    id                 serial PRIMARY KEY,
    name               text        NOT NULL UNIQUE,
    latitude           double precision NOT NULL,
    longitude          double precision NOT NULL,
    elevation_m        real,              -- arrives free in the CSV metadata header
    timezone           text        NOT NULL DEFAULT 'UTC',
    utc_offset_seconds integer     NOT NULL DEFAULT 0,

    -- FETCH parameters, frozen at ingest: they shape the GTI column the API
    -- returns, so changing them means re-fetching the site.
    -- NB azimuth is 0 = SOUTH, 180 = NORTH. See PLAN, "The two traps".
    tilt_deg           real        NOT NULL,
    azimuth_deg        real        NOT NULL
);

CREATE TABLE weather_hour (
    site_id    integer     NOT NULL REFERENCES site(id) ON DELETE CASCADE,
    ts         timestamptz NOT NULL,
    local_date date        NOT NULL,   -- ts + utc_offset. The daily cagg groups on
                                       -- this: time_bucket's tz must be constant
                                       -- inside a continuous aggregate.

    -- the nine variables. real (float4) not double: weather precision is far
    -- below float8, and it halves the bytes on the hypertable.
    global_tilted_irradiance  real,    -- W/m^2, the POA figure PV runs on
    shortwave_radiation       real,    -- W/m^2  \
    direct_radiation          real,    -- W/m^2   | horizontal set, kept to
    diffuse_radiation         real,    -- W/m^2   | validate GTI in one test
    direct_normal_irradiance  real,    -- W/m^2  /
    temperature_2m            real,    -- degC
    wind_speed_100m           real,    -- km/h  <-- NOT m/s. Divide by 3.6.
    wind_direction_100m       real,    -- deg
    surface_pressure          real,    -- hPa

    PRIMARY KEY (site_id, ts)          -- a hypertable PK must include the time column
);

SELECT create_hypertable(
    'weather_hour', 'ts',
    chunk_time_interval => INTERVAL '1 month'   -- 10 years -> ~120 chunks
);

-- Resumable backfill: one row per (site, year), so a crash costs one year.
CREATE TABLE ingest_job (
    site_id    integer     NOT NULL REFERENCES site(id) ON DELETE CASCADE,
    year       integer     NOT NULL,
    status     text        NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('pending','running','done','error')),
    rows       integer,
    fetched_at timestamptz,
    error      text,
    PRIMARY KEY (site_id, year)
);
