# The api image. The frontend is served by this same process (StaticFiles),
# so there is no build stage and no Node -- specs/slice-5a.md, Containers.
FROM python:3.13-slim

WORKDIR /app

# Dependencies first: this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Only what the API imports at runtime: api -> timescale (SITE_TIMEZONE)
# -> psycopg, plus config and the static files. models/, ingest/ and data/
# are deliberately absent -- the model functions and the aggregate already
# live in the database; this image only reads them.
COPY config.py .
COPY api/ api/
COPY timescale/ timescale/
COPY web/ web/

EXPOSE 8000
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]
