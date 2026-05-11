# Manager Service

FastAPI service responsible for MapReduce job metadata, task lifecycle state,
retry tracking, and result metadata.

## Local Setup

Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create the PostgreSQL database expected by `app/database.py`:

```sql
CREATE DATABASE manager_db;
CREATE USER manager_user WITH PASSWORD 'manager_password';
GRANT ALL PRIVILEGES ON DATABASE manager_db TO manager_user;

\c manager_db

GRANT ALL ON SCHEMA public TO manager_user;
ALTER SCHEMA public OWNER TO manager_user;
```

Run the service:

```bash
uvicorn app.main:app --reload --port 8001
```

The API will be available at:

```text
http://127.0.0.1:8001
```

Interactive API docs:

```text
http://127.0.0.1:8001/docs
```

## Current Notes

The service currently expects the Authentication Service at:

```text
http://127.0.0.1:8000
```

You can override service configuration with environment variables:

```bash
export DATABASE_URL="postgresql://manager_user:manager_password@localhost/manager_db"
export AUTH_SERVICE_URL="http://127.0.0.1:8000"
export MANAGER_ID="manager-local"
export MINIO_ENDPOINT="127.0.0.1:9000"
export MINIO_ACCESS_KEY="minioadmin"
export MINIO_SECRET_KEY="minioadmin"
export MINIO_BUCKET="mapreduce"
export MINIO_SECURE="false"
export MAP_CHUNK_SIZE_BYTES="5242880"
export DEFAULT_NUM_REDUCERS="1"
```

Job and task tables are created automatically during local development through
SQLAlchemy's `Base.metadata.create_all(...)`.

## Job Submission

`POST /jobs` expects multipart form data:

```text
input_file=<uploaded dataset>
mapper_file=<uploaded mapper code>
reducer_file=<uploaded reducer code>
```

The Manager uploads the submitted files to MinIO, splits the input into
line-based chunk objects, and stores the MinIO paths in PostgreSQL before
creating the initial map task metadata. The user does not choose the number of
mappers or reducers. The Manager creates one map task per input chunk and uses
`DEFAULT_NUM_REDUCERS` for the reducer count.
