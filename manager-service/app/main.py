from datetime import datetime
import os

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel
from sqlalchemy.orm import Session
import requests

from app.database import engine, get_db
from app.models import Base, Job, Task, TaskAttempt

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


def build_map_task(job: Job, task_index: int):
    """
    Create one pending map task for a submitted job.

    For now, the input path is a logical partition name. Later, when MinIO
    splitting exists, this should become the real MinIO object path for
    that partition.
    """

    return Task(
        job_id=job.job_id,
        task_type="map",
        task_index=task_index,
        input_path=f"{job.input_file}#partition-{task_index}",
        output_path=f"jobs/{job.job_id}/intermediate/map-{task_index}",
        status="pending"
    )


def build_reduce_task(job: Job, task_index: int):
    """
    Create one pending reduce task for a job whose map phase has completed.

    The input path is currently a logical shuffle partition. Later, workers
    should write actual partitioned intermediate data to MinIO.
    """

    return Task(
        job_id=job.job_id,
        task_type="reduce",
        task_index=task_index,
        input_path=f"jobs/{job.job_id}/intermediate/reduce-{task_index}",
        output_path=f"jobs/{job.job_id}/output/reduce-{task_index}",
        status="pending"
    )


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

    new_reduce_tasks = [
        build_reduce_task(job, task_index)
        for task_index in range(job.num_reducers)
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
    num_mappers: int = Form(1, ge=1),
    num_reducers: int = Form(1, ge=1),
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
    uploaded_objects = []
    map_tasks = []

    new_job = Job(
        username=user_info["username"],
        manager_id=MANAGER_ID,
        input_file="pending-upload",
        mapper_file="pending-upload",
        reducer_file="pending-upload",
        num_mappers=num_mappers,
        num_reducers=num_reducers,
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

        map_tasks = [
            build_map_task(new_job, task_index)
            for task_index in range(num_mappers)
        ]

        db.add_all(map_tasks)
        db.commit()
        db.refresh(new_job)
    except HTTPException:
        db.rollback()
        cleanup_minio_objects(uploaded_objects)
        raise
    except Exception:
        db.rollback()
        cleanup_minio_objects(uploaded_objects)
        raise

    return {
        "success": True,
        "job_id": new_job.job_id,
        "status": new_job.status,
        "map_tasks_created": len(map_tasks),
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
    task.kubernetes_job_name = request.kubernetes_job_name
    task.error_message = None

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

    task.status = "completed"
    task.completed_at = now_utc()

    if request.output_path:
        task.output_path = request.output_path

    attempt = get_latest_attempt(db, task)

    if attempt:
        attempt.status = "completed"
        attempt.completed_at = now_utc()

    recalculate_job_status(db, job)

    db.commit()
    db.refresh(job)
    db.refresh(task)

    response = {
        "success": True,
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
        will_retry = True
    else:
        task.status = "failed"
        task.completed_at = now_utc()
        will_retry = False

    recalculate_job_status(db, job)

    db.commit()
    db.refresh(job)
    db.refresh(task)
    db.refresh(attempt)

    return {
        "success": True,
        "will_retry": will_retry,
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
    job is completed, and then returns the result.
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
            detail="You are not allowed to retrieve this job result"
        )

    if job.status != "completed":
        raise HTTPException(
            status_code=400,
            detail="Result is not available yet"
        )

    return {
        "job_id": job.job_id,
        "result": job.result,
        "output_path": job.output_path
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
