from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Job(Base):
    """
    Database model for MapReduce jobs.

    Each row represents one submitted job. The username field stores
    the owner of the job, as returned by the Authentication Service
    after token validation.
    """

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="check_job_status"
        ),
        CheckConstraint(
            "num_mappers >= 1",
            name="check_job_num_mappers_positive"
        ),
        CheckConstraint(
            "num_reducers >= 1",
            name="check_job_num_reducers_positive"
        ),
    )

    # Unique job identifier.
    job_id = Column(Integer, primary_key=True, index=True)

    # Username of the user who submitted the job.
    # This is used for job ownership checks.
    username = Column(String, nullable=False)

    # File names or paths for the submitted job.
    # For now these are simple strings. Later, they can become MinIO paths.
    input_file = Column(String, nullable=False)
    mapper_file = Column(String, nullable=False)
    reducer_file = Column(String, nullable=False)

    # Requested parallelism.
    num_mappers = Column(Integer, nullable=False, default=1)
    num_reducers = Column(Integer, nullable=False, default=1)

    # Manager replica responsible for this job.
    # This is useful later when the Manager runs as a Kubernetes StatefulSet.
    manager_id = Column(String, nullable=True)

    # Job status.
    # For now we use a string for simplicity.
    # Expected values: pending, running, completed, failed.
    status = Column(String, nullable=False, default="pending")

    # Final output location. Later this should point to a MinIO object/prefix.
    output_path = Column(String, nullable=True)

    # Temporary result field for local testing.
    # Later, this should probably become an output_path pointing to MinIO.
    result = Column(String, nullable=True)

    # Error message explaining why a job failed, when status = failed.
    error_message = Column(Text, nullable=True)

    # Timestamp automatically created by PostgreSQL.
    created_at = Column(DateTime, server_default=func.now())

    # Timestamp updated whenever SQLAlchemy modifies the row.
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Timestamp set when the job completes.
    completed_at = Column(DateTime, nullable=True)

    tasks = relationship(
        "Task",
        back_populates="job",
        cascade="all, delete-orphan"
    )


class Task(Base):
    """
    Database model for one map or reduce task inside a MapReduce job.

    The Manager creates task rows when it decomposes a submitted job.
    Workers/Kubernetes execution will later update these rows as tasks
    move through pending, running, completed, and failed states.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "task_type IN ('map', 'reduce')",
            name="check_task_type"
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="check_task_status"
        ),
        CheckConstraint(
            "task_index >= 0",
            name="check_task_index_non_negative"
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="check_task_attempt_count_non_negative"
        ),
        CheckConstraint(
            "max_retries >= 0",
            name="check_task_max_retries_non_negative"
        ),
        UniqueConstraint(
            "job_id",
            "task_type",
            "task_index",
            name="unique_task_per_job_phase_index"
        ),
    )

    # Unique task identifier.
    task_id = Column(Integer, primary_key=True, index=True)

    # Parent MapReduce job.
    job_id = Column(
        Integer,
        ForeignKey("jobs.job_id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Expected values: map, reduce.
    task_type = Column(String, nullable=False)

    # Position of this task within its phase.
    # Example: map task 0 of 4, reduce task 2 of 3.
    task_index = Column(Integer, nullable=False)

    # Input and output locations for this task.
    # Later these should be MinIO object paths/prefixes.
    input_path = Column(String, nullable=False)
    output_path = Column(String, nullable=True)

    # Expected values: pending, running, completed, failed.
    status = Column(String, nullable=False, default="pending")

    # Retry tracking.
    attempt_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)

    # Kubernetes Job name used to execute this task.
    kubernetes_job_name = Column(String, nullable=True)

    # Error message from the latest failed attempt, if any.
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    job = relationship("Job", back_populates="tasks")

    attempts = relationship(
        "TaskAttempt",
        back_populates="task",
        cascade="all, delete-orphan"
    )


class TaskAttempt(Base):
    """
    Database model for a single execution attempt of a task.

    A task can have multiple attempts when workers fail or Kubernetes
    Jobs need to be retried. Keeping attempts separate makes failures
    easier to explain during testing and the final presentation.
    """

    __tablename__ = "task_attempts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name="check_task_attempt_status"
        ),
        CheckConstraint(
            "attempt_number >= 1",
            name="check_task_attempt_number_positive"
        ),
        UniqueConstraint(
            "task_id",
            "attempt_number",
            name="unique_attempt_number_per_task"
        ),
    )

    attempt_id = Column(Integer, primary_key=True, index=True)

    task_id = Column(
        Integer,
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    attempt_number = Column(Integer, nullable=False)

    # Expected values: running, completed, failed.
    status = Column(String, nullable=False, default="running")

    kubernetes_pod_name = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)

    started_at = Column(DateTime, server_default=func.now())
    completed_at = Column(DateTime, nullable=True)

    task = relationship("Task", back_populates="attempts")
