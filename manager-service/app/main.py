from datetime import datetime
import hashlib
from io import BytesIO
import json
import os
import re

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy.orm import Session
import requests

from app.database import engine, get_db
from app.models import Base, Job, Task, TaskAttempt

try:
    from kubernetes import client as kubernetes_client
    from kubernetes import config as kubernetes_config
    from kubernetes.client.rest import ApiException
except ImportError:
    kubernetes_client = None
    kubernetes_config = None
    ApiException = Exception

from minio import Minio
from minio.error import S3Error


def get_positive_int_env(name: str, default: int):
    """
    Read a positive integer from the environment.

    If the value is missing or invalid, keep the safe local default.
    """

    value = os.getenv(name)

    if value is None:
        return default

    try:
        parsed_value = int(value)
    except ValueError:
        return default

    return max(1, parsed_value)


def get_bool_env(name: str, default: bool = False):
    """
    Read a boolean from the environment.
    """

    value = os.getenv(name)

    if value is None:
        return default

    return value.lower() in ["1", "true", "yes", "on"]


# Create the FastAPI application.
app = FastAPI(title="Manager Service")

# Create database tables if they do not already exist.
# Useful for local development/testing.
Base.metadata.create_all(bind=engine)

# Authentication Service URL.
# The Manager Service uses this to validate JWT tokens.
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://127.0.0.1:8000")

# Identifier of this Manager replica.
# In Kubernetes, HOSTNAME is usually the pod name. MANAGER_ID can override it.
MANAGER_ID = os.getenv(
    "MANAGER_ID",
    os.getenv("HOSTNAME", "manager-local")
)

# MinIO object storage configuration.
# The Manager uploads submitted input/mapper/reducer files here.
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "127.0.0.1:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "mapreduce")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# Manager-controlled task sizing.
# Users submit files; the Manager decides how many map/reduce tasks to create.
MAP_CHUNK_SIZE_BYTES = get_positive_int_env(
    "MAP_CHUNK_SIZE_BYTES",
    5 * 1024 * 1024
)
MIN_NUM_REDUCERS = get_positive_int_env("MIN_NUM_REDUCERS", 1)
MAX_NUM_REDUCERS = max(
    MIN_NUM_REDUCERS,
    get_positive_int_env("MAX_NUM_REDUCERS", 8)
)
MAP_TASKS_PER_REDUCER = get_positive_int_env("MAP_TASKS_PER_REDUCER", 2)

# Kubernetes worker scheduling configuration.
KUBERNETES_SCHEDULING_ENABLED = get_bool_env(
    "KUBERNETES_SCHEDULING_ENABLED",
    False
)
KUBERNETES_NAMESPACE = os.getenv("KUBERNETES_NAMESPACE", "default")
WORKER_IMAGE = os.getenv("WORKER_IMAGE", "mapreduce-worker:latest")
WORKER_IMAGE_PULL_POLICY = os.getenv("WORKER_IMAGE_PULL_POLICY", "IfNotPresent")
MANAGER_INTERNAL_URL = os.getenv("MANAGER_INTERNAL_URL", "http://manager:8001")
WORKER_SERVICE_TOKEN = os.getenv("WORKER_SERVICE_TOKEN", "")
KUBERNETES_JOB_TTL_SECONDS = get_positive_int_env(
    "KUBERNETES_JOB_TTL_SECONDS",
    3600
)

kubernetes_batch_api = None

minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=MINIO_ACCESS_KEY,
    secret_key=MINIO_SECRET_KEY,
    secure=MINIO_SECURE
)

# HTTPBearer tells FastAPI to expect:
# Authorization: Bearer <token>
security = HTTPBearer()


class CompleteJobRequest(BaseModel):
    """
    Temporary request body used to manually complete a job.

    This is only for testing before real workers/Kubernetes execution exist.
    """

    result: str


class StartTaskRequest(BaseModel):
    """
    Request body used when a task starts running.

    The Kubernetes fields are optional because local testing may not have
    real Kubernetes Jobs/Pods yet.
    """

    kubernetes_job_name: str | None = None
    kubernetes_pod_name: str | None = None


class CompleteTaskRequest(BaseModel):
    """
    Request body used when a task finishes successfully.
    """

    output_path: str | None = None


class FailTaskRequest(BaseModel):
    """
    Request body used when a task execution attempt fails.
    """

    error_message: str


def validate_token(credentials: HTTPAuthorizationCredentials):
    """
    Validate the JWT token by calling the Authentication Service.

    If the token is valid, the Authentication Service returns the username
    and role of the authenticated user.
    """

    token = credentials.credentials

    response = requests.post(
        f"{AUTH_SERVICE_URL}/auth/validate-token",
        json={
            "token": token
        }
    )

    result = response.json()

    if not result.get("valid"):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token"
        )

    return result


def now_utc():
    """
    Return the current UTC time for lifecycle timestamps.
    """

    return datetime.utcnow()


def require_admin(user_info):
    """
    Allow only admin users to call temporary lifecycle mutation endpoints.
    """

    if user_info["role"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only admins can update task lifecycle state"
        )


