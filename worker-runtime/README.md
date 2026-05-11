# Worker Runtime

Container entrypoint that executes exactly one MapReduce task.

The Manager creates Kubernetes Jobs for workers and passes task configuration
through environment variables. A worker does not decide the job plan. It only
downloads its assigned input and user code, executes the task, uploads output to
MinIO, and reports task state back to the Manager.

## Environment Variables

Required:

```text
TASK_TYPE=map|reduce
JOB_ID=<job id>
TASK_ID=<task id>
TASK_INDEX=<task index>
INPUT_PATH=s3://<bucket>/<object>
USER_CODE_PATH=s3://<bucket>/<mapper-or-reducer-code>
OUTPUT_PATH=s3://<bucket>/<output-object>
MINIO_ENDPOINT=<host:port>
MINIO_BUCKET=<bucket>
MINIO_ACCESS_KEY=<access key>
MINIO_SECRET_KEY=<secret key>
```

Optional:

```text
MINIO_SECURE=false
MANAGER_URL=http://manager:8001
WORKER_SERVICE_TOKEN=<token accepted by Manager/Auth>
```

If `MANAGER_URL` and `WORKER_SERVICE_TOKEN` are set, the worker reports:

```text
POST /jobs/{job_id}/tasks/{task_id}/start
POST /jobs/{job_id}/tasks/{task_id}/complete
POST /jobs/{job_id}/tasks/{task_id}/fail
```

## Mapper Interface

Mapper code should define one of:

```python
def map(key, value):
    ...
```

```python
def mapper(key, value):
    ...
```

```python
def map_func(key, value):
    ...
```

For line-based input chunks, `key` is the line number inside the chunk and
`value` is the line text. The mapper should return or yield key/value pairs:

```python
def map(key, value):
    for word in value.split():
        yield word, 1
```

## Reducer Interface

Reducer code should define one of:

```python
def reduce(key, values):
    ...
```

```python
def reducer(key, values):
    ...
```

```python
def reduce_func(key, values):
    ...
```

The reducer should return or yield key/value pairs:

```python
def reduce(key, values):
    yield key, sum(values)
```

## Output Format

Workers write JSON Lines to MinIO, one key/value pair per line:

```json
["word", 3]
```

## Build Image

From this directory:

```bash
docker build -t mapreduce-worker:latest .
```

When using Minikube:

```bash
eval $(minikube docker-env)
docker build -t mapreduce-worker:latest .
```
