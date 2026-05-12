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
export AUTH_REQUEST_TIMEOUT_SECONDS="5"
export MANAGER_ID="manager-local"
export MINIO_ENDPOINT="127.0.0.1:9000"
export MINIO_ACCESS_KEY="minioadmin"
export MINIO_SECRET_KEY="minioadmin"
export MINIO_BUCKET="mapreduce"
export MINIO_SECURE="false"
export MAP_CHUNK_SIZE_BYTES="5242880"
export MIN_NUM_REDUCERS="1"
export MAX_NUM_REDUCERS="8"
export MAP_TASKS_PER_REDUCER="2"
export KUBERNETES_SCHEDULING_ENABLED="false"
export KUBERNETES_NAMESPACE="default"
export WORKER_IMAGE="mapreduce-worker:latest"
export WORKER_IMAGE_PULL_POLICY="IfNotPresent"
export MANAGER_INTERNAL_URL="http://manager:8001"
export WORKER_SERVICE_TOKEN=""
export KUBERNETES_JOB_TTL_SECONDS="3600"
export KUBERNETES_RECONCILE_ENABLED="false"
export KUBERNETES_RECONCILE_INTERVAL_SECONDS="15"
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
mappers or reducers. The Manager creates one map task per input chunk and
chooses the reducer count from the number of map tasks, bounded by
`MIN_NUM_REDUCERS` and `MAX_NUM_REDUCERS`. `MAP_TASKS_PER_REDUCER` controls how
quickly the reducer count grows.

## Shuffle Phase

When all map tasks for a job are completed, the Manager reads mapper output
objects from MinIO, groups values by key, partitions the keys across the
configured reducer count, and writes reducer input objects back to MinIO.

Mapper output is expected as JSON Lines, one key/value pair per line:

```json
["word", 1]
```

Reducer input is also JSON Lines, one grouped key per line:

```json
["word", [1, 1, 1]]
```

The Manager then creates one reduce task per reducer input object.

## Result Retrieval

`GET /jobs/{job_id}/result` is available only after a job reaches `completed`.
It lists final result objects from MinIO under the job output prefix, returning
their object names, `s3://...` paths, sizes, etags, and last-modified times.

`GET /jobs/{job_id}/result/content` downloads the final result objects from
MinIO, concatenates them in reducer-output order, and returns the result content
as plain text.

The temporary admin-only `POST /jobs/{job_id}/complete` endpoint is for local
testing. It writes the provided test result to the same MinIO output prefix used
by real reducer outputs, so the result retrieval endpoints continue to work.

## Kubernetes Scheduling

Kubernetes scheduling is disabled by default for local development. Set
`KUBERNETES_SCHEDULING_ENABLED=true` when the Manager is running in Minikube or
another Kubernetes environment with access to the Kubernetes API.

When enabled, the Manager creates one Kubernetes Job for each map task after job
submission. After all map tasks complete and shuffle creates reduce tasks, the
Manager schedules one Kubernetes Job per reduce task. If a task fails but still
has retries left, the Manager clears the old worker Job name and schedules a new
Kubernetes Job for the next attempt.

Each worker Job receives environment variables describing exactly one task:

```text
TASK_TYPE
JOB_ID
TASK_ID
TASK_INDEX
INPUT_PATH
USER_CODE_PATH
OUTPUT_PATH
MINIO_ENDPOINT
MINIO_BUCKET
MINIO_ACCESS_KEY
MINIO_SECRET_KEY
MINIO_SECURE
MANAGER_URL
WORKER_SERVICE_TOKEN
```

The worker image is configured with `WORKER_IMAGE`. The Manager stores the
created Kubernetes Job name in each task row as `kubernetes_job_name`.

## Kubernetes Monitoring

Set `KUBERNETES_RECONCILE_ENABLED=true` to start a background Manager loop that
polls Kubernetes Job status every `KUBERNETES_RECONCILE_INTERVAL_SECONDS`.
Each Manager replica only reconciles jobs where the stored `manager_id` matches
its own `MANAGER_ID`.

The reconciler keeps PostgreSQL task metadata aligned with Kubernetes:

- active Jobs move tasks to `running`
- completed Jobs move tasks to `completed`
- failed or missing Jobs record a failed attempt
- failed tasks with retries left are rescheduled as new Kubernetes Jobs
- completed map phase triggers shuffle and reduce Job scheduling