def get_job_or_404(db: Session, job_id: int):
    """
    Load a job or raise a 404 response.
    """

    job = db.query(Job).filter(Job.job_id == job_id).first()

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    return job


def get_task_or_404(db: Session, job_id: int, task_id: int):
    """
    Load a task belonging to a job or raise a 404 response.
    """

    task = db.query(Task).filter(
        Task.job_id == job_id,
        Task.task_id == task_id
    ).first()

    if not task:
        raise HTTPException(
            status_code=404,
            detail="Task not found"
        )

    return task


def ensure_job_access(user_info, job: Job, action: str):
    """
    Allow admins to access any job and normal users only their own jobs.
    """

    if user_info["role"] != "admin" and job.username != user_info["username"]:
        raise HTTPException(
            status_code=403,
            detail=f"You are not allowed to {action} this job"
        )


def ensure_minio_bucket():
    """
    Create the MinIO bucket if it does not already exist.
    """

    try:
        if not minio_client.bucket_exists(MINIO_BUCKET):
            minio_client.make_bucket(MINIO_BUCKET)
    except S3Error:
        raise HTTPException(
            status_code=503,
            detail="MinIO storage service unavailable"
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="MinIO storage service unavailable"
        )


def build_object_name(job_id: int, uploaded_file: UploadFile, kind: str):
    """
    Build a stable MinIO object name for a submitted job file.
    """

    filename = os.path.basename(uploaded_file.filename or kind)

    if not filename:
        filename = kind

    return f"jobs/{job_id}/submitted/{kind}/{filename}"


def minio_uri(object_name: str):
    """
    Return the URI stored in PostgreSQL for a MinIO object.
    """

    return f"s3://{MINIO_BUCKET}/{object_name}"


def parse_minio_path(path: str):
    """
    Convert an s3:// URI or raw object name into a bucket/object pair.
    """

    if path.startswith("s3://"):
        without_scheme = path[len("s3://"):]
        bucket, object_name = without_scheme.split("/", 1)
        return bucket, object_name

    return MINIO_BUCKET, path.lstrip("/")


def get_job_output_prefix(job: Job):
    """
    Return the MinIO object prefix where a job's final output is stored.

    job.output_path may be stored either as a raw object prefix or as an
    s3://bucket/prefix URI. This helper normalizes both forms into a MinIO
    object prefix.
    """

    if job.output_path:
        bucket_uri_prefix = f"s3://{MINIO_BUCKET}/"

        if job.output_path.startswith(bucket_uri_prefix):
            object_prefix = job.output_path[len(bucket_uri_prefix):]
        else:
            object_prefix = job.output_path

        return f"{object_prefix.strip('/')}/"

    return f"jobs/{job.job_id}/output/"


def serialize_minio_object(minio_object):
    """
    Convert a MinIO object summary into a JSON-friendly dictionary.
    """

    return {
        "object_name": minio_object.object_name,
        "path": minio_uri(minio_object.object_name),
        "size": minio_object.size,
        "etag": minio_object.etag,
        "last_modified": minio_object.last_modified
    }


def list_minio_objects(prefix: str):
    """
    List MinIO objects under a prefix.
    """

    try:
        return list(
            minio_client.list_objects(
                MINIO_BUCKET,
                prefix=prefix,
                recursive=True
            )
        )
    except S3Error:
        raise HTTPException(
            status_code=503,
            detail="Could not list result objects from MinIO"
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Could not list result objects from MinIO"
        )


def download_minio_object(path: str):
    """
    Download an object from MinIO by s3:// URI or raw object path.
    """

    bucket, object_name = parse_minio_path(path)

    try:
        response = minio_client.get_object(bucket, object_name)

        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()
    except S3Error:
        raise HTTPException(
            status_code=503,
            detail=f"Could not read object from MinIO: {path}"
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail=f"Could not read object from MinIO: {path}"
        )


def upload_job_file(job_id: int, uploaded_file: UploadFile, kind: str):
    """
    Upload one submitted file to MinIO and return its DDS storage path.
    """

    object_name = build_object_name(job_id, uploaded_file, kind)

    try:
        uploaded_file.file.seek(0)
        minio_client.put_object(
            MINIO_BUCKET,
            object_name,
            uploaded_file.file,
            length=-1,
            part_size=10 * 1024 * 1024,
            content_type=uploaded_file.content_type or "application/octet-stream"
        )
    except S3Error:
        raise HTTPException(
            status_code=503,
            detail=f"Could not upload {kind} file to MinIO"
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail=f"Could not upload {kind} file to MinIO"
        )

    return minio_uri(object_name), object_name


def upload_bytes_object(
    object_name: str,
    content: bytes,
    content_type: str = "application/octet-stream"
):
    """
    Upload generated bytes to MinIO and return its DDS storage path.
    """

    try:
        minio_client.put_object(
            MINIO_BUCKET,
            object_name,
            BytesIO(content),
            length=len(content),
            content_type=content_type
        )
    except S3Error:
        raise HTTPException(
            status_code=503,
            detail="Could not upload generated object to MinIO"
        )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Could not upload generated object to MinIO"
        )

    return minio_uri(object_name), object_name


