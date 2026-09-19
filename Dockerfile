# The api image. The frontend is served by this same process (StaticFiles),
# so there is no build stage and no Node -- specs/slice-5a.md, Containers.
FROM python:3.13-slim

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# What the API imports at runtime, and nothing else.
#
# models/ and data/ are here because startup applies the runtime DDL
# (api/__init__.py, lifespan) -- apply_models generates the SQL from the
# Python constants, and models/curve.py reads the vendored turbine curve out
# of data/ to do it. ingest/ stays out: this image only reads the database,
# it never fills it.
COPY config.py .
COPY api/ api/
COPY models/ models/
COPY timescale/ timescale/
COPY data/ data/
COPY web/ web/

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