def cleanup_minio_objects(object_names):
    """
    Best-effort cleanup for uploaded objects when job submission fails.
    """

    for object_name in object_names:
        try:
            minio_client.remove_object(MINIO_BUCKET, object_name)
        except S3Error:
            pass
        except Exception:
            pass


def get_uploaded_file_size(uploaded_file: UploadFile):
    """
    Return uploaded file size in bytes without consuming the file stream.
    """

    uploaded_file.file.seek(0, os.SEEK_END)
    file_size = uploaded_file.file.tell()
    uploaded_file.file.seek(0)

    return file_size


def split_input_file_to_minio_chunks(job_id: int, uploaded_file: UploadFile):
    """
    Split the uploaded input into line-based chunks and upload them to MinIO.

    Each returned chunk path becomes the input_path of one map task.
    """

    chunk_paths = []
    chunk_objects = []
    chunk_buffer = BytesIO()
    chunk_size = 0
    chunk_index = 0
    content_type = uploaded_file.content_type or "text/plain"

    def flush_chunk():
        nonlocal chunk_buffer, chunk_size, chunk_index

        object_name = f"jobs/{job_id}/input/chunks/chunk-{chunk_index:05d}.txt"
        chunk_path, chunk_object = upload_bytes_object(
            object_name,
            chunk_buffer.getvalue(),
            content_type
        )

        chunk_paths.append(chunk_path)
        chunk_objects.append(chunk_object)

        chunk_index += 1
        chunk_buffer = BytesIO()
        chunk_size = 0

    try:
        uploaded_file.file.seek(0)

        while True:
            line = uploaded_file.file.readline()

            if not line:
                break

            line_size = len(line)

            if chunk_size > 0 and chunk_size + line_size > MAP_CHUNK_SIZE_BYTES:
                flush_chunk()

            chunk_buffer.write(line)
            chunk_size += line_size

        if chunk_size > 0:
            flush_chunk()

        if not chunk_paths:
            flush_chunk()

        uploaded_file.file.seek(0)

        return chunk_paths, chunk_objects
    except HTTPException:
        cleanup_minio_objects(chunk_objects)
        uploaded_file.file.seek(0)
        raise
    except Exception:
        cleanup_minio_objects(chunk_objects)
        uploaded_file.file.seek(0)
        raise


def calculate_reducer_count(num_mappers: int):
    """
    Choose the number of reduce tasks from the number of map tasks.

    The policy is intentionally simple:
    - more map chunks create more reducers,
    - reducers are bounded by MIN_NUM_REDUCERS and MAX_NUM_REDUCERS,
    - MAP_TASKS_PER_REDUCER controls how aggressively reducers scale up.
    """

    mapper_count = max(1, num_mappers)
    reducer_count = (
        mapper_count + MAP_TASKS_PER_REDUCER - 1
    ) // MAP_TASKS_PER_REDUCER

    reducer_count = max(MIN_NUM_REDUCERS, reducer_count)

    return min(MAX_NUM_REDUCERS, reducer_count)


def parse_map_output_pairs(map_output_bytes: bytes, source_path: str):
    """
    Parse mapper output JSON Lines into key/value pairs.

    Expected line format:
    ["key", value]
    """

    output_text = map_output_bytes.decode("utf-8")
    pairs = []

    for line_number, line in enumerate(output_text.splitlines(), start=1):
        if not line.strip():
            continue

        try:
            pair = json.loads(line)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Invalid JSON in mapper output {source_path} "
                    f"at line {line_number}"
                )
            )

        if not isinstance(pair, list) or len(pair) != 2:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Invalid key/value pair in mapper output {source_path} "
                    f"at line {line_number}"
                )
            )

        pairs.append((pair[0], pair[1]))

    return pairs


def get_reducer_index(key, num_reducers: int):
    """
    Pick the reducer partition for a key using a stable hash.
    """

    encoded_key = json.dumps(key, sort_keys=True).encode("utf-8")
    key_hash = hashlib.sha256(encoded_key).hexdigest()

    return int(key_hash, 16) % num_reducers


def encode_reducer_input(groups):
    """
    Encode reducer input as JSON Lines.

    Each line has the form:
    [key, [values...]]
    """

    lines = [
        json.dumps([key, values])
        for key, values in sorted(
            groups.items(),
            key=lambda item: json.dumps(item[0], sort_keys=True)
        )
    ]

    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def shuffle_map_outputs_to_reducer_inputs(job: Job, map_tasks):
    """
    Read completed mapper outputs and create reducer input objects in MinIO.
    """

    reducer_groups = [
        {}
        for _ in range(job.num_reducers)
    ]

    for task in sorted(map_tasks, key=lambda item: item.task_index):
        if not task.output_path:
            raise HTTPException(
                status_code=500,
                detail=f"Map task {task.task_id} has no output path"
            )

        map_output_bytes = download_minio_object(task.output_path)
        pairs = parse_map_output_pairs(map_output_bytes, task.output_path)

        for key, value in pairs:
            reducer_index = get_reducer_index(key, job.num_reducers)
            reducer_groups[reducer_index].setdefault(key, []).append(value)

    reducer_input_paths = []

    for reducer_index, groups in enumerate(reducer_groups):
        object_name = (
            f"jobs/{job.job_id}/reducer-input/"
            f"reduce-{reducer_index:05d}.jsonl"
        )
        reducer_input_path, _ = upload_bytes_object(
            object_name,
            encode_reducer_input(groups),
            "application/jsonl"
        )
        reducer_input_paths.append(reducer_input_path)

    return reducer_input_paths


def serialize_task(task: Task):
    """
    Convert a Task database row into a JSON-friendly dictionary.
    """

    return {
        "task_id": task.task_id,
        "job_id": task.job_id,
        "task_type": task.task_type,
        "task_index": task.task_index,
        "input_path": task.input_path,
        "output_path": task.output_path,
        "status": task.status,
        "attempt_count": task.attempt_count,
        "max_retries": task.max_retries,
        "kubernetes_job_name": task.kubernetes_job_name,
        "error_message": task.error_message,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "started_at": task.started_at,
        "completed_at": task.completed_at
    }


def serialize_attempt(attempt: TaskAttempt):
    """
    Convert a TaskAttempt database row into a JSON-friendly dictionary.
    """

    return {
        "attempt_id": attempt.attempt_id,
        "task_id": attempt.task_id,
        "attempt_number": attempt.attempt_number,
        "status": attempt.status,
        "kubernetes_pod_name": attempt.kubernetes_pod_name,
        "error_message": attempt.error_message,
        "started_at": attempt.started_at,
        "completed_at": attempt.completed_at
    }


def summarize_tasks(tasks):
    """
    Count tasks by lifecycle status.
    """

    summary = {
        "total": len(tasks),
        "pending": 0,
        "running": 0,
        "completed": 0,
        "failed": 0
    }

    for task in tasks:
        if task.status not in summary:
            summary[task.status] = 0
        summary[task.status] += 1

    return summary


def serialize_job(job: Job, include_tasks: bool = False):
    """
    Convert a Job database row into a JSON-friendly dictionary.
    """

    tasks = sorted(
        job.tasks,
        key=lambda task: (task.task_type, task.task_index)
    )

    result = {
        "job_id": job.job_id,
        "username": job.username,
        "input_file": job.input_file,
        "mapper_file": job.mapper_file,
        "reducer_file": job.reducer_file,
        "num_mappers": job.num_mappers,
        "num_reducers": job.num_reducers,
        "manager_id": job.manager_id,
        "status": job.status,
        "output_path": job.output_path,
        "result": job.result,
        "error_message": job.error_message,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "completed_at": job.completed_at,
        "task_progress": summarize_tasks(tasks)
    }

    if include_tasks:
        result["tasks"] = [serialize_task(task) for task in tasks]

    return result


def build_map_task(job: Job, task_index: int, input_path: str):
    """
    Create one pending map task for one real input chunk.
    """

    return Task(
        job_id=job.job_id,
        task_type="map",
        task_index=task_index,
        input_path=input_path,
        output_path=minio_uri(
            f"jobs/{job.job_id}/intermediate/map-{task_index:05d}.json"
        ),
        status="pending"
    )


def build_reduce_task(job: Job, task_index: int, input_path: str):
    """
    Create one pending reduce task for a job whose map phase has completed.

    The input path is currently a logical shuffle partition. Later, workers
    should write actual partitioned intermediate data to MinIO.
    """

    return Task(
        job_id=job.job_id,
        task_type="reduce",
        task_index=task_index,
        input_path=input_path,
        output_path=minio_uri(
            f"jobs/{job.job_id}/output/reduce-{task_index:05d}.json"
        ),
        status="pending"
    )


def sanitize_kubernetes_name(value: str):
    """
    Convert a value into a Kubernetes-safe object name.
    """

    sanitized = re.sub(r"[^a-z0-9-]", "-", value.lower())
    sanitized = sanitized.strip("-")

    return sanitized or "task"


def build_kubernetes_job_name(task: Task):
    """
    Build a stable Kubernetes Job name for one task attempt.
    """

    next_attempt_number = task.attempt_count + 1
    raw_name = (
        f"mr-{task.task_type}-{task.job_id}-"
        f"{task.task_index:05d}-attempt-{next_attempt_number:03d}"
    )

    return sanitize_kubernetes_name(raw_name)[:63].rstrip("-")


def get_kubernetes_batch_api():
    """
    Return a Kubernetes BatchV1Api client.

    In a cluster, the Manager should use the pod service account. During local
    Minikube development, it can fall back to the user's kubeconfig.
    """

    global kubernetes_batch_api

    if kubernetes_batch_api is not None:
        return kubernetes_batch_api

    if kubernetes_client is None or kubernetes_config is None:
        raise HTTPException(
            status_code=503,
            detail="Kubernetes client library is not installed"
        )

    try:
        kubernetes_config.load_incluster_config()
    except Exception:
        try:
            kubernetes_config.load_kube_config()
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="Kubernetes configuration is unavailable"
            )

    kubernetes_batch_api = kubernetes_client.BatchV1Api()

    return kubernetes_batch_api


def build_worker_env(job: Job, task: Task):
    """
    Build environment variables passed to one worker pod.
    """

    user_code_path = job.mapper_file

    if task.task_type == "reduce":
        user_code_path = job.reducer_file

    env_values = {
        "TASK_TYPE": task.task_type,
        "JOB_ID": str(job.job_id),
        "TASK_ID": str(task.task_id),
        "TASK_INDEX": str(task.task_index),
        "INPUT_PATH": task.input_path,
        "USER_CODE_PATH": user_code_path,
        "OUTPUT_PATH": task.output_path or "",
        "MINIO_ENDPOINT": MINIO_ENDPOINT,
        "MINIO_BUCKET": MINIO_BUCKET,
        "MINIO_ACCESS_KEY": MINIO_ACCESS_KEY,
        "MINIO_SECRET_KEY": MINIO_SECRET_KEY,
        "MINIO_SECURE": str(MINIO_SECURE).lower(),
        "MANAGER_URL": MANAGER_INTERNAL_URL,
        "WORKER_SERVICE_TOKEN": WORKER_SERVICE_TOKEN
    }

    return [
        kubernetes_client.V1EnvVar(name=name, value=value)
        for name, value in env_values.items()
    ]


def create_kubernetes_job_for_task(job: Job, task: Task):
    """
    Create one Kubernetes Job that runs one map/reduce task.
    """

    batch_api = get_kubernetes_batch_api()
    kubernetes_job_name = build_kubernetes_job_name(task)

    labels = {
        "app": "mapreduce-worker",
        "mapreduce-job-id": str(job.job_id),
        "mapreduce-task-id": str(task.task_id),
        "mapreduce-task-type": task.task_type
    }

    container = kubernetes_client.V1Container(
        name="worker",
        image=WORKER_IMAGE,
        image_pull_policy=WORKER_IMAGE_PULL_POLICY,
        env=build_worker_env(job, task)
    )

    pod_template = kubernetes_client.V1PodTemplateSpec(
        metadata=kubernetes_client.V1ObjectMeta(labels=labels),
        spec=kubernetes_client.V1PodSpec(
            restart_policy="Never",
            containers=[container]
        )
    )

    kubernetes_job = kubernetes_client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=kubernetes_client.V1ObjectMeta(
            name=kubernetes_job_name,
            namespace=KUBERNETES_NAMESPACE,
            labels=labels
        ),
        spec=kubernetes_client.V1JobSpec(
            template=pod_template,
            backoff_limit=0,
            ttl_seconds_after_finished=KUBERNETES_JOB_TTL_SECONDS
        )
    )

    try:
        batch_api.create_namespaced_job(
            namespace=KUBERNETES_NAMESPACE,
            body=kubernetes_job
        )
    except ApiException as exc:
        if getattr(exc, "status", None) != 409:
            raise HTTPException(
                status_code=503,
                detail=f"Could not create Kubernetes Job for task {task.task_id}"
            )
    except Exception:
        raise HTTPException(
            status_code=503,
            detail=f"Could not create Kubernetes Job for task {task.task_id}"
        )

    task.kubernetes_job_name = kubernetes_job_name

    return kubernetes_job_name


def cleanup_kubernetes_jobs(kubernetes_job_names):
    """
    Best-effort cleanup for worker Jobs when submission fails.
    """

    if not kubernetes_job_names:
        return

    try:
        batch_api = get_kubernetes_batch_api()
    except HTTPException:
        return

    for kubernetes_job_name in kubernetes_job_names:
        try:
            batch_api.delete_namespaced_job(
                name=kubernetes_job_name,
                namespace=KUBERNETES_NAMESPACE,
                propagation_policy="Background"
            )
        except Exception:
            pass


def schedule_tasks_if_enabled(job: Job, tasks, scheduled_job_names=None):
    """
    Create Kubernetes Jobs for tasks when scheduling is enabled.
    """

    if scheduled_job_names is None:
        scheduled_job_names = []

    if not KUBERNETES_SCHEDULING_ENABLED:
        return scheduled_job_names

    for task in tasks:
        scheduled_job_name = create_kubernetes_job_for_task(job, task)
        scheduled_job_names.append(scheduled_job_name)

    return scheduled_job_names


def get_pending_unscheduled_tasks(db: Session, job_id: int, task_type: str):
    """
    Return pending tasks of a given type that do not have a Kubernetes Job yet.
    """

    return db.query(Task).filter(
        Task.job_id == job_id,
        Task.task_type == task_type,
        Task.status == "pending",
        Task.kubernetes_job_name.is_(None)
    ).order_by(
        Task.task_index
    ).all()


def get_latest_attempt(db: Session, task: Task):
    """
    Return the most recent attempt for a task, if one exists.
    """

    return db.query(TaskAttempt).filter(
        TaskAttempt.task_id == task.task_id
    ).order_by(
        TaskAttempt.attempt_number.desc()
    ).first()


def create_reduce_tasks_if_ready(db: Session, job: Job):
    """
    Create reduce tasks once all map tasks have completed.
    """

    tasks = db.query(Task).filter(Task.job_id == job.job_id).all()
    map_tasks = [task for task in tasks if task.task_type == "map"]
    reduce_tasks = [task for task in tasks if task.task_type == "reduce"]

    if not map_tasks:
        return []

    maps_completed = all(task.status == "completed" for task in map_tasks)

    if not maps_completed or reduce_tasks:
        return []

    reducer_input_paths = shuffle_map_outputs_to_reducer_inputs(job, map_tasks)

    new_reduce_tasks = [
        build_reduce_task(job, task_index, input_path)
        for task_index, input_path in enumerate(reducer_input_paths)
    ]

    db.add_all(new_reduce_tasks)
    db.flush()

    return new_reduce_tasks


def recalculate_job_status(db: Session, job: Job):
    """
    Update the parent job status from the current task states.
    """

    create_reduce_tasks_if_ready(db, job)

    tasks = db.query(Task).filter(Task.job_id == job.job_id).all()

    if not tasks:
        job.status = "pending"
        return

    exhausted_task = next(
        (
            task
            for task in tasks
            if task.status == "failed" and task.attempt_count > task.max_retries
        ),
        None
    )

    if exhausted_task:
        job.status = "failed"
        job.error_message = (
            f"Task {exhausted_task.task_id} failed after "
            f"{exhausted_task.attempt_count} attempts"
        )
        return

    reduce_tasks = [task for task in tasks if task.task_type == "reduce"]

    if reduce_tasks and all(task.status == "completed" for task in reduce_tasks):
        job.status = "completed"
        job.output_path = f"jobs/{job.job_id}/output"
        job.completed_at = now_utc()
        return

    if any(task.status in ["running", "completed"] for task in tasks):
        job.status = "running"
        return

    job.status = "pending"


@app.get("/")
def root():
    """
    Basic health/info endpoint.
    """

    return {
        "service": "Manager Service",
        "status": "running"
    }


@app.post("/jobs")
def submit_job(
    input_file: UploadFile = File(...),
    mapper_file: UploadFile = File(...),
    reducer_file: UploadFile = File(...),
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    Submit a new MapReduce job.

    The Manager validates the user's token, uploads submitted files to
    MinIO, stores the MinIO paths in DDS, and creates the initial map task
    metadata.
    """

    user_info = validate_token(credentials)
    input_size_bytes = get_uploaded_file_size(input_file)

    uploaded_objects = []
    scheduled_kubernetes_jobs = []
    map_tasks = []

    new_job = Job(
        username=user_info["username"],
        manager_id=MANAGER_ID,
        input_file="pending-upload",
        mapper_file="pending-upload",
        reducer_file="pending-upload",
        num_mappers=1,
        num_reducers=MIN_NUM_REDUCERS,
        status="pending",
        result=None
    )

    try:
        db.add(new_job)

        # Flush sends the INSERT to PostgreSQL without committing yet.
        # We need this so PostgreSQL assigns new_job.job_id before we upload
        # files and create child task rows that reference it.
        db.flush()

        ensure_minio_bucket()

        input_path, input_object = upload_job_file(
            new_job.job_id,
            input_file,
            "input"
        )
        uploaded_objects.append(input_object)

        mapper_path, mapper_object = upload_job_file(
            new_job.job_id,
            mapper_file,
            "mapper"
        )
        uploaded_objects.append(mapper_object)

        reducer_path, reducer_object = upload_job_file(
            new_job.job_id,
            reducer_file,
            "reducer"
        )
        uploaded_objects.append(reducer_object)

        new_job.input_file = input_path
        new_job.mapper_file = mapper_path
        new_job.reducer_file = reducer_path

        input_chunk_paths, input_chunk_objects = split_input_file_to_minio_chunks(
            new_job.job_id,
            input_file
        )
        uploaded_objects.extend(input_chunk_objects)

        new_job.num_mappers = len(input_chunk_paths)
        new_job.num_reducers = calculate_reducer_count(new_job.num_mappers)

        map_tasks = [
            build_map_task(new_job, task_index, chunk_path)
            for task_index, chunk_path in enumerate(input_chunk_paths)
        ]

        db.add_all(map_tasks)
        db.flush()

        scheduled_kubernetes_jobs = schedule_tasks_if_enabled(
            new_job,
            map_tasks,
            scheduled_kubernetes_jobs
        )

        db.commit()
        db.refresh(new_job)
    except HTTPException:
        db.rollback()
        cleanup_kubernetes_jobs(scheduled_kubernetes_jobs)
        cleanup_minio_objects(uploaded_objects)
        raise
    except Exception:
        db.rollback()
        cleanup_kubernetes_jobs(scheduled_kubernetes_jobs)
        cleanup_minio_objects(uploaded_objects)
        raise

    return {
        "success": True,
        "job_id": new_job.job_id,
        "status": new_job.status,
        "input_size_bytes": input_size_bytes,
        "map_chunk_size_bytes": MAP_CHUNK_SIZE_BYTES,
        "map_tasks_per_reducer": MAP_TASKS_PER_REDUCER,
        "min_num_reducers": MIN_NUM_REDUCERS,
        "max_num_reducers": MAX_NUM_REDUCERS,
        "num_mappers": new_job.num_mappers,
        "num_reducers": new_job.num_reducers,
        "map_tasks_created": len(map_tasks),
        "kubernetes_scheduling_enabled": KUBERNETES_SCHEDULING_ENABLED,
        "kubernetes_jobs_created": scheduled_kubernetes_jobs,
        "task_progress": summarize_tasks(map_tasks)
    }


@app.get("/jobs")
def list_jobs(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    List jobs visible to the current user.

    Admin users can see all jobs.
    Normal users can see only their own jobs.
    """

    user_info = validate_token(credentials)

    if user_info["role"] == "admin":
        db_jobs = db.query(Job).all()
    else:
        db_jobs = db.query(Job).filter(
            Job.username == user_info["username"]
        ).all()

    result = []

    for job in db_jobs:
        result.append(serialize_job(job))

    return result


@app.get("/jobs/{job_id}")
def view_job(
    job_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    View metadata for a specific job.

    Admin users can view any job.
    Normal users can only view jobs they own.
    """

    user_info = validate_token(credentials)

    job = db.query(Job).filter(Job.job_id == job_id).first()

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    if user_info["role"] != "admin" and job.username != user_info["username"]:
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to view this job"
        )

    return serialize_job(job, include_tasks=True)


@app.get("/jobs/{job_id}/tasks")
def list_job_tasks(
    job_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    List all map/reduce tasks for a specific job.

    This is useful for checking progress and debugging retries.
    """

    user_info = validate_token(credentials)

    job = db.query(Job).filter(Job.job_id == job_id).first()

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    if user_info["role"] != "admin" and job.username != user_info["username"]:
        raise HTTPException(
            status_code=403,
            detail="You are not allowed to view this job's tasks"
        )

    tasks = db.query(Task).filter(
        Task.job_id == job_id
    ).order_by(
        Task.task_type,
        Task.task_index
    ).all()

    return {
        "job_id": job_id,
        "task_progress": summarize_tasks(tasks),
        "tasks": [serialize_task(task) for task in tasks]
    }


@app.get("/jobs/{job_id}/tasks/{task_id}/attempts")
def list_task_attempts(
    job_id: int,
    task_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    List all execution attempts for a specific task.
    """

    user_info = validate_token(credentials)
    job = get_job_or_404(db, job_id)
    ensure_job_access(user_info, job, "view")

    get_task_or_404(db, job_id, task_id)

    attempts = db.query(TaskAttempt).filter(
        TaskAttempt.task_id == task_id
    ).order_by(
        TaskAttempt.attempt_number
    ).all()

    return {
        "job_id": job_id,
        "task_id": task_id,
        "attempts": [serialize_attempt(attempt) for attempt in attempts]
    }


@app.post("/jobs/{job_id}/tasks/{task_id}/start")
def start_task(
    job_id: int,
    task_id: int,
    request: StartTaskRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    Mark a pending task as running and create a new attempt record.

    This is a temporary lifecycle endpoint for local testing. Later, this
    transition should be driven by the Manager/Kubernetes scheduling loop.
    """

    user_info = validate_token(credentials)
    require_admin(user_info)

    job = get_job_or_404(db, job_id)
    task = get_task_or_404(db, job_id, task_id)

    if job.status in ["completed", "failed"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot start a task for a {job.status} job"
        )

    if task.status != "pending":
        raise HTTPException(
            status_code=400,
            detail=f"Only pending tasks can be started. Current status: {task.status}"
        )

    task.attempt_count += 1
    task.status = "running"
    task.started_at = now_utc()
    task.completed_at = None
    task.error_message = None

    if request.kubernetes_job_name:
        task.kubernetes_job_name = request.kubernetes_job_name

    attempt = TaskAttempt(
        task_id=task.task_id,
        attempt_number=task.attempt_count,
        status="running",
        kubernetes_pod_name=request.kubernetes_pod_name
    )

    db.add(attempt)
    recalculate_job_status(db, job)

    db.commit()
    db.refresh(job)
    db.refresh(task)
    db.refresh(attempt)

    return {
        "success": True,
        "job": serialize_job(job),
        "task": serialize_task(task),
        "attempt": serialize_attempt(attempt)
    }


@app.post("/jobs/{job_id}/tasks/{task_id}/complete")
def complete_task(
    job_id: int,
    task_id: int,
    request: CompleteTaskRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    Mark a running task as completed.

    Completing all map tasks automatically creates the reduce tasks.
    Completing all reduce tasks automatically marks the job as completed.
    """

    user_info = validate_token(credentials)
    require_admin(user_info)

    job = get_job_or_404(db, job_id)
    task = get_task_or_404(db, job_id, task_id)

    if job.status in ["completed", "failed"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot complete a task for a {job.status} job"
        )

    if task.status != "running":
        raise HTTPException(
            status_code=400,
            detail=f"Only running tasks can be completed. Current status: {task.status}"
        )

    scheduled_kubernetes_jobs = []
    attempt = None

    try:
        task.status = "completed"
        task.completed_at = now_utc()

        if request.output_path:
            task.output_path = request.output_path

        attempt = get_latest_attempt(db, task)

        if attempt:
            attempt.status = "completed"
            attempt.completed_at = now_utc()

        recalculate_job_status(db, job)

        reduce_tasks_to_schedule = get_pending_unscheduled_tasks(
            db,
            job.job_id,
            "reduce"
        )
        scheduled_kubernetes_jobs = schedule_tasks_if_enabled(
            job,
            reduce_tasks_to_schedule,
            scheduled_kubernetes_jobs
        )

        db.commit()
    except HTTPException:
        db.rollback()
        cleanup_kubernetes_jobs(scheduled_kubernetes_jobs)
        raise
    except Exception:
        db.rollback()
        cleanup_kubernetes_jobs(scheduled_kubernetes_jobs)
        raise

    db.refresh(job)
    db.refresh(task)

    response = {
        "success": True,
        "kubernetes_jobs_created": scheduled_kubernetes_jobs,
        "job": serialize_job(job),
        "task": serialize_task(task)
    }

    if attempt:
        db.refresh(attempt)
        response["attempt"] = serialize_attempt(attempt)

    return response


@app.post("/jobs/{job_id}/tasks/{task_id}/fail")
def fail_task(
    job_id: int,
    task_id: int,
    request: FailTaskRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    Record a failed task attempt.

    If retries are still available, the task goes back to pending so it can
    be started again. If retries are exhausted, the task and job fail.
    """

    user_info = validate_token(credentials)
    require_admin(user_info)

    job = get_job_or_404(db, job_id)
    task = get_task_or_404(db, job_id, task_id)

    if job.status in ["completed", "failed"]:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot fail a task for a {job.status} job"
        )

    if task.status not in ["pending", "running"]:
        raise HTTPException(
            status_code=400,
            detail=f"Only pending or running tasks can fail. Current status: {task.status}"
        )

    scheduled_kubernetes_jobs = []

    if task.status == "pending":
        task.attempt_count += 1

    attempt = get_latest_attempt(db, task)

    if not attempt or attempt.attempt_number != task.attempt_count:
        attempt = TaskAttempt(
            task_id=task.task_id,
            attempt_number=task.attempt_count,
            status="failed"
        )
        db.add(attempt)
    else:
        attempt.status = "failed"

    attempt.error_message = request.error_message
    attempt.completed_at = now_utc()

    task.error_message = request.error_message

    # max_retries means retries after the first attempt. So with
    # max_retries=3, attempts 1, 2, 3 can fail and be retried; attempt 4
    # is the final allowed attempt.
    if task.attempt_count <= task.max_retries:
        task.status = "pending"
        task.started_at = None
        task.completed_at = None
        task.kubernetes_job_name = None
        will_retry = True
    else:
        task.status = "failed"
        task.completed_at = now_utc()
        will_retry = False

    try:
        recalculate_job_status(db, job)

        if will_retry:
            scheduled_kubernetes_jobs = schedule_tasks_if_enabled(
                job,
                [task],
                scheduled_kubernetes_jobs
            )

        db.commit()
    except HTTPException:
        db.rollback()
        cleanup_kubernetes_jobs(scheduled_kubernetes_jobs)
        raise
    except Exception:
        db.rollback()
        cleanup_kubernetes_jobs(scheduled_kubernetes_jobs)
        raise

    db.refresh(job)
    db.refresh(task)
    db.refresh(attempt)

    return {
        "success": True,
        "will_retry": will_retry,
        "kubernetes_jobs_created": scheduled_kubernetes_jobs,
        "job": serialize_job(job),
        "task": serialize_task(task),
        "attempt": serialize_attempt(attempt)
    }


@app.get("/jobs/{job_id}/result")
def retrieve_job_result(
    job_id: int,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    Retrieve the result of a completed job.

    The Manager validates the token, checks job ownership, checks that the
    job is completed, and then returns final output objects from MinIO.
    """

    user_info = validate_token(credentials)
    job = get_job_or_404(db, job_id)
    ensure_job_access(user_info, job, "retrieve")

    if job.status != "completed":
        raise HTTPException(
            status_code=400,
            detail="Result is not available yet"
        )

    output_prefix = get_job_output_prefix(job)
    result_objects = list_minio_objects(output_prefix)

    if not result_objects:
        raise HTTPException(
            status_code=404,
            detail="Result objects were not found in MinIO"
        )

    return {
        "job_id": job.job_id,
        "status": job.status,
        "output_prefix": output_prefix,
        "output_path": job.output_path,
        "result_object_count": len(result_objects),
        "result_objects": [
            serialize_minio_object(minio_object)
            for minio_object in sorted(
                result_objects,
                key=lambda item: item.object_name
            )
        ]
    }


@app.post("/jobs/{job_id}/complete")
def complete_job(
    job_id: int,
    request: CompleteJobRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    """
    Temporarily mark a job as completed.

    This endpoint is only for local testing before real worker execution
    exists. Only admins are allowed to manually complete jobs.
    """

    user_info = validate_token(credentials)

    if user_info["role"] != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only admins can manually complete jobs"
        )

    job = db.query(Job).filter(Job.job_id == job_id).first()

    if not job:
        raise HTTPException(
            status_code=404,
            detail="Job not found"
        )

    job.status = "completed"
    job.result = request.result
    job.completed_at = now_utc()

    db.commit()
    db.refresh(job)

    return {
        "success": True,
        "job_id": job.job_id,
        "status": job.status,
        "result": job.result
    }
